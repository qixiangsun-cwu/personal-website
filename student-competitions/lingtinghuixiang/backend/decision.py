# -*- coding: utf-8 -*-
"""结构化决策 —— 把 classify / tool-decide / generate 三次 LLM 往返合并成一次（11/21 新增）

## 为什么合并会把 P50 变好，而不是变差

改造前每轮最多三次**串行** LLM 往返：
    ① `_classify_user_intent`  打一个 4 分类标签（max_tokens=8）
    ② `tools_registry._decide` 决定要不要查、查什么（function call，实测 640~1140ms）
    ③ `_generate`              流式写播报文本（首片段约 1.2s）
串行合计 2.1~3.2s，而投机窗口只有 1~1.5s —— 这正是 `speculative.py` 把覆盖率
从 0.50 硬压到 0.35、并在 README 里承认"首帧可能答非所问"的原因。

合并之后只剩**一次**调用，返回结构化的：

    {"intent": "new_question|confirmed_understood|hesitant|other",
     "slots": {"qtype": "...", "material": "...", "action": "...", "expect": "..."},
     "say": "本轮要播报的话（1~2 句，≤50 字）"}

一次性拿到「怎么理解、填哪个槽、说什么」，往返 3→1。

## 关键词门控为什么可以删掉

`tools_registry.should_use()` 用 26 个关键词决定"这轮值不值得多花一次决策往返"。
它存在的**唯一理由**就是那次往返太贵。合并之后不再有第二次往返：

    · 证据（工单状态 / 商家档案 / 知识库命中）全部是本地 SQLite 查询，单轮 ~ms 级；
    · 于是每轮都把这小份证据直接放进同一次调用的上下文，让模型自己决定用不用。

**"要不要查"不再由关键词表决定，而由模型在拿到证据后自己决定** —— 这正是
赛题「工具调用策略由 RSI 优化」的对象。旧关键词表保留在 legacy 路径里（默认不生效）。

## 安全边界（一行都没松）

红线词库、禁语拦截、必说要素、状态不变式四道闸门全部在**输出侧/校验侧**，
与"谁做决策"无关：

    模型提议  ──►  invariants.veto（否决器）  ──►  既有输出闸门  ──►  播报
                      │
                      └─ 违反 → 退回规则算出的安全状态，并记录违规编号

## 开关

    DECISION_MODE   legacy（默认，行为与改造前完全一致）| struct（本模块生效）
    TOOL_GATE       on（默认，legacy 路径保留关键词门控）| off（A/B 对照用）

默认 legacy 是为了保证 983 条离线回归零回归；切到 struct 需要在有 Key 的环境实测。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import prompts

# 结束态（与 agent.END / invariants.END 同值）。这里本地定义而不是 import agent，
# 是为了让本模块能被离线单测，不必先起一个 Agent。
END = "END"

SYS_HINT = """你是京东商家资质审核外呼的数字员工。这一轮你需要**一次性**完成三件事：
1) 判断商家这句话的意图；2) 填写槽位；3) 说出本轮要播报的话。

只输出一个 JSON 对象，不要任何解释、不要 Markdown 代码块：
{{"intent": "new_question|confirmed_understood|hesitant|other",
  "advance": "stay|next",
  "slots": {{"qtype": "", "material": "", "action": "", "expect": ""}},
  "say": "本轮播报文本"}}

intent 判定口径：
  new_question          提出了问题、追问、质疑、转折 —— 必须先正面解答
  confirmed_understood  明确表示已理解且没有新问题
  hesitant              含糊、犹豫、未明确表态 —— 主动追问具体疑虑
  other                 以上都不是（寒暄、拒接、场景标记等）

advance 判定口径（本环节该不该往下走）：
  stay  商家还在问、还没听明白、或是关键信息没拿到 —— **停在本环节继续解答/追问**
  next  商家已明确无新问题且当前环节的事做完了 —— 可以进入下一环节
  注意：你只能提议「停」或「进入下一环节」，**不能直接提议结束通话**；
  结束由规则层的闭环闸门判断（避免半截电话被算成闭环）。

say 的硬要求：
  · 一到两句、不超过 50 个汉字，口语化，适合语音播报；
  · 不得出现「保证通过 / 绝对能过 / 包过 / 百分百」等承诺，不得索要身份证号、验证码；
  · 不得出现括号占位符（【】/XX），不知道就如实说"以京麦后台驳回意见为准"；
  · 依据下面给出的【证据】作答，证据没有的不要编造；
  · 不得说出状态名、参数名、JSON 片段。
"""


# ---------------------------------------------------------------- 开关
def mode() -> str:
    """当前决策模式：legacy（默认）/ struct。"""
    m = (os.getenv("DECISION_MODE") or "legacy").strip().lower()
    return m if m in ("legacy", "struct") else "legacy"


def is_struct() -> bool:
    return mode() == "struct"


# ---------------------------------------------------------------- 证据
def evidence_bundle(agent: Any, user_text: str, limit_knowledge: int = 3) -> str:
    """本地证据包：场景 + 工单 + 商家档案 + 知识库命中，全部 SQLite 查询（~ms）。

    取代 `tools_registry.should_use()` 的关键词门控 —— 既然不再多花一次往返，
    就不需要"值不值得查"的预判，直接把证据摆给模型，让它自己决定用哪条。
    """
    import db

    scen = getattr(agent, "scenario", None) or {}
    merchant = getattr(agent, "merchant", None) or {}
    parts: List[str] = []

    parts.append(
        "【本通电话】"
        f"商家={merchant.get('company_name') or '未提供'}"
        f"（店铺 {merchant.get('shop_name') or '未提供'}）；"
        f"驳回场景={scen.get('level2_category') or '未提供'}；"
        f"关单方式={(getattr(agent, 'close_decision', None) or {}).get('method') or scen.get('close_method') or '电话'}"
        f"（依据 {(getattr(agent, 'close_decision', None) or {}).get('rule') or '-'}）。"
    )
    if scen.get("script_text"):
        parts.append(f"【标准话术】{str(scen['script_text'])[:160]}")

    # 工单/档案：模型不需要"申请查询"，证据已经在这里
    try:
        import tools_registry
        ctx = {"call_id": getattr(agent, "call_id", ""),
               "merchant_id": merchant.get("merchant_id") or merchant.get("id"),
               "company": merchant.get("company_name", ""),
               "shop": merchant.get("shop_name", ""),
               "scenario": scen.get("level2_category", "")}
        for name in ("query_order_status", "query_merchant_profile"):
            r = tools_registry.execute(name, {"aspect": "all"}, ctx) or {}
            ev = (r.get("evidence") or "").strip()
            if ev:
                parts.append(f"【{tools_registry.TOOL_LABELS.get(name, name)}】{ev[:220]}")
    except Exception:
        pass

    # 知识库：用本轮商家原话检索（和改造前同一套 FTS）
    try:
        hits = db.search_knowledge(user_text or "", top_k=limit_knowledge) or []
        for h in hits:
            if h.get("source") == "merchant":
                continue
            parts.append(f"【知识·{h.get('source', '')}】{str(h.get('content') or '')[:200]}")
    except Exception:
        pass

    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------- 组装与解析
def build_messages(agent: Any, user_text: str, prompt_state: str,
                   limit_history: int = 8) -> List[dict]:
    """一次调用的完整消息：系统提示词 + 本轮证据 + 最近历史 + 本轮商家原话。"""
    system = SYS_HINT
    try:
        # 复用既有外呼/来电模板（保持话术口径与合规红线和改造前完全一致）
        base = agent._system_prompt(prompt_state, user_text)
        if base:
            system = base + "\n\n" + SYS_HINT
    except Exception:
        pass
    msgs: List[dict] = [{"role": "system", "content": system}]
    ev = evidence_bundle(agent, user_text)
    if ev:
        msgs.append({"role": "system", "content": "【证据（系统实时查询，可直接引用）】\n" + ev})
    for h in (getattr(agent, "history", None) or [])[-limit_history:]:
        role = h.get("role")
        if role in ("user", "assistant") and h.get("content"):
            msgs.append({"role": role, "content": h["content"]})
    msgs.append({"role": "user", "content": user_text or "（开场）"})
    return msgs


_JSON_RE = re.compile(r"\{.*\}", re.S)
_INTENTS = ("new_question", "confirmed_understood", "hesitant", "other")


def parse(raw: str) -> Dict[str, Any]:
    """把模型返回解析成结构化决策；任何异常都退化成安全默认值（绝不抛错）。"""
    out = {"intent": "other", "slots": {}, "say": "", "ok": False, "raw": (raw or "")[:300]}
    if not raw:
        return out
    m = _JSON_RE.search(raw)
    if not m:
        return out
    try:
        data = json.loads(m.group(0))
    except Exception:
        return out
    if not isinstance(data, dict):
        return out
    intent = str(data.get("intent") or "").strip().lower()
    out["intent"] = intent if intent in _INTENTS else "other"
    adv = str(data.get("advance") or "").strip().lower()
    out["advance"] = adv if adv in ("stay", "next") else "next"
    slots = data.get("slots") or {}
    if isinstance(slots, dict):
        out["slots"] = {str(k): str(v)[:60] for k, v in slots.items() if v}
    say = data.get("say")
    out["say"] = str(say).strip()[:200] if isinstance(say, str) else ""
    out["ok"] = bool(out["say"])
    return out


def decide(client, model: str, agent: Any, user_text: str, prompt_state: str,
           temperature: float = 0.3, max_tokens: int = 220) -> Dict[str, Any]:
    """**一次调用**拿到意图 + 槽位 + 播报文本。失败时返回 ok=False，由上层走兜底。"""
    msgs = build_messages(agent, user_text, prompt_state)
    try:
        r = client.chat.completions.create(
            model=model, messages=msgs, temperature=temperature,
            max_tokens=max_tokens, stream=False)
        raw = (r.choices[0].message.content or "") if r.choices else ""
    except Exception as e:
        return {"intent": "other", "slots": {}, "say": "", "ok": False,
                "error": f"{type(e).__name__}: {e}"}
    return parse(raw)


# ---------------------------------------------------------------- 否决与应用
def propose_state(parsed: Dict[str, Any], prev_state: str, rule_state: str) -> str:
    """由模型的 advance 提议推出候选迁移状态（**不允许直接提议结束**）。

    · 规则要结束（rule_state == END）→ 一律按规则，模型无权提前收线；
    · advance=="stay" → 停在本环节（这是最有价值的纠正：防止商家还在问就被推进）；
    · advance=="next" → 采用规则算出的推进目标。
    """
    if rule_state == END:
        return END
    if (parsed or {}).get("advance") == "stay":
        return prev_state
    return rule_state


def apply_veto(proposal: Dict[str, Any], proposed_state: str, rule_state: str,
               ctx: Dict[str, Any], after_elements: Optional[set] = None,
               after: Optional[Dict[str, Any]] = None
               ) -> Tuple[str, List[str]]:
    """把模型提议的状态过一遍不变式否决器；违反则退回规则算出的安全状态。

    这是"规则层降级为否决器"的落地点：规则不再抢着决定走哪一步，
    只在模型提议破坏业务不变式时行使否决权。

    `after` 传本轮**结束后**的预计事实（身份是否确认、要素、收线标记）——
    否则「本轮回复本身就在确认身份」会被误判成违规（实测踩到过 7 处时序误伤）。
    """
    import invariants
    return invariants.veto(proposed_state, rule_state, ctx, after_elements, after)


def merge_slots(agent: Any, parsed: Dict[str, Any]) -> Dict[str, Any]:
    """把模型填的槽位并入本通电话的槽位账本（场景三槽已在开单时预填）。"""
    import slots as slots_mod
    base = getattr(agent, "slots", None) or slots_mod.from_scenario(
        getattr(agent, "scenario", None))
    merged = slots_mod.merge(base, parsed.get("slots") or {})
    try:
        agent.slots = merged
    except Exception:
        pass
    return merged


def describe(parsed: Dict[str, Any]) -> str:
    """一行日志：本轮决策的结构化摘要（落库/复盘用）。"""
    if not parsed:
        return ""
    intents = {"new_question": "追问", "confirmed_understood": "已理解",
               "hesitant": "含糊", "other": "其它"}
    sl = parsed.get("slots") or {}
    filled = "、".join(f"{k}={v}" for k, v in sl.items() if v) or "无"
    return (f"intent={intents.get(parsed.get('intent'), parsed.get('intent'))}"
            f"｜槽位 {filled}｜say={len(parsed.get('say') or '')}字")
