# -*- coding: utf-8 -*-
"""火山引擎「豆包语音合成模型 2.0」(seed-tts-2.0) 客户端 —— 只负责「念稿的人」。

替换智谱 GLM-TTS 的原因：glm-tts 会在每段音频开头附加约 1.9s 的恒幅
正弦 AI 音频水印（用户听到的「滴滴/嘟嘟」）。火山 2.0 默认不加可听水印
（additions.aigc_watermark=false），实测首片音频 0.23~0.66s 到达，音色由
环境变量固定，所有文字统一同一音色。

【11/13 下午新基线 基线版(8).zip 兼容层】
下午基线 server.py 比上午版多约 978 行（QA 快路径 / 红线 / 风险 / 首包竞速 /
TTS 落盘缓存），对 TTSClient 的调用也多了 5 个接口。本文件全部补齐：
    __init__(voice=...)                 server.py:277 预热用独立实例
    load_disk_cache(texts) -> int       server.py:272 启动时灌入预热音频
    synthesize(text, timeout=(2,4))    server.py:2170 降级路径
    synthesize_stream(..., cancel=...) speculative.py:148 投机预生成取消
    synthesize_stream_raced(...)       server.py:2159 本轮首片段首包竞速
    warmup(texts=None, pause=None)     与 tts_zhipu 保持一致
**磁盘缓存用独立目录 data/tts_cache_volcano/**，绝不读取下午基线自带的
data/tts_cache/ 下 33 个智谱预合成 wav —— 那些文件开头带水印，读出来会
把「滴滴」重新播一遍。

官方文档：
  https://volcengine.com/docs/doubao-voice/http-chunked-sse-unidirectional-streaming-v3
  POST https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse
  X-Api-Key / X-Api-Resource-Id: seed-tts-2.0 / X-Api-Request-Id

真机实测（2026/11/13）踩过的两个坑，改动时请勿「顺手修正」：
  1) req_params.additions 必须是 **JSON 字符串**，传 object 会返回
     55000000 "cannot unmarshal object into Go struct field ... of type string"；
  2) 官方音色不要传 req_params.model，该字段仅供声音复刻(ICL)音色使用。

11/13 下午边界审查后修复的 5 个缺陷（tools/test_tts_edge.py 可复跑）：
  B1 requests.Session 官方非线程安全，而投机预生成最多 5 线程并发调用
     → 改为每线程独立 Session（同线程内仍复用 keep-alive）；
  B2 event:153 且 code=0 时旧版会静默把半句音频当成功返回 → 现统一标记失败；
  B3 已下发音频分片后才失败，旧版抛异常会让 server.py 降级重合成整句，
     用户会听到「半句 + 又一遍完整句子」→ 现改为记录 last_error 但不再抛；
  B4 单个坏 base64 分片会让整句崩 → 现跳过坏分片并计数；
  B5 长回复（实测 174 字 / 29s 音频）读超时 30s 偏紧 → 放宽到 60s。
"""

import base64
import binascii
import hashlib
import json
import os
import struct
import sys
import threading
import uuid
from typing import Callable, Optional

import requests

API_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 60            # B5：长回复流式返回可能超过 30s
OK_CODES = (0, 20000000, None)

# 音频格式固定：24kHz / 16bit / 单声道 PCM，与前端 audioQ 及 _wav_seconds() 兼容
SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2
BYTES_PER_SEC = SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS      # 48000
FIRST_FLUSH_BYTES = 1920     # 首片 40ms：既要抢首帧，又要避免过短 WAV 解码失败
FLUSH_BYTES = 9600           # 后续每片 200ms，减少浏览器逐帧解码次数

# 显式关闭可听水印 + 打开服务端同文本缓存（相同话术第二次直接返回）
ADDITIONS = json.dumps(
    {"aigc_watermark": False, "cache_config": {"text_type": 1, "use_cache": True}},
    ensure_ascii=False, separators=(",", ":"),
)

# 【水印隔离】必须用独立的落盘目录。下午基线自带 data/tts_cache/，里面是
# 智谱 GLM-TTS 预合成的 33 个 wav，开头均有约 1.9s 恒幅正弦水印。
CACHE_DIR_NAME = "tts_cache_volcano"


def _load_env() -> None:
    from dotenv import load_dotenv
    here = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(here, ".env"))
    load_dotenv(os.path.join(here, "..", "backend", ".env"))
    load_dotenv()  # 兜底：当前工作目录


def pcm_to_wav(pcm: bytes) -> bytes:
    """给裸 PCM 加 44 字节标准 WAV 头，使每片都能被 decodeAudioData 独立解码。"""
    n = len(pcm)
    head = b"RIFF" + struct.pack("<I", 36 + n) + b"WAVE"
    head += b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, CHANNELS, SAMPLE_RATE,
        SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH,
        CHANNELS * SAMPLE_WIDTH, SAMPLE_WIDTH * 8,
    )
    head += b"data" + struct.pack("<I", n)
    return head + pcm


class TTSClient:
    """线程安全的按句合成客户端。音色由 .env 固定，全通话/全文字一致。"""

    def __init__(self, api_key: str = "", speaker: str = "", resource_id: str = "",
                 voice: Optional[str] = None, response_format: str = "wav",
                 speed: float = 1.0, **_ignored):
        # voice / response_format / speed 是下午基线 server.py:277 传进来的
        # 智谱参数；火山侧音色由 .env 的 VOLCANO_TTS_SPEAKER 统一决定，
        # 这里只接收不改行为，避免 TypeError 打断后台预热。
        _load_env()
        self.api_key = api_key or os.getenv("VOLCANO_TTS_API_KEY", "")
        self.speaker = speaker or os.getenv(
            "VOLCANO_TTS_SPEAKER", "zh_female_xiaohe_uranus_bigtts")
        self.resource_id = resource_id or os.getenv(
            "VOLCANO_TTS_RESOURCE_ID", "seed-tts-2.0")
        if not self.api_key:
            raise RuntimeError("未配置 VOLCANO_TTS_API_KEY，无法使用火山语音合成 2.0")
        self.voice = self.speaker          # 供 getattr(tts, "voice", ...) 读取
        self.response_format = response_format
        self.speed = speed
        self.cache_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", CACHE_DIR_NAME)
        self._cache: dict[str, bytes] = {}
        self._cache_lock = threading.Lock()
        # B1：requests.Session 不是线程安全的；SpeculativeEngine 最多 5 线程并发。
        # 每个线程各持一个 Session，同线程内仍复用 TLS（火山 keep-alive 1 分钟）。
        self._tls = threading.local()
        self._sessions: list[requests.Session] = []
        self._sessions_lock = threading.Lock()
        self.last_error: Optional[str] = None   # B2/B3：最近一次会话异常，供排查

    # ---------------- 连接（按线程隔离） ----------------
    def _get_session(self) -> requests.Session:
        s = getattr(self._tls, "session", None)
        if s is None:
            s = requests.Session()
            self._tls.session = s
            with self._sessions_lock:
                self._sessions.append(s)
        return s

    def _post(self, payload: dict, headers: dict):
        """单独抽出，便于 tools/test_tts_edge.py 用伪造 SSE 复现异常分支。"""
        return self._get_session().post(
            API_URL, headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), stream=True)

    # ---------------- 缓存（只读写 data/tts_cache_volcano/） ----------------
    def _cache_path(self, text: str) -> str:
        key = hashlib.sha1(
            (self.speaker + "|" + (text or "").strip()).encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, key + ".wav")

    def get_cached(self, text: str) -> Optional[bytes]:
        key = (text or "").strip()
        with self._cache_lock:
            hit = self._cache.get(key)
        if hit is not None:
            return hit
        # 内存未命中时回查自己的落盘缓存（重启后 QA 快路径仍可零网络直出）。
        # 注意只读 CACHE_DIR_NAME 目录，绝不碰智谱的 data/tts_cache/。
        try:
            p = self._cache_path(key)
            if os.path.exists(p):
                with open(p, "rb") as f:
                    audio = f.read()
                if audio[:4] == b"RIFF" and len(audio) > 44:
                    with self._cache_lock:
                        self._cache[key] = audio
                    return audio
        except Exception:
            pass
        return None

    def _put_cache(self, text: str, audio: bytes) -> None:
        key = (text or "").strip()
        if not audio:
            return
        with self._cache_lock:
            if len(self._cache) < 400:
                self._cache[key] = audio
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            p = self._cache_path(key)
            if not os.path.exists(p):
                tmp = p + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(audio)
                os.replace(tmp, p)
        except Exception:
            pass

    def load_disk_cache(self, texts: list[str]) -> int:
        """下午基线 server.py:272 调用：把落盘预热音频灌进内存，返回命中条数。

        与 tts_zhipu 同名方法的关键差别：只读火山自己的目录。若这里照抄
        智谱实现去读 data/tts_cache/，33 个带水印的旧 wav 会被当成命中直接
        播出 —— 换厂商后「滴滴」依旧，正是本次要堵死的坑。
        """
        n = 0
        for t in texts or []:
            key = (t or "").strip()
            if not key:
                continue
            try:
                p = self._cache_path(key)
                if not os.path.exists(p):
                    continue
                with open(p, "rb") as f:
                    audio = f.read()
                if audio[:4] == b"RIFF" and len(audio) > 44:
                    with self._cache_lock:
                        self._cache[key] = audio
                    n += 1
            except Exception:
                pass
        return n

    # ---------------- 请求体 / 请求头 ----------------
    def _payload(self, text: str) -> dict:
        return {
            "user": {"uid": "zhiyu-callcenter"},
            "req_params": {
                "text": text,
                "speaker": self.speaker,
                "audio_params": {"format": "pcm", "sample_rate": SAMPLE_RATE},
                "additions": ADDITIONS,   # 必须是字符串，见模块注释
            },
        }

    def _headers(self) -> dict:
        return {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": uuid.uuid4().hex,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

    # ---------------- 流式分片（首字响应关键路径） ----------------
    def synthesize_stream(self, text: str, on_chunk: Callable[[bytes], None],
                          cancel: Optional[threading.Event] = None) -> int:
        """SSE 流式合成：每片回调一个可独立播放的 WAV，返回分片数。

        SSE 事件：352=音频(base64 PCM)、351=句子结束、152=会话结束、153=失败。
        cancel 为下午基线 speculative.py:148 的投机取消信号，置位即停止读流。
        B3：只要已经下发过分片就不再抛异常 —— 上层 server.py 一旦捕获异常
        会降级调用 synthesize() 重新合成整句，用户会听到重复播报。
        完全无音频（n==0）时仍抛异常，保留上层降级能力。
        """
        text = (text or "").strip()
        if not text:
            return 0
        cached = self.get_cached(text)
        if cached is not None:
            if not (cancel and cancel.is_set()):
                on_chunk(cached)
                return 1
            return 0

        self.last_error = None
        n = 0
        bad_frames = 0
        buf = bytearray()
        cur_event = ""
        err: Optional[str] = None
        cancelled = False

        try:
            with self._post(self._payload(text), self._headers()) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"火山TTS HTTP {resp.status_code}: {str(resp.text)[:200]}")
                for raw_line in resp.iter_lines():
                    if cancel and cancel.is_set():
                        cancelled = True
                        break       # 投机预测被取消：尽快释放连接与配额
                    if not raw_line:
                        continue
                    line = raw_line.decode("utf-8", "replace").strip() \
                        if isinstance(raw_line, bytes) else raw_line.strip()
                    if line.startswith("event:"):
                        cur_event = line[6:].strip()
                        # B2：153 是失败事件，即使 code=0 / data 为空也必须标记
                        if cur_event == "153" and err is None:
                            err = "SessionFailed(153)"
                        continue
                    if not line.startswith("data:"):
                        continue
                    try:
                        obj = json.loads(line[5:].strip())
                    except (ValueError, TypeError):
                        continue
                    code = obj.get("code")
                    if code not in OK_CODES and err is None:
                        err = f"{code}: {str(obj.get('message', ''))[:120]}"
                    if cur_event == "352" and obj.get("data"):
                        raw = "".join(str(obj["data"]).split())   # 去掉可能的换行
                        try:
                            buf.extend(base64.b64decode(raw, validate=True))
                        except (binascii.Error, ValueError):
                            # B4：单个损坏分片跳过即可，不影响后续分片
                            bad_frames += 1
                            continue
                        threshold = FIRST_FLUSH_BYTES if n == 0 else FLUSH_BYTES
                        if len(buf) >= threshold:
                            n += 1
                            on_chunk(pcm_to_wav(bytes(buf)))
                            buf.clear()
        except Exception as e:
            if n == 0 and not cancelled:
                raise
            # 建连/读流中断但已下发音频：同样不能抛，否则上层会重复播报
            err = err or f"{type(e).__name__}: {e}"

        if buf and not cancelled:
            n += 1
            on_chunk(pcm_to_wav(bytes(buf)))

        if cancelled:
            # 主动取消不是故障：不写 last_error、不抛异常、不触发降级重合成
            return n

        if err is not None:
            self.last_error = err + (f"；跳过 {bad_frames} 个坏分片" if bad_frames else "")
            if n == 0:
                raise RuntimeError("火山TTS " + self.last_error)
            print(f"[TTS] 会话异常但已下发 {n} 片，跳过降级重合成以免重复播报："
                  f"{self.last_error}", file=sys.stderr, flush=True)
        elif bad_frames:
            print(f"[TTS] 跳过 {bad_frames} 个损坏音频分片", file=sys.stderr, flush=True)

        if n == 0:
            raise RuntimeError("火山TTS 返回无有效音频分片")
        return n

    # ---------------- 首包竞速（下午基线接口） ----------------
    def synthesize_stream_raced(self, text: str, on_chunk: Callable[[bytes], None],
                                delay: float = 0.15) -> int:
        """兼容下午基线 server.py:2159 的「本轮首片段」接口。

        原实现是智谱 GLM-TTS 的双路 hedged 请求（首包 TTFB 0.6~1.5s 波动大）。
        火山 2.0 实测首片 0.23~0.66s 且稳定，双路只会让同一句付两次钱、
        多占一个并发位，因此这里直接走单路流式，接口与返回语义保持一致。
        """
        return self.synthesize_stream(text, on_chunk)

    # ---------------- 整句非流式（兜底 / 预热 / 缓存） ----------------
    def synthesize(self, text: str, timeout: tuple = (2, 5)) -> bytes:
        """同步合成一句 → 完整 WAV bytes；失败抛异常，由调用方降级。

        timeout 仅为兼容下午基线 server.py:2170 的 synthesize(sentence,(2,4))；
        火山流式已有 CONNECT_TIMEOUT/READ_TIMEOUT，短超时会截断长句，故忽略。
        """
        text = (text or "").strip()
        if not text:
            return b""
        cached = self.get_cached(text)
        if cached is not None:
            return cached
        parts: list[bytes] = []
        self.synthesize_stream(
            text, lambda b: parts.append(b[44:] if b[:4] == b"RIFF" else b))
        pcm = b"".join(parts)
        if not pcm:
            raise RuntimeError("火山TTS 返回空音频")
        audio = pcm_to_wav(pcm)
        self._put_cache(text, audio)
        return audio

    # ---------------- 启动预热 ----------------
    def warmup(self, texts: Optional[list[str]] = None,
               pause: Optional[threading.Event] = None) -> int:
        """后台预合成固定话术 + 预热 TLS 连接，命中缓存后首帧 0ms 下发。"""
        from tts_zhipu import PREWARM_TEXTS
        ok = 0
        for t in (texts if texts is not None else PREWARM_TEXTS):
            if pause is not None and pause.is_set():
                break
            try:
                if self.get_cached(t) is None:
                    self.synthesize(t)
                ok += 1
            except Exception:
                pass
        return ok

    def close(self) -> None:
        self._cache.clear()
        with self._sessions_lock:
            sessions, self._sessions = list(self._sessions), []
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass
