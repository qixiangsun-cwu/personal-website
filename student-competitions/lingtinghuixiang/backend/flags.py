# -*- coding: utf-8 -*-
"""统一标记规则（11/15 第七轮整改：工单看板 + 历史记录同源）。

背景：以前「闭环 / 未闭环」标签在历史记录里由前端 `c.closed_loop` 自己判，
「需转人工」在工单轨迹里另有一套 flag 事件，两处口径、文案、颜色都不同步。
本模块把「需要转人工」与「未闭环」收敛成**两个纯函数**，只吃 calls 表行
（含 result_json），产出统一结构的标记列表：

    {"type": "transfer_human" | "unclosed",
     "label": "需转人工" | "未闭环",
     "glyph": "⛑" | "⚠",
     "brief": 短原因（直接显示在徽标上）,
     "reason": 完整原因（写明判定依据，供 title / 标记说明卡片）,
     "code": 结束原因码, "status": 通话状态, "call_id": 通话号}

调用方：
  * server.api_calls / api_call_detail    → 每通通话 `flags`
  * orders.list_orders / get_order        → 工单 `flags`（取名下最近一通通话，
    与历史记录里同一通通话的标记逐字段一致）+ 关联通话各自的 `flags`
  * 前端 admin.html 的 flagsHTML() / flagDetailHTML() 是唯一渲染器

刻意不 import db / orders / server，避免循环依赖且便于离线回归。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

# 口径文案：前端两处页面共用同一段文字，避免各写一套说明
RULE_TEXT = (
    "标记口径（工单看板与历史记录同源）："
    "「需转人工」= 通话已登记转人工（全时无空 / 身份质疑 / 商家主动要人工 / 严重违规）；"
    "「未闭环」= closed_loop=0，含通话中、链路中断、中途挂断、非任务型结束等；"
    "两类标记均随附具体原因。"
)

# 结束原因码 → 中文短标签（用于徽标短原因与完整原因拼接）
CLOSE_LABELS: Dict[str, str] = {
    "NO_TIME_TRANSFER": "全时无空 · 转人工",
    "DISTRUST_TRANSFER": "身份质疑 · 转人工",
    "HUMAN_REQUEST": "商家主动要人工",
    "HUMAN": "商家要求人工",
    "COMPLAINT": "商家投诉 · 人工跟进",
    "EMOTIONAL": "情绪激烈 · 礼貌收线",
    "MERCHANT_BYE": "商家提前道别",
    "MERCHANT_REFUSE": "商家明确拒绝整改",
    "NOT_CONTACT": "未联系到负责人",
    "PUSHBACK_CLOSE": "反复质疑 · 礼貌收线",
    "OFFTOPIC_CLOSE": "连续闲聊 · 礼貌收线",
    "HESITANT_LOOP": "连续含糊 · 结束通话",
    "RESCHEDULE_NO_TIME": "近期没空 · 未约时间",
    "MAX_TURNS": "达到最大轮次",
}

# 明确「已启动转人工」的结束原因 → (徽标短原因, 完整原因)
TRANSFER_REASONS: Dict[str, tuple] = {
    "NO_TIME_TRANSFER": (
        "全时无空，无可约时间",
        "商家表示哪一天都没空、无可约时间；数字员工已登记转人工专员，"
        "由人工同事另约时间跟进整改，本次通话按非任务型结束，不计任务闭环。"),
    "DISTRUST_TRANSFER": (
        "身份质疑，第三次转人工",
        "商家连续质疑数字员工身份真实性，两级自证后仍不放心；"
        "数字员工已引导转人工专员核实处理并结束通话，不计任务闭环。"),
    "HUMAN_REQUEST": (
        "商家主动要求人工",
        "商家在通话中明确要求人工客服 / 真人服务；数字员工已礼貌引导并结束通话，"
        "转由人工同事跟进，不计任务闭环。"),
    "HUMAN": (
        "商家要求人工",
        "商家在身份确认环节要求转人工；数字员工已结束通话转人工跟进，不计任务闭环。"),
    "COMPLAINT": (
        "商家投诉，转人工跟进",
        "商家在身份确认环节提出投诉；数字员工已结束通话并留痕，需人工同事跟进处理。"),
}

SEVERE_TRANSFER_BRIEF = "严重违规，屡教不改转人工"
SEVERE_TRANSFER_REASON = (
    "商家同一违规诉求屡教不改（分支 C 严重违规）；数字员工已打【严重违规】标签、"
    "终止本次通话并转人工专员跟进，不计任务闭环。")

# flag_log 里同样代表「已启动转人工」的 kind（只在 close_reason 缺失时兜底）
TRANSFER_FLAG_KINDS = {"no_day_free", "human_request", "distrust_transfer"}

LIVE_STATUS = {"in_progress", "ringing", "idle", ""}
STATUS_BRIEF = {
    "in_progress": "通话进行中",
    "ringing": "振铃中，尚未接通",
    "idle": "尚未开始",
    "failed": "通话链路中断",
    "missed": "商家未接听",
    "rejected": "商家已拒接",
    "pending_followup": "商家中途挂断",
    "superseded": "已由后续通话接替",
    "completed": "已结束但未闭环",
}

CHECK_LABELS = [
    ("finished", "流程未走完"),
    ("elements_complete", "必说话术未全覆盖"),
    ("no_violation", "命中输出禁语"),
    ("turns_ok", "商家有效轮次不足"),
    ("task_end", "非任务型结束"),
]


def _result(call: Dict[str, Any]) -> Dict[str, Any]:
    """兼容 calls 表行（result_json 字符串）、/api/orders 详情（result 字典）。"""
    raw = call.get("result_json")
    if raw is None:
        raw = call.get("result")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str) and raw.strip():
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _is_closed(call: Dict[str, Any], res: Dict[str, Any]) -> bool:
    """闭环唯一真值：calls.closed_loop 列；缺列时回落 result_json.closed_loop。"""
    v = call.get("closed_loop")
    if v is None:
        v = res.get("closed_loop", False)
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "none", "null")
    return bool(v)


def _code(res: Dict[str, Any]) -> str:
    return str(res.get("close_reason") or "").strip()


def _needs_transfer(res: Dict[str, Any], code: str) -> bool:
    if res.get("transfer_human") is True:
        return True
    if code in TRANSFER_REASONS or code.startswith("VIOLATION_SEVERE_"):
        return True
    if res.get("violation_severe") is True:
        return True
    for f in (res.get("flags") or []):
        if isinstance(f, dict) and f.get("kind") in TRANSFER_FLAG_KINDS:
            return True
    return False


def _transfer_detail(res: Dict[str, Any], code: str):
    if code in TRANSFER_REASONS:
        return TRANSFER_REASONS[code]
    if code.startswith("VIOLATION_SEVERE_") or res.get("violation_severe") is True:
        return SEVERE_TRANSFER_BRIEF, SEVERE_TRANSFER_REASON
    if res.get("transfer_human") is True:
        return "已启动转人工", "复盘 transfer_human=True：本次通话已登记转人工专员跟进，不计任务闭环。"
    return ("已登记转人工", "特殊标记时间线中出现转人工类留痕，需人工同事跟进处理。")


def _pending_reason(res: Dict[str, Any]) -> str:
    for x in (res.get("pending") or []):
        s = str(x)
        if s.startswith("未闭环原因："):
            return s[len("未闭环原因："):].strip()
    return ""


def _failed_checks(res: Dict[str, Any]) -> List[str]:
    ck = res.get("closure_checks") or {}
    if not isinstance(ck, dict):
        return []
    return [label for key, label in CHECK_LABELS if key in ck and not ck[key]]


def _unclosed_detail(call: Dict[str, Any], res: Dict[str, Any], code: str):
    """返回 (短原因, 完整原因)。所有未闭环通话都会拿到一条，绝不返回空原因。"""
    status = str(call.get("status") or "").strip()
    label = CLOSE_LABELS.get(code) or (
        "严重违规 · 转人工" if code.startswith("VIOLATION_SEVERE_") else "")
    pend = _pending_reason(res)
    failed = _failed_checks(res)
    stop = str(res.get("stop_stage") or call.get("stop_stage") or "").strip()
    reason_text = str(res.get("reason") or "").strip()

    if status in LIVE_STATUS:
        brief = STATUS_BRIEF.get(status, "通话尚未结束")
        return brief, (f"通话尚未终结（status={status or '未知'}），闭环判定尚未产生；"
                       f"结束通话后按流程走完、五要素齐全、≥3 轮商家发言、属任务型结束四项硬条件复核。")

    # 已正常结束的通话优先显示「具体为什么没闭环」；其它状态先显示状态本身
    if status == "completed":
        brief = label or (failed[0] if failed else "")
    else:
        brief = STATUS_BRIEF.get(status, "") or label or (failed[0] if failed else "")
    brief = brief or STATUS_BRIEF.get("completed", "闭环条件未满足")

    has_evidence = bool(pend or failed or label or code or reason_text)
    bits = []
    if status in ("failed", "pending_followup", "missed", "rejected", "superseded"):
        prefix = {
            "failed": "通话链路中断，未产生完整结构化回写",
            "pending_followup": "商家中途挂断，任务尚未终结",
            "missed": "商家未接听，未产生通话结论",
            "rejected": "商家拒接，未产生通话结论",
            "superseded": "本通已由后续外呼接替，不再单独计闭环",
        }[status]
        bits.append(prefix)
        if reason_text:
            bits.append(f"收尾备注：{reason_text}")
    if pend:
        bits.append(f"未闭环原因：{pend}")
    elif failed:
        bits.append("未闭环原因：" + "、".join(failed))
    if label:
        bits.append(f"结束原因：{label}（{code}）" if code else f"结束原因：{label}")
    elif code:
        bits.append(f"结束原因码：{code}")
    if stop:
        bits.append(f"停在环节：{stop}")
    if not has_evidence:
        # 极旧数据 / 无复盘：也必须写明判定依据，不能只留一个空徽标
        bits.append("calls.closed_loop=0：四项闭环硬条件未全部满足，本通不计入已闭环工单")
    return brief, "；".join(bits)


def call_flags(call: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """一通通话的统一标记：最多 2 条（需转人工 / 未闭环），顺序固定。"""
    if not isinstance(call, dict):
        return []
    res = _result(call)
    code = _code(res)
    status = str(call.get("status") or "").strip()
    cid = str(call.get("call_id") or "")
    out: List[Dict[str, str]] = []

    if _needs_transfer(res, code):
        brief, reason = _transfer_detail(res, code)
        out.append({
            "type": "transfer_human", "label": "需转人工", "glyph": "⛑",
            "brief": brief, "reason": reason,
            "code": code, "status": status, "call_id": cid,
        })
    if not _is_closed(call, res):
        brief, reason = _unclosed_detail(call, res, code)
        out.append({
            "type": "unclosed", "label": "未闭环", "glyph": "⚠",
            "brief": brief, "reason": reason,
            "code": code, "status": status, "call_id": cid,
        })
    return out


def latest_call_flags(rows: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    """工单标记 = 名下最近一通通话的标记（rows 按 id / started_at 倒序）。

    这样工单看板卡片与历史记录里同一通通话显示的是同一份 flags，
    不存在「工单说转人工、历史说未闭环」的口径分叉。
    """
    for c in (rows or []):
        if isinstance(c, dict):
            return call_flags(c)
    return []


def attach(rows: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """原地给一组通话行挂 flags，返回同一列表（兼容 db.query 的 dict 行）。"""
    for c in (rows or []):
        if isinstance(c, dict):
            c["flags"] = call_flags(c)
    return rows or []
