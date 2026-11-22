# -*- coding: utf-8 -*-
"""投机预生成引擎（硬性入围指标：端到端首字响应 P50 <= 2 秒）。

原理（不投机取巧、不播填充词）：
  火山流式 ASR 在用户**还在说话**时就持续返回 partial 文本，实测最后一个
  partial 往往已是完整语义（规范化后与 definite 一致）。利用这段「免费时间窗」
  在后台提前把本轮回复的 LLM 生成 + 首片段 TTS 全部跑完并缓存；等 definite
  到达、校验文本前缀一致后，直接下发缓存音频 —— 首帧不再等 LLM 与 TTS。

  · 首帧播的就是针对本轮问题的**真实回复**，不是通用承接语；
  · 命中时整轮复用预览结果，不重复调用大模型，文字与状态机完全一致；
  · 校验不通过（含转折词、语义明显变化）则丢弃缓存走原链路；
  · 覆盖率放宽到 0.35 是经确认的准确性取舍：部分命中轮次首帧可能答非所问，
    由后续句子补正，换取「停说 -> 首帧」P50 压到 2 秒内；
  · 全部计算在 executor 线程，绝不修改会话状态；状态提交仍由主协程独占。
"""

import re
import threading
import time
from typing import Callable, Optional

# 用户把话说完后又转折时，早期 partial 生成的回复不可复用
_TURN_WORDS = ("但是", "不过", "不是", "而是", "算了", "另外", "其实",
               "可是", "然而", "等等", "不对", "反过来", "相反")

# 【11/21 十轮·提速】6→2：短确认句（"是的""没有疑问""可以方便"）此前
# 因 <6 字被挡在投机门外 → 候选=0 → 说完才现场等 LLM 16s。降为 2 后，这类
# 短句在商家说话期间即可触发预生成；且它们的回复是本地固定话术（S1 开口即认 /
# 强证据确认句），preview_reply 已走本地快判不调 LLM，预生成本身很快、命中率高。
# 安全性：投机候选只用于 definite 命中后播报，喂错最多 miss，绝不上屏。
_MIN_PRED_CHARS = 2
# 实测：一次 LLM+TTS 预测约 2~3s，而「完整 partial -> final」窗口只有 1~1.5s。
# 覆盖率 0.5 时多数轮次只能等到基于半句话的短候选，命中率仅 1/3、P50 约 2.8s。
# 经用户确认放宽到 0.35：允许部分命中轮次的首帧回复基于半句话生成，
# 由后续句子补正；转折词检测与 >=6 字下限保留，避免完全离谱的复用。
_MIN_COVERAGE = 0.35       # 预测文本至少覆盖最终文本的 35%
_PREDICT_INTERVAL = 0.35   # 两次预测最小间隔（秒），文本显著增长时可绕过
_MAX_INFLIGHT = 3          # 常规并发上限；增长型预测允许 +2，避免 API 拥塞


def _norm(s: str) -> str:
    """规范化：只保留中英文与数字，消除标点 / ITN / 二遍识别带来的差异。"""
    return re.sub(r"[^一-鿿0-9a-zA-Z]", "", s or "")


class SpeculativeEngine:
    """每通电话一个实例；由 WebSocket 协程独占驱动。"""

    def __init__(self, agent, tts, loop, log: Optional[Callable[[str], None]] = None):
        self.agent = agent
        self.tts = tts
        self.loop = loop
        self.log = log or (lambda _m: None)
        self._candidates: list[dict] = []      # 已完成的预测
        self._lock = threading.Lock()
        self._last_text = ""
        self._last_start = 0.0
        self._inflight = 0
        self._inflight_lock = threading.Lock()
        self._closed = False
        self.stats = {"submitted": 0, "done": 0, "hit": 0, "miss": 0, "failed": 0}

    # ---------------- 输入：ASR 流式中间结果 ----------------
    def on_partial(self, text: str) -> None:
        """每次收到 partial 调用，节流后在后台启动一次预测。

        【抢占调度】ASR 文本每显著增长 >=4 字就绕过节流、并允许并发上限 +2
        立即提交一次预测，让「较完整文本」的预测尽早开跑；常规 partial 仍走
        0.35s 节流与并发上限 3，避免早期短文本占满槽位造成 API 拥塞。
        云端报错文本（[ASR...）不参与预测，避免浪费一次 LLM+TTS 调用。
        """
        if self._closed or text.startswith("[ASR"):
            return
        n = _norm(text)
        if len(n) < _MIN_PRED_CHARS:
            return
        now = time.monotonic()
        prev = _norm(self._last_text)
        if n == prev:
            return
        grown = len(n) - len(prev) >= 4
        if not grown and now - self._last_start < _PREDICT_INTERVAL:
            return
        with self._inflight_lock:
            cap = _MAX_INFLIGHT + (2 if grown else 0)
            if self._inflight >= cap:
                return
            self._inflight += 1
        self._last_text = text
        self._last_start = now
        self.stats["submitted"] += 1
        try:
            self.loop.run_in_executor(None, self._predict, text)
        except Exception:
            with self._inflight_lock:
                self._inflight -= 1

    # ---------------- 后台预测 ----------------
    def _predict(self, text: str) -> None:
        try:
            pv = self.agent.preview_reply(text)
            frags = pv.get("fragments") or []
            audio: list[bytes] = []
            if frags:
                self.tts.synthesize_stream(frags[0], lambda b: audio.append(b))
            if not audio:
                raise RuntimeError("首片段无音频")
            item = {"text": text, "norm": _norm(text), "pv": pv, "audio": audio}
            self.loop.call_soon_threadsafe(self._store, item)
        except Exception as e:
            self.log(f"SPEC-FAIL {type(e).__name__}: {str(e)[:100]}")
            self.loop.call_soon_threadsafe(self._fail)
        finally:
            with self._inflight_lock:
                self._inflight -= 1

    def _store(self, item: dict) -> None:
        if self._closed:
            return
        with self._lock:
            self._candidates.append(item)
            if len(self._candidates) > 8:
                self._candidates = self._candidates[-8:]
        self.stats["done"] += 1
        self.log(f"SPEC-READY 「{item['text'][:18]}」 音频分片={len(item['audio'])}")

    def _fail(self) -> None:
        self.stats["failed"] += 1

    # ---------------- 输出：definite 到达时取用 ----------------
    @staticmethod
    def _coverage(final_norm: str, pred_norm: str) -> float:
        """返回预测文本与最终文本的语义覆盖率（0 表示不可用）。"""
        if not pred_norm or not final_norm or len(pred_norm) < _MIN_PRED_CHARS:
            return 0.0
        if final_norm.startswith(pred_norm):
            cover = len(pred_norm) / len(final_norm)
        elif pred_norm.startswith(final_norm):
            cover = len(final_norm) / len(pred_norm)
        else:
            common = 0
            for a, b in zip(final_norm, pred_norm):
                if a != b:
                    break
                common += 1
            if common < _MIN_PRED_CHARS:
                return 0.0
            cover = common / max(len(final_norm), len(pred_norm))
        # 预测没覆盖到的后半句若出现转折，语义已变，不可复用
        tail = final_norm[len(pred_norm):] if final_norm.startswith(pred_norm) else ""
        if tail and any(w in tail for w in _TURN_WORDS):
            return 0.0
        return cover if cover >= _MIN_COVERAGE else 0.0

    def take(self, final_text: str) -> Optional[dict]:
        """校验并返回最匹配的已完成预测；命中后清空候选，避免跨轮误用。

        11/19 修复：只允许复用**大模型生成的答疑回复**（intent=NEED_CLARIFICATION）。
        规则判定的固定话术（闲聊拉回/身份确认/红线纠正/确认推进…）依赖**完整句子**
        ——用半句话判「闲聊」，等商家说完「…就去提交」整句后前缀照样命中，会把
        错误回复直接播出去（实测把「没啥疑问了，我现在就去提交」拖成闲聊循环）。
        这类轮次放弃预生成，走最终文本的正常路由，毫秒级损失只发生在少数纠正轮。
        """
        fn = _norm(final_text)
        best, best_score = None, 0.0
        with self._lock:
            cands = list(self._candidates)
        for c in cands:
            score = self._coverage(fn, c["norm"])
            if score > best_score:
                best, best_score = c, score
        if best is not None:
            pv_intent = str((best.get("pv") or {}).get("intent") or "")
            if pv_intent != "NEED_CLARIFICATION":
                self.stats["miss"] += 1
                self.clear()
                self.log(f"SPEC-DROP 「{final_text[:18]}」 intent={pv_intent} "
                         f"（规则判定回复禁止按半句复用）")
                return None
        if best is None:
            self.stats["miss"] += 1
            self.clear()
            self.log(f"SPEC-MISS 「{final_text[:18]}」 候选={len(cands)}")
            return None
        self.stats["hit"] += 1
        self.clear()
        self.log(f"SPEC-HIT 「{final_text[:18]}」 覆盖={best_score:.2f} "
                 f"预测词数={len(best['norm'])} 分片={len(best['audio'])}")
        return {"pv": best["pv"], "audio": best["audio"], "coverage": best_score}

    def clear(self) -> None:
        with self._lock:
            self._candidates.clear()
        self._last_text = ""
        self._last_start = 0.0

    def close(self) -> None:
        self._closed = True
        self.clear()
