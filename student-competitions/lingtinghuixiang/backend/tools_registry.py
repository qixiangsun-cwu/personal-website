# -*- coding: utf-8 -*-
"""实时工具调用（Function Calling）—— 数字员工在通话中「自己决定去查」。

一句话：模型不再只是把话术说出来，它能主动说「我要查一下这家商家的工单」，
由本模块真的去 SQLite 里查，再把结果回注给模型，它据此回答具体数字。
调用留痕写入 work_order_events（kind='tool'），运营端处理轨迹可见。

三条硬约束（改这里之前先读）：

  1. **只读**。三个工具全部是查询，没有任何写操作 —— 最坏结果是「查不到」，
     绝不会把工单状态、商家资料写坏；写操作（约定回拨 / 转人工）留给第二版，
     且必须二次确认。
  2. **会话域受限**。工具绑定在当前通话的 merchant_id 上，模型只需表达「查什么」，
     不需要知道公司名；即使模型编错关键词，也只可能查到当前商家自己的数据，
     不存在跨商家越权。
  3. **不入关键路径**。决策在投机预生成（SpeculativeEngine）的后台线程里执行，
     超时 / 异常一律 fail-open（放弃工具、走原链路），绝不阻塞播报。

开关：环境变量 ``TOOL_CALL=off`` 一键退回纯状态机，行为与改动前完全一致。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

import db
import orders
import prompts          # 工具描述取自提示词层（RSI「工具调用策略」通道可补丁）

# ---------------------------------------------------------------- 配置
_DEFAULT_TIMEOUT_MS = 1600      # 单次决策硬超时；实测 glm-4-flash 预热后 640~1140ms
_MAX_CALLS_PER_ROUND = 2        # 一轮最多执行几个工具（防模型贪心）
MAX_TIMEOUTS = 2                # 连续超时熔断：>=2 次后本通电话不再走工具


def enabled() -> bool:
    """工具调用总开关（每次调用时读取，便于热改 .env 后重启生效）。"""
    return os.getenv("TOOL_CALL", "on").strip().lower() not in ("off", "0", "false", "no")


def timeout_s() -> float:
    try:
        return max(0.3, float(os.getenv("TOOL_TIMEOUT_MS", str(_DEFAULT_TIMEOUT_MS))) / 1000.0)
    except ValueError:
        return _DEFAULT_TIMEOUT_MS / 1000.0


# ---------------------------------------------------------------- 工具清单
# 参数刻意保持极简：通话链路上每多一个字段，每轮都要多付 token 与首字延迟。
TOOLS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "query_order_status",
            "description": prompts.tool_desc_of(
                "query_order_status",
                "查询当前通话商家的资质整改工单：状态、整改进度 x/5、"
                "累计通话与回拨次数、已提交材料、上次通话结论、下一步动作。"
                "商家问「审核到哪一步 / 我上次不是交过了 / 还要补什么」时调用。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "aspect": {
                        "type": "string",
                        "enum": ["progress", "material", "history", "all"],
                        "description": "关注点：进度 / 材料 / 历史通话 / 全部（默认 all）",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_merchant_profile",
            "description": prompts.tool_desc_of(
                "query_merchant_profile",
                "查询当前商家的资质档案：品牌、驳回材料、驳回原因、整改建议、"
                "关单方式、申请单号、联系人与电话。"
                "商家问「你们要我补哪个材料 / 我的驳回原因是什么」时调用。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "description": "可选，只取某一项：brand / material / reason / advice / sn / contact",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": prompts.tool_desc_of(
                "search_knowledge",
                "检索京东资质审核政策知识库（类目规则、商标/授权书要求、材料规范）。"
                "商家问政策含义、规则细节、为什么这样要求时调用。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词，取商家问题的核心词"}
                },
                "required": ["query"],
            },
        },
    },
]

TOOL_LABELS = {
    "query_order_status": "查询工单状态",
    "query_merchant_profile": "查询商家档案",
    "search_knowledge": "检索政策知识库",
}

# ---------------------------------------------------------------- 门控
# 只有命中这些词才允许带 tools：未命中的轮次与改动前逐字节一致，0 额外开销。
_GATE_KW = (
    "上次", "之前", "已经交", "交过", "提交过", "进度", "到哪", "审核到", "工单", "状态",
    "材料", "要补", "补什么", "改什么", "怎么改", "怎么办", "为什么", "商标", "授权", "资质",
    "政策", "规定", "要求", "时限", "多久", "什么时候", "记录", "第几",
)
_GATE_RE = (r"第\s*\d+\s*类", r"能(不能)?过", r"过没过", r"通没通过")

# 这些状态/意图不需要查数据（寒暄、结束、异常分支），直接跳过，省一次往返。
# 注意：S4 不在此列 —— 「都说明白了那你再给我讲讲要补什么」这类追问常被打到 S4，
# 靠 intent=CLOSING 兜住真正的收尾轮即可，否则该查的时候没查，答得反而更空。
_BLOCK_STATES = {
    "PRE", "END",
    "E1-INTERRUPT", "E2-UNCLEAR", "E3-EMOTIONAL", "E4-OUT_OF_SCOPE",
    "E5-VIOLATION",   # 11/12 红线轮只播企标话术，不查数据
}
_BLOCK_INTENTS = {"CLOSING", "COMPLETED", "NOT_CONTACT", "SERVICE_ERROR", "ASR_UNCLEAR",
                  "VIOLATION", "VIOLATION_WARN"}


def should_use(text: str, prompt_state: str = "", intent: str = "") -> bool:
    """规则门控：这一轮值不值得多花一次决策往返。"""
    t = (text or "").strip()
    if len(t) < 5:
        return False
    if intent in _BLOCK_INTENTS or prompt_state in _BLOCK_STATES:
        return False
    if any(k in t for k in _GATE_KW):
        return True
    return any(re.search(p, t) for p in _GATE_RE)


# ---------------------------------------------------------------- 统计
_STATS_LOCK = threading.Lock()
_STATS = {"rounds": 0, "calls": 0, "ok": 0, "fail": 0, "timeout": 0,
          "skipped": 0, "gate_pass": 0, "decide_ms": [], "exec_ms": []}


def _bump(key: str, n: int = 1) -> None:
    with _STATS_LOCK:
        _STATS[key] = _STATS.get(key, 0) + n


def _push(key: str, v: float) -> None:
    with _STATS_LOCK:
        arr = _STATS.setdefault(key, [])
        arr.append(round(v, 1))
        if len(arr) > 200:
            del arr[:len(arr) - 200]


def _pct(arr: List[float], p: float) -> float:
    if not arr:
        return 0.0
    s = sorted(arr)
    idx = min(len(s) - 1, max(0, int(round((len(s) - 1) * p))))
    return round(s[idx], 1)


def stats() -> dict:
    """内存态统计（本次服务进程启动以来），供 /api/tools 与指标看板。"""
    with _STATS_LOCK:
        d = {k: (list(v) if isinstance(v, list) else v) for k, v in _STATS.items()}
    decide, exec_ms = d["decide_ms"], d["exec_ms"]
    calls = d["calls"]
    return {
        "enabled": enabled(),
        "rounds": d["rounds"],
        "gate_pass": d["gate_pass"],
        "calls": calls,
        "ok": d["ok"],
        "fail": d["fail"],
        "timeout": d["timeout"],
        "skipped": d["skipped"],
        "success_rate": round(d["ok"] / calls * 100, 1) if calls else 0.0,
        "decide_p50_ms": _pct(decide, 0.5),
        "decide_p95_ms": _pct(decide, 0.95),
        "exec_p50_ms": _pct(exec_ms, 0.5),
        "timeout_ms": int(timeout_s() * 1000),
    }


def db_stats() -> dict:
    """落库统计（跨重启）：tool 轨迹条数 / 涉及通话数。"""
    try:
        row = db.query_one("SELECT COUNT(*) AS n, COUNT(DISTINCT ref) AS c "
                           "FROM work_order_events WHERE kind='tool'")
        return {"events": (row or {}).get("n", 0) or 0,
                "calls": (row or {}).get("c", 0) or 0}
    except Exception:
        return {"events": 0, "calls": 0}


# ---------------------------------------------------------------- 工具实现（全部只读）
def _ctx_order(ctx: dict) -> Optional[dict]:
    mid = ctx.get("merchant_id")
    if mid in (None, ""):
        return None
    return db.query_one("SELECT * FROM work_orders WHERE merchant_id=?", (mid,))


def _kv(rows: List[tuple]) -> str:
    return "；".join(f"{k}：{v}" for k, v in rows if v not in (None, "", 0) or v == 0)


def _tool_order_status(args: dict, ctx: dict) -> dict:
    o = _ctx_order(ctx)
    if not o:
        return {"ok": False, "error": "未找到该商家的工单",
                "title": "查询工单状态 → 无工单", "summary": "该商家尚无工单记录",
                "evidence": "系统未查到该商家的整改工单，回答时不要编造进度。"}
    # 11/15：工单进度与运营端 / 商家接听端共用同一份 flow5，避免 AI 报的
    # x/5 和看板显示的 x/5 对不上（旧口径这里按五要素另算一套分）。
    # 【11/22】通话中优先用 live flow5；若工单历史状态是"已闭环"，本通其实在
    # 进行中，展示为"跟进中（本轮通话）"，避免出现"已闭环 + 3/5"的自相矛盾。
    live = orders._live_flow5(o["merchant_id"])
    f5 = live if live else orders.order_flow5(o["merchant_id"], o.get("progress"))
    prog = orders.flow5_done(f5)
    stage = prog
    stage_name = orders.STAGES[min(max(prog, 0), 4)] if prog else "尚未开始"
    status_text = o["status"]
    if live and status_text in (orders.ST_CLOSED, orders.ST_WRITTEN):
        status_text = "跟进中（本轮通话）"
    aspect = (args or {}).get("aspect") or "all"
    data = {
        "order_id": o["order_id"], "status": status_text, "progress": prog,
        "stage": stage_name, "call_count": o["call_count"], "callback_count": o["callback_count"],
        "last_summary": o["last_summary"] or "", "next_action": o["next_action"] or "",
        "due_at": o["due_at"] or "", "material": o["material"] or "",
        "reason": o["reason"] or "", "owner": o["owner"] or "",
    }
    if aspect == "history":
        rows = db.query("SELECT direction,status,started_at FROM calls "
                        "WHERE merchant_id=? ORDER BY id DESC LIMIT 3", (ctx.get("merchant_id"),))
        data["recent_calls"] = [dict(r) for r in rows]
    parts: List[tuple] = [("工单", o["order_id"]), ("状态", status_text),
                          ("整改进度", f"{prog}/5（{stage_name}）")]
    if aspect in ("all", "material") and o["material"]:
        parts.append(("驳回材料", o["material"]))
    if aspect in ("all", "progress"):
        parts.append(("累计通话", f"{o['call_count'] or 0} 通"))
        parts.append(("回拨", f"{o['callback_count'] or 0} 次"))
        if o["next_action"]:
            parts.append(("下一步", o["next_action"]))
    if o["last_summary"]:
        parts.append(("上次通话结论", (o["last_summary"] or "")[:80]))
    ev = _kv(parts)
    return {
        "ok": True, "data": data,
        "title": f"查询工单状态 → {o['order_id']} · {prog}/5",
        "summary": f"{status_text} · {stage_name} · 通话 {o['call_count'] or 0} 通 / 回拨 {o['callback_count'] or 0} 次",
        "evidence": ev,
        "sources": ["work_orders", "calls"],
    }


def _tool_merchant_profile(args: dict, ctx: dict) -> dict:
    o = _ctx_order(ctx) or {}
    snap = {}
    try:
        snap = json.loads(o.get("snapshot") or "{}") or {}
    except Exception:
        snap = {}
    m = db.query_one("SELECT * FROM merchants WHERE id=?", (ctx.get("merchant_id"),)) or {}
    data = {
        "shop": o.get("shop") or m.get("shop_name") or "",
        "company": o.get("company") or m.get("company_name") or "",
        "brand": o.get("brand") or snap.get("brand") or "",
        "material": o.get("material") or snap.get("material") or "",
        "reason": o.get("reason") or snap.get("reason") or "",
        "advice": snap.get("advice") or "",
        "close_type": o.get("close_type") or snap.get("close_type") or "",
        "sn": snap.get("sn") or "",
        "contact": snap.get("contact") or m.get("contact_name") or "",
        "phone": snap.get("phone") or m.get("phone") or "",
    }
    field = (args or {}).get("field") or ""
    if field and field in data:
        shown = {field: data[field]}
    else:
        shown = data
    label = {"brand": "品牌", "material": "驳回材料", "reason": "驳回原因", "advice": "整改建议",
             "sn": "申请单号", "contact": "联系人", "phone": "联系电话",
             "shop": "店铺", "company": "公司", "close_type": "关单方式"}
    ev = _kv([(label.get(k, k), v) for k, v in shown.items()])
    return {
        "ok": True, "data": data,
        "title": f"查询商家档案 → {data.get('shop') or data.get('company') or '当前商家'}",
        "summary": " · ".join(x for x in (data.get("brand"), data.get("material"), data.get("close_type")) if x) or "档案已读取",
        "evidence": ev or "该商家档案暂无有效字段",
        "sources": ["merchants", "work_orders"],
    }


def _tool_knowledge(args: dict, ctx: dict) -> dict:
    q = ((args or {}).get("query") or "").strip() or (ctx.get("user_text") or "")
    hits = []
    try:
        hits = [h for h in db.search_knowledge(q, top_k=3) if h.get("source") != "merchant"]
    except Exception:
        hits = []
    if not hits:
        return {"ok": False, "error": "知识库无命中",
                "title": f"检索知识库 → 「{q[:12]}」无命中",
                "summary": "未命中，回答时按「以京麦后台驳回意见为准」兜底",
                "evidence": f"知识库未检索到「{q}」相关条目，回答时不要编造政策细节，"
                            "引导商家以京麦后台驳回意见为准，或拨打商家服务热线。",
                "sources": ["knowledge_chunks"]}
    lines = [f"- [{h['title'][:18]}] {(h['content'] or '')[:110]}" for h in hits]
    return {
        "ok": True, "data": {"query": q, "hits": [{"title": h["title"], "source": h["source"]} for h in hits]},
        "title": f"检索知识库 → 「{q[:12]}」命中 {len(hits)} 条",
        "summary": "；".join(h["title"][:14] for h in hits[:2]),
        "evidence": "\n".join(lines),
        "sources": ["knowledge_chunks"],
    }


_EXEC = {
    "query_order_status": _tool_order_status,
    "query_merchant_profile": _tool_merchant_profile,
    "search_knowledge": _tool_knowledge,
}


def execute(name: str, args: dict, ctx: dict) -> dict:
    """执行单个工具（只读），异常一律转成 ok=False 的结果，不向上抛。"""
    t0 = time.perf_counter()
    fn = _EXEC.get(name)
    if fn is None:
        return {"ok": False, "tool": name, "error": "未注册的工具", "title": f"未知工具 {name}",
                "summary": "已拒绝执行", "evidence": "", "args": args or {}, "ms": 0.0}
    try:
        res = fn(args or {}, ctx)
    except Exception as e:  # 查不到 / 表不存在 / 字段缺失都不该打断通话
        res = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:80]}",
               "title": f"{TOOL_LABELS.get(name, name)}失败", "summary": "查询异常，已跳过",
               "evidence": ""}
    ms = round((time.perf_counter() - t0) * 1000, 1)
    res.update({"tool": name, "name": TOOL_LABELS.get(name, name), "args": args or {}, "ms": ms})
    _push("exec_ms", ms)
    _bump("calls")
    _bump("ok" if res.get("ok") else "fail")
    return res


# ---------------------------------------------------------------- 决策（一次非流式往返）
def _is_timeout(err: Exception) -> bool:
    s = f"{type(err).__name__} {err}".lower()
    return any(k in s for k in ("timeout", "timed out", "readtimeout"))


def _decide(client, model: str, user_text: str, ctx: dict) -> dict:
    """让模型决定「这轮要不要查、查什么」。只吐 tool_calls，不产出播报文本。"""
    sys_p = ("你是京东商家资质审核外呼数字员工，正在与商家通话。"
             "商家的问题只要涉及他自己的工单进度、已提交材料、驳回原因、整改方式，"
             "或资质审核政策细节，你必须先调用工具查证再回答，不得凭猜测作答。"
             "若商家只是寒暄、确认、表示理解或要求转人工，则不要调用工具。"
             f"当前商家：{ctx.get('company') or '该商家'}（店铺 {ctx.get('shop') or '—'}，"
             f"驳回场景 {ctx.get('scenario') or '—'}）。")
    t0 = time.perf_counter()
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": sys_p},
                      {"role": "user", "content": user_text}],
            tools=TOOLS, tool_choice="auto", temperature=0.1, max_tokens=120,
            timeout=timeout_s())
    except Exception as e:
        ms = round((time.perf_counter() - t0) * 1000, 1)
        to = _is_timeout(e)
        if to:
            _bump("timeout")
        return {"calls": [], "ms": ms, "timeout": to, "error": f"{type(e).__name__}: {str(e)[:80]}"}
    ms = round((time.perf_counter() - t0) * 1000, 1)
    _push("decide_ms", ms)
    msg = r.choices[0].message if r.choices else None
    raw = (getattr(msg, "tool_calls", None) or []) if msg else []
    calls = []
    for tc in raw:
        fn = getattr(tc, "function", None)
        name = getattr(fn, "name", "") if fn else ""
        args_raw = getattr(fn, "arguments", "") if fn else ""
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) and args_raw.strip() else (args_raw or {})
        except Exception:
            args = {}
        if not isinstance(args, dict):
            args = {}
        calls.append({"id": getattr(tc, "id", ""), "name": name, "args": args})
    return {"calls": calls, "ms": ms, "timeout": False, "error": ""}


def _compose(records: List[dict]) -> str:
    if not records:
        return ""
    body = []
    for i, r in enumerate(records, 1):
        ev = (r.get("evidence") or "").strip()
        if not ev:
            continue
        body.append(f"{i}. {r.get('name')} → {ev}")
    if not body:
        return ""
    return ("【本轮工具查证结果 · 系统实时查询，可直接引用】\n" + "\n".join(body) +
            "\n把上述事实自然地讲给商家（可以引用具体进度、材料名、政策条目），"
            "不要念字段名，也不要编造结果之外的信息。")


def run(client, model: str, user_text: str, ctx: dict) -> dict:
    """一轮完整的工具往返：门控 → 决策 → 执行 → 组装证据。任何异常都 fail-open。"""
    t0 = time.perf_counter()
    _bump("rounds")
    if not should_use(user_text, ctx.get("prompt_state", ""), ctx.get("intent", "")):
        _bump("skipped")
        return {"records": [], "evidence": "", "skipped": True, "ms": 0.0,
                "timeout": False, "error": ""}
    _bump("gate_pass")
    try:
        d = _decide(client, model, user_text, ctx)
        records: List[dict] = []
        for c in (d.get("calls") or [])[:_MAX_CALLS_PER_ROUND]:
            if c["name"] in _EXEC:
                records.append(execute(c["name"], c["args"], ctx))
        return {"records": records, "evidence": _compose(records), "skipped": False,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "timeout": d.get("timeout", False), "error": d.get("error", "")}
    except Exception as e:      # 兜底：工具链任何问题都不影响通话
        return {"records": [], "evidence": "", "skipped": False,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "timeout": False, "error": f"{type(e).__name__}: {str(e)[:80]}"}


# ---------------------------------------------------------------- 留痕
def record_events(call_id: str, merchant_id: Any, records: List[dict]) -> int:
    """把工具调用写进工单处理轨迹（kind='tool'），供运营端审计。"""
    if not records:
        return 0
    o = db.query_one("SELECT order_id FROM work_orders WHERE merchant_id=?", (merchant_id,))
    if not o:
        return 0
    n = 0
    for r in records:
        try:
            orders.add_event(
                o["order_id"], "tool", r.get("title") or r.get("name", "工具调用"),
                detail=r.get("summary") or r.get("error", ""),
                actor="数字员工", ref=call_id,
                payload={"tool": r.get("tool"), "args": r.get("args"), "ok": r.get("ok"),
                         "ms": r.get("ms"), "error": r.get("error", ""),
                         "data": (r.get("data") or {}) if isinstance(r.get("data"), dict) else {}})
            n += 1
        except Exception:
            continue
    return n


__all__ = ["TOOLS", "TOOL_LABELS", "enabled", "timeout_s", "should_use", "execute",
           "run", "stats", "db_stats", "record_events", "MAX_TIMEOUTS"]
