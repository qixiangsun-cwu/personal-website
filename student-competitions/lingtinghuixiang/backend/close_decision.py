# -*- coding: utf-8 -*-
"""关单方式判定 —— 「该不该打这通电话」的 Agent 分支决策（11/21 新增）

## 为什么要有这个模块

赛题《数据说明》第七节第 3 条原话：

> 做任务规划：把「关单方式判定」实现为 Agent 的分支决策——先判定走电话还是走京麦，
> 再决定是否外呼、如何兜底，这比无差别外呼更贴近真实业务。

而在此之前，项目只是把 xlsx 里的 close_method 当成一个**展示字段**：
拼进提示词（agent.py:管单方式）、显示在工单列表（orders.py）、当成工具返回的标签，
**从来没有用它决定过"这通电话该不该打"**。这是任务规划项的现成失分点。

## 判定依据

直接落《场景汇总》表里的「关单总原则」四条（逐条可溯源）：

  R1 涉及场景关单方式为电话的，需外呼告知商家驳回原因以及处理方案；
     京麦驳回关单场景不做强制回电限制。
  R2 电话场景首次外呼未联系上 → 京麦驳回告知原因即可；
     相同场景二次提交错误信息、电话未联系上 → 创事件跟进预约二次外呼；
     二次外呼未接通 → 执行京麦驳回关单；三次及三次以上 → 一律京麦驳回。
  R3 商家主动要求驳回需回电告知原因 → 均需电话外呼。
  R4 已存在威胁越级投诉、外部曝光（微博/小红书等）→ 均需回电告知。

优先级：R4 > R3 > R5/R2 > R1。风险与商家意愿永远压过场景默认值与次数规则，
因为这些一旦判断错，代价是投诉升级，而不是少打一通电话。

## 设计取舍（为什么是规则而不是让模型拍板）

这四条是**公司制度**，不是语义理解问题：同一份输入必须得到同一份结论，
否则工单、看板、答辩口径全会打架。所以主干判定用确定性规则，并输出完整 trace 供审计；
真正需要语义的只有两个布尔信号（商家是否主动要求回电、是否存在投诉曝光威胁），
由调用方用现有路由（转人工 / 投诉 / 风险标记）或模型给出 —— 见 `with_semantic_signals()`。

## 用法

    import close_decision
    cd = close_decision.from_db(merchant_id, scenario, call_id)
    cd["method"]      # "电话" | "京麦驳回"
    cd["rule"]        # "R1" ... "R5"
    cd["call_now"]    # 这通电话是否应当外呼
    cd["create_event"]# 是否应创事件预约二次外呼
    cd["trace"]       # 逐条规则命中记录，供工单/答辩溯源
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

PHONE = "电话"
JM = "京麦驳回"

# 规则的展示名（工单/看板/答辩直接引用，避免各处自己写死中文）
RULE_NAMES = {
    "R1": "场景关单方式为电话，需外呼告知原因与处理方案",
    "R2a": "首次外呼未接通，京麦驳回告知原因即可",
    "R2b": "同场景二次错误且未接通，创事件预约二次外呼",
    "R2c": "二次外呼仍未接通 / 三次及以上错误，转京麦驳回关单",
    "R3": "商家主动要求回电告知原因，必须电话外呼",
    "R4": "存在越级投诉或外部曝光风险，必须回电",
    "R5": "场景关单方式为京麦驳回，不做强制回电",
}


def _blank(method: str = PHONE, rule: str = "R1", reason: str = "") -> Dict[str, Any]:
    return {
        "method": method,
        "rule": rule,
        "rule_name": RULE_NAMES.get(rule, ""),
        "reason": reason,
        "call_now": method == PHONE,
        "create_event": False,
        "priority": 0,
        "confidence": "high",
        "evidence": {},
        "trace": [],
    }


def _hit(res: Dict[str, Any], rule: str, note: str) -> Dict[str, Any]:
    """收尾：写入规则号 + 规则名，并记录一条命中轨迹。

    rule 与 rule_name 必须**一起**写：_blank() 先给了 R1 的默认名，
    各分支只 update(rule=...) 会留下"R5 配 R1 说明"的错配（已实测踩到）。
    """
    res["rule"] = rule
    res["rule_name"] = RULE_NAMES.get(rule, "")
    res["trace"].append({"rule": rule, "note": note})
    return res


def decide(scenario_method: str = PHONE,
           same_scenario_errors: int = 1,
           attempts: int = 0,
           last_attempt_connected: Optional[bool] = None,
           pending_callback_event: bool = False,
           merchant_requested_callback: bool = False,
           escalate_risk: bool = False,
           risk_note: str = "") -> Dict[str, Any]:
    """核心判定（纯函数，无 IO，可离线回放）。

    参数
    ----
    scenario_method          场景表里的关单方式（"电话" / "京麦驳回"）
    same_scenario_errors     同一场景累计提交错误次数（含本次，最小按 1 计）
    attempts                 该场景已发生的电话外呼次数
    last_attempt_connected   上次外呼是否接通；None 表示无历史（首次）
    pending_callback_event   是否已存在「预约二次外呼」的跟进事件
    merchant_requested_callback  商家是否主动要求回电
    escalate_risk            是否存在越级投诉 / 外部曝光威胁
    risk_note                风险原话（可溯源用）
    """
    errs = max(1, int(same_scenario_errors or 1))
    tries = max(0, int(attempts or 0))
    scen = (scenario_method or PHONE).strip()

    res = _blank()
    res["evidence"] = {
        "scenario_method": scen,
        "same_scenario_errors": errs,
        "attempts": tries,
        "last_attempt_connected": last_attempt_connected,
        "pending_callback_event": bool(pending_callback_event),
        "merchant_requested_callback": bool(merchant_requested_callback),
        "escalate_risk": bool(escalate_risk),
        "risk_note": risk_note or "",
    }

    # ---- R4 最高优先：投诉/曝光风险 → 必须回电 ----
    if escalate_risk:
        res.update(method=PHONE, rule="R4", priority=40, call_now=True,
                   reason="商家已出现越级投诉或外部曝光倾向，必须回电说明驳回原因与处理方案")
        if risk_note:
            res["reason"] += f"（原话：{risk_note[:40]}）"
        return _hit(res, "R4", "命中风险信号，覆盖场景默认值与次数规则")

    # ---- R3：商家主动要求回电 ----
    if merchant_requested_callback:
        res.update(method=PHONE, rule="R3", priority=30, call_now=True,
                   reason="商家主动要求回电告知原因，按关单总原则第 3 条必须电话外呼")
        return _hit(res, "R3", "商家主动要求回电，覆盖次数规则")

    # ---- R5：场景本就只需站内驳回 ----
    if scen == JM:
        res.update(method=JM, rule="R5", priority=20, call_now=False,
                   reason="该场景关单方式为京麦驳回，商家没有可补救动作，不做强制回电")
        return _hit(res, "R5", "场景默认即为京麦驳回")

    # ---- R2c：三次及以上 → 一律京麦驳回 ----
    if errs >= 3:
        res.update(method=JM, rule="R2c", priority=15, call_now=False,
                   reason=f"同一场景已第 {errs} 次提交错误，按关单总原则第 2 条一律采用京麦驳回关单")
        return _hit(res, "R2c", f"错误次数 {errs} >= 3")

    # ---- R2c 另一半：已有预约二次外呼事件，且二次仍未接通 → 京麦驳回 ----
    if pending_callback_event and last_attempt_connected is False:
        res.update(method=JM, rule="R2c", priority=14, call_now=False,
                   reason="已预约过二次外呼仍未接通，按关单总原则第 2 条执行京麦驳回关单")
        return _hit(res, "R2c", "预约二次外呼事件已完成但未接通")

    # ---- R2b：二次错误 + 未接通 + 还没有跟进事件 → 创事件预约二次外呼 ----
    if errs == 2 and last_attempt_connected is False and not pending_callback_event:
        res.update(method=PHONE, rule="R2b", priority=13, call_now=True, create_event=True,
                   reason="同一场景二次提交仍错误且上次未接通，需创事件跟进并预约二次外呼")
        return _hit(res, "R2b", "错误次数 2 且上次未接通，按规则创事件预约二次外呼")

    # ---- R2a：首次未接通 → 京麦驳回告知即可 ----
    if errs == 1 and tries >= 1 and last_attempt_connected is False:
        res.update(method=JM, rule="R2a", priority=12, call_now=False,
                   reason="首次外呼未联系上，按关单总原则第 2 条先用京麦驳回告知原因即可")
        return _hit(res, "R2a", "首次外呼未接通")

    # ---- R1：场景要求电话 → 外呼 ----
    res.update(method=PHONE, rule="R1", priority=10, call_now=True,
               reason="该场景关单方式为电话，需外呼告知商家驳回原因与处理方案")
    # 输入不全时把置信度降下来，方便上层决定要不要人工复核
    if last_attempt_connected is None and tries > 0:
        res["confidence"] = "medium"
        _hit(res, "R1", "缺少上次接通状态，按场景默认值判定")
    else:
        _hit(res, "R1", "场景默认电话外呼")
    return res


def with_semantic_signals(decision: Dict[str, Any], *,
                          merchant_requested_callback: bool = False,
                          escalate_risk: bool = False,
                          risk_note: str = "") -> Dict[str, Any]:
    """把语义信号（取自转人工/投诉路由或模型判断）并入既有判定。

    只在信号**新出现**时升级：已经判定要回电的不再重复算，判定为京麦驳回的
    被 R3/R4 合法升级 —— 这正是「商家临场改主意」应当得到的处理。
    """
    if decision.get("rule") in ("R4", "R3"):
        return decision
    if not (merchant_requested_callback or escalate_risk):
        return decision
    ev = dict(decision.get("evidence") or {})
    ev["merchant_requested_callback"] = bool(merchant_requested_callback)
    ev["escalate_risk"] = bool(escalate_risk)
    if risk_note:
        ev["risk_note"] = risk_note
    new = decide(
        scenario_method=ev.get("scenario_method", PHONE),
        same_scenario_errors=ev.get("same_scenario_errors", 1),
        attempts=ev.get("attempts", 0),
        last_attempt_connected=ev.get("last_attempt_connected"),
        pending_callback_event=ev.get("pending_callback_event", False),
        merchant_requested_callback=merchant_requested_callback,
        escalate_risk=escalate_risk,
        risk_note=risk_note,
    )
    new["trace"] = list(decision.get("trace") or []) + new["trace"]
    new["upgraded_from"] = decision.get("rule")
    return new


def _num(row: Any, key: str, default: int = 0) -> int:
    try:
        return int((row or {}).get(key) or default)
    except (TypeError, ValueError):
        return default


def from_db(merchant_id: Any, scenario: Optional[dict] = None,
            call_id: str = "") -> Dict[str, Any]:
    """从库里取历史信号并判定（无库/无数据时安全退化为场景默认值）。

    信号来源：
      · 同场景累计错误次数 → calls 表里该商家+该场景的 closed_loop=0 的通数 +1（含本次）
      · 已外呼次数与上次是否接通 → 同场景最近一通的方向/状态
      · 是否已有预约二次外呼的跟进事件 → work_orders.result_json 的 pending 字段
    任何异常都退回「按场景默认值判定」，绝不因为查不到历史就卡住外呼。
    """
    scen = scenario or {}
    method = (scen.get("close_method") or PHONE).strip()
    signals = {"same_scenario_errors": 1, "attempts": 0,
               "last_attempt_connected": None, "pending_callback_event": False}
    try:
        import db
        sid = scen.get("id")
        rows = db.query(
            "SELECT call_id, direction, status, closed_loop, stop_stage FROM calls "
            "WHERE merchant_id=? AND (? IS NULL OR scenario_id=?) AND call_id<>? "
            "ORDER BY id DESC LIMIT 10",
            (merchant_id, sid, sid, call_id or "")) or []
        if rows:
            signals["attempts"] = len([r for r in rows if (r.get("direction") or "outbound") == "outbound"])
            last = rows[0]
            st = (last.get("status") or "").lower()
            signals["last_attempt_connected"] = st not in ("missed", "rejected", "no_answer", "")
            # 未闭环的历史通数 = 之前提交过但没整改成功 → 本次是第 N+1 次
            unclosed = len([r for r in rows if not _num(r, "closed_loop", 0)])
            signals["same_scenario_errors"] = unclosed + 1
        wo = db.query_one(
            "SELECT result_json FROM work_orders WHERE merchant_id=? ORDER BY id DESC LIMIT 1",
            (merchant_id,)) if merchant_id else None
        if wo:
            import json
            try:
                rj = json.loads(wo.get("result_json") or "{}")
            except Exception:
                rj = {}
            pend = rj.get("pending") or []
            signals["pending_callback_event"] = any("二次外呼" in str(x) for x in pend)
    except Exception:
        signals = {"same_scenario_errors": 1, "attempts": 0,
                   "last_attempt_connected": None, "pending_callback_event": False}

    return decide(scenario_method=method, **signals)


def summary_line(d: Optional[Dict[str, Any]]) -> str:
    """给提示词/工单用的一行结论（含依据，方便答辩时逐条解释）。"""
    if not d:
        return ""
    flag = "需外呼" if d.get("call_now") else "无需外呼"
    tail = "；并创事件预约二次外呼" if d.get("create_event") else ""
    return f"{d.get('method')}（{flag}{tail}）· 依据 {d.get('rule')}：{d.get('rule_name')}"
