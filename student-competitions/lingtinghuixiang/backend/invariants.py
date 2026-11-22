# -*- coding: utf-8 -*-
"""状态不变式 —— 规则层从「路由器」降级为「否决器」的可执行定义（11/21 新增）

## 这个文件在架构里的位置

改造前：`agent._route()` 是一棵 613 行的 if/elif 树，它**同时**干三件事 ——
理解商家在说什么、决定走到哪一步、决定说什么话。规则既当检察官又当法官。

改造目标：把「决定走到哪一步」交出去（模型提议），规则只保留**否决权**：

    模型提议 next_state  ──►  不变式校验（本文件）  ──►  通过 → 执行
                                      │
                                      └─ 违反 → 否决，退回规则算出的安全状态

## 为什么不变式比 if/elif 更适合当护栏

if/elif 是「按句子特征分派」，每遇到一种新说法就要加一条分支（历史上加了 110 处）；
不变式是「按状态语义约束」，与商家怎么说无关，**加不改商家话术也不会失效**。
比如「身份没确认不得透露驳回详情」是一条约束，无论商家换成什么说法都成立。

## 不变式清单（每条都能对应到一次真实翻车）

  I1 NO_DISCLOSE_BEFORE_IDENTITY
     身份未确认不得进入 S2/S3/S4（驳回详情播报段）。来电场景无身份环节，豁免。
     翻车史：11/15「确认前不得透露驳回详情」被写成输出侧字符串替换才勉强兜住。

  I2 END_REQUIRES_OUTCOME
     进入 END 必须二选一：五要素齐全的真闭环，或已显式标记 non_task_end。
     翻车史：半截电话被算成成功工单，闭环率虚高。

  I3 STAGE_MONOTONIC
     五环节完成数不得无故减少；回退必须带显式重核标记（_name_challenged / reconfirm）。
     翻车史：「身份灯灭 → 从头重跑 S1→S2」把商家逼着全流程再答一遍。

  I4 ELEMENT_ORDER
     要素必须按顺序点亮：驳回原因早于整改动作，整改动作早于结束语。
     翻车史：「刚确认身份就 5/5 全亮」（进度条抢跑）。

  I5 REDLINE_NOT_BYPASSED
     红线命中时不得直接进入正常业务推进（S3/S4）；必须走 E5 / 纠正话术或显式收线。
     翻车史：商家提造假诉求被当成普通疑问继续问业务。

  I6 NO_ACTION_AFTER_FINISHED
     已结束（finished）的通话不得再推进业务环节；只能答疑或收线。
     翻车史：收尾后商家再开口，流程被硬拉回 S4 复读。

使用方式见 `veto()`；对现状的兼容性验证见 `_test_invariants.py`（跑真实脚本对话，
断言现有规则的每一次状态迁移都满足上述不变式 —— 这是把规则降级为否决器的前置证据）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# 状态常量（与 agent.py 保持一致；这里复制一份是为了让本模块能独立被测试，
# 不 import agent，避免循环依赖与「测试必须先起一个 Agent」的负担）
PRE = "PRE"
S0 = "S0-INTRODUCE"
S1 = "S1-VERIFY_PERSON"
S2 = "S2-REJECTION"
S3 = "S3-QA"
S4 = "S4-CONFIRM"
S5 = "S5-CLOSE"
E1 = "E1-INTERRUPT"
E2 = "E2-UNCLEAR"
E3 = "E3-EMOTIONAL"
E4 = "E4-OUT_OF_SCOPE"
E5 = "E5-VIOLATION"
END = "END"

# 披露段：进入这些状态说明要开始讲驳回详情了
DISCLOSING_STATES = (S2, S3, S4)
# 正常业务推进段（红线命中时不允许直接进）
BUSINESS_STATES = (S3, S4)
ELEMENT_ORDER = ("自报身份", "身份确认", "驳回原因", "整改动作", "结束语")

CODES = {
    "I1": ("NO_DISCLOSE_BEFORE_IDENTITY",
           "身份未确认不得进入驳回详情段（S2/S3/S4）"),
    "I2": ("END_REQUIRES_OUTCOME",
           "结束通话必须是有结论的闭环，或已显式标记为非任务结束"),
    "I3": ("STAGE_MONOTONIC",
           "五环节完成数不得无故减少；回退须带显式重核标记"),
    "I4": ("ELEMENT_ORDER",
           "必说要素必须按序点亮（驳回原因→整改动作→结束语）"),
    "I5": ("REDLINE_NOT_BYPASSED",
           "红线命中时不得直接进入正常业务推进段"),
    "I6": ("NO_ACTION_AFTER_FINISHED",
           "通话已结束后不得再推进业务环节"),
}


def snapshot(agent: Any) -> Dict[str, Any]:
    """把 Agent 当前状态拍成不变式校验需要的快照（只读，绝不改 Agent）。"""
    stages = 0
    try:
        stages = len([s for s in agent.flow_progress() if s.get("done")])
    except Exception:
        stages = len(getattr(agent, "done_elements", set()) or [])
    return {
        "state": getattr(agent, "state", PRE),
        "identity_confirmed": bool(getattr(agent, "identity_confirmed", False)),
        "inbound": bool(getattr(agent, "inbound", False)),
        "finished": bool(getattr(agent, "finished", False)),
        "non_task_end": bool(getattr(agent, "non_task_end", False)),
        "elements": set(getattr(agent, "done_elements", set()) or set()),
        "stages_done": stages,
        "redline_active": bool(getattr(agent, "_vio", None)),
        "reconfirm": bool(getattr(agent, "_name_challenged", False)),
        "close_reason": getattr(agent, "_close_reason", "") or "",
    }


def _missing_before(el: set, element: str) -> List[str]:
    """按 ELEMENT_ORDER，返回排在 element 之前却没点亮的要素。"""
    try:
        idx = ELEMENT_ORDER.index(element)
    except ValueError:
        return []
    return [e for e in ELEMENT_ORDER[:idx] if e not in el]


# 环节序号：仅用于判定「是不是在往回退」
STAGE_RANK = {PRE: 0, S0: 0, S1: 1, S2: 2, S3: 3, S4: 4, S5: 5, END: 5,
              E1: 1, E2: 2, E3: 5, E4: 5, E5: 2}


def check(prev: str, nxt: str, ctx: Optional[Dict[str, Any]] = None,
          after_elements: Optional[set] = None,
          after: Optional[Dict[str, Any]] = None) -> List[str]:
    """校验一次状态迁移。返回违反的不变式编号列表（空列表 = 通过）。

    参数
    ----
    prev / nxt      迁移前后的状态
    ctx             `snapshot()` 在回合**开始前**拍下的快照
    after_elements  迁移完成后预计点亮的要素集合
    after           迁移完成**之后**的事实快照（identity_confirmed / non_task_end /
                    close_reason / elements）。这一层必不可少：本轮的回复本身就在确立
                    「身份确认」「结束语」这些事实，只看迁前快照会把每一次合法确认
                    都误判成违规（实测 7 处全是这种时序误伤）。

    判定取「迁后事实优先、缺省回落到迁前快照」。
    """
    ctx = ctx or {}
    after = after or {}
    bad: List[str] = []

    def _post(key: str, default: Any = None) -> Any:
        return after[key] if key in after else ctx.get(key, default)

    identity_ok = bool(_post("identity_confirmed")) or bool(ctx.get("inbound"))
    redline = bool(ctx.get("redline_active"))
    finished_before = bool(ctx.get("finished"))
    el_before = set(ctx.get("elements") or set())
    if after_elements is not None:
        el_after = set(after_elements)
    else:
        el_after = set(_post("elements") or el_before)

    # I1 身份未确认不得披露
    if nxt in DISCLOSING_STATES and not identity_ok:
        bad.append("I1")

    # I2 结束必须有结论
    if nxt == END and prev != END:
        closed = all(e in el_after for e in ELEMENT_ORDER)
        has_outcome = bool(_post("non_task_end")) or bool(_post("close_reason"))
        if not (closed or has_outcome):
            bad.append("I2")

    # I3 环节不得无故往回退（异常态 E1/E2 本身就会短暂回退，豁免）
    if (nxt not in (END, E1, E2) and prev not in (PRE, END)
            and STAGE_RANK.get(nxt, -1) < STAGE_RANK.get(prev, -1)
            and not ctx.get("reconfirm")):
        bad.append("I3")

    # I4 要素按序点亮
    for e in el_after - el_before:
        if _missing_before(el_after, e):
            bad.append("I4")
            break

    # I5 红线命中不得直接进正常业务推进
    if redline and nxt in BUSINESS_STATES and prev not in BUSINESS_STATES:
        bad.append("I5")

    # I6 结束后不得再**推进**业务（回到 S3 继续答疑是允许的：收尾后商家又提问）
    if finished_before and nxt in (S4, S5):
        bad.append("I6")

    seen, out = set(), []
    for c in bad:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def describe(codes: List[str]) -> List[str]:
    """把编号翻成人话（日志/工单/答辩都用得上）。"""
    return [f"{c} {CODES.get(c, ('?', '未知不变式'))[0]}：{CODES.get(c, ('?', ''))[1]}"
            for c in codes]


def veto(proposed: str, fallback: str, ctx: Optional[Dict[str, Any]] = None,
         after_elements: Optional[set] = None,
         after: Optional[Dict[str, Any]] = None) -> Tuple[str, List[str]]:
    """**否决器本体**：模型提议的状态违反不变式时，退回安全状态。

    返回 (最终采用的状态, 违反的不变式编号)。这是"规则降级为否决器"的核心 API：
      · 通过 → 采用模型提议（规则不再抢决策权）；
      · 违反 → 先退到 fallback（通常是 _route 算出的状态）；
      · **fallback 本身也违反** → 再退到"原地不动"（prev_state）并记录两条违规。
        这一层很重要：否决器不能把控制权交给另一个非法状态，
        否则"规则先判"时期的 bug 会被原样继承下来。
    """
    ctx = ctx or {}
    prev = ctx.get("state", PRE)
    bad = check(prev, proposed, ctx, after_elements, after)
    if not bad:
        return proposed, []
    # 兜底不能是同一个非法提议：那样等于换个名字继续违规
    if fallback != proposed:
        bad_fb = check(prev, fallback, ctx, after_elements, after)
        if not bad_fb:
            return fallback, bad
    # 兜底也非法 → 停在本环节（最保守，且不会违反 I1/I2/I5/I6）
    bad_prev = check(prev, prev, ctx, after_elements, after)
    if not bad_prev:
        return prev, bad + ["FALLBACK_UNSAFE"]
    return prev, bad + ["FALLBACK_UNSAFE", "STAY_UNSAFE"]
