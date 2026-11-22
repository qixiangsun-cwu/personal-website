# -*- coding: utf-8 -*-
"""工单域（work_orders / work_order_events）。

运营端「工单看板」的唯一数据源，与通话域（calls / call_turns / tickets）解耦：

  work_orders        一张工单 = 一个商家的一次资质整改任务
                     24 列，四组：标识/商家 · 状态/进度 · 联系记录 · 时间戳
  work_order_events  处理轨迹：created / call / callback / closed / escalate
                     / schedule / note / edit / reopen / assign

状态机：待跟进 → 跟进中 → 已闭环 → 已回写
                       └→ 已升级（主管介入后可重新打开回到跟进中）

挂机自动回填：sync_calls() 扫描 calls 中已结束的通话，把最近结论写回
last_summary / call_count / progress，并补一条 call 轨迹。
"""

from __future__ import annotations

import json
import uuid
import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

import db
import risk
import flags as call_marks  # 11/15 第七轮：与通话域共用同一套标记判定

router = APIRouter(prefix="/api/orders", tags=["orders"])

# 11/15：运营端「整改进度」改为与商家接听端 / 实时转写看板**完全同一套五环节**。
# 旧 STAGES 是运营动作口径（身份确认 / 原因告知 / 整改指导 / 意愿确认 / 关单回写），
# 且 progress 按旧的五要素（身份确认+驳回原因+整改动作+意愿确认+结束语）另算一套分，
# 导致真机上「商家端 4/5、运营端却显示另一组阶段名」。
# 唯一真相源 = agent.STAGES_5 / agent.flow_progress()；这里延迟导入避免
# orders ↔ server 循环依赖，导入失败时用同序同名的常量兜底。
_FLOW5_FALLBACK = ["自我介绍", "商家身份确认", "情况说明", "商家答疑", "整改意愿确认"]


def stages5() -> list:
    """返回五环节名称（顺序、文案与商家接听端 STAGES_5 严格一致）。"""
    try:
        from agent import STAGES_5
        names = [s["name"] for s in STAGES_5]
        if len(names) == 5 and all(names):
            return names
    except Exception:
        pass
    return list(_FLOW5_FALLBACK)


STAGES = stages5()

ST_PENDING = "待跟进"
ST_ACTIVE = "跟进中"
ST_CLOSED = "已闭环"
ST_ESCALATED = "已升级"
ST_WRITTEN = "已回写"

ALL_STATUS = [ST_PENDING, ST_ACTIVE, ST_CLOSED, ST_ESCALATED, ST_WRITTEN]

EVENT_KINDS = {
    "created":  {"label": "工单创建", "glyph": "＋"},
    "call":     {"label": "通话",     "glyph": "☎"},
    "callback": {"label": "发起回拨", "glyph": "↻"},
    "closed":   {"label": "闭环",     "glyph": "✓"},
    "escalate": {"label": "升级主管", "glyph": "▲"},
    "schedule": {"label": "约定回拨", "glyph": "◷"},
    "note":     {"label": "运营备注", "glyph": "✎"},
    "edit":     {"label": "商家资料更新", "glyph": "✐"},
    "reopen":   {"label": "重新打开", "glyph": "⟲"},
    "assign":   {"label": "指派跟进人", "glyph": "☺"},
    # 11/12 工具调用（Function Calling）：数字员工在通话中自己查了工单 / 档案 / 知识库
    "tool":     {"label": "工具调用", "glyph": "⚙"},
    # 11/12 红线拦截：商家提出违规诉求，数字员工按企标拒绝并留痕
    "violation": {"label": "红线拦截", "glyph": "⛒"},
    # 11/12 商家风险标记：B 级违规沉淀成商家维度的长期标记
    "risk":      {"label": "风险标记", "glyph": "⚠"},
    # 11/15 功能补丁：业务外闲聊（分级处置留痕，不计红线 KPI）
    "offtopic":  {"label": "闲聊警告", "glyph": "💬"},
    # 11/15 功能补丁：非违规特殊标记（反复找茬 / 不信任·转人工 等）
    "flag":      {"label": "特殊标记", "glyph": "🏷"},
}


# ---------------------------------------------------------------- 基础工具
def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ago(minutes: int = 0) -> str:
    return (datetime.datetime.now() - datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def _j(v: Any, default: Any = None) -> Any:
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return default


# ---------------------------------------------------------------- 建表
def ensure_schema() -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS work_orders(
            order_id       TEXT PRIMARY KEY,
            merchant_id    INTEGER UNIQUE,
            shop           TEXT DEFAULT '',
            company        TEXT DEFAULT '',
            brand          TEXT DEFAULT '',
            material       TEXT DEFAULT '',
            reason         TEXT DEFAULT '',
            close_type     TEXT DEFAULT '',
            snapshot       TEXT DEFAULT '{}',
            status         TEXT DEFAULT '待跟进',
            stage          INTEGER DEFAULT 0,
            progress       INTEGER DEFAULT 0,
            priority       TEXT DEFAULT 'normal',
            owner          TEXT DEFAULT '',
            call_count     INTEGER DEFAULT 0,
            callback_count INTEGER DEFAULT 0,
            last_call_at   TEXT,
            last_call_id   TEXT,
            last_summary   TEXT DEFAULT '',
            next_action    TEXT DEFAULT '',
            due_at         TEXT,
            note           TEXT DEFAULT '',
            created_at     TEXT,
            updated_at     TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS work_order_events(
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id   TEXT NOT NULL,
            kind       TEXT NOT NULL,
            title      TEXT DEFAULT '',
            detail     TEXT DEFAULT '',
            actor      TEXT DEFAULT '系统',
            ref        TEXT DEFAULT '',
            payload    TEXT DEFAULT '{}',
            created_at TEXT
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_wo_events ON work_order_events(order_id, id)")


def add_event(order_id: str, kind: str, title: str, detail: str = "",
              actor: str = "系统", ref: str = "", payload: Optional[dict] = None,
              created_at: Optional[str] = None) -> int:
    return db.execute(
        "INSERT INTO work_order_events(order_id,kind,title,detail,actor,ref,payload,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (order_id, kind, title, detail, actor, ref,
         json.dumps(payload or {}, ensure_ascii=False), created_at or _now()))


def record_violations(call_id: str, merchant_id: Any, items: list) -> int:
    """把商家侧的违规诉求写进工单处理轨迹（kind='violation'），供运营/质检审计。

    与工具调用留痕同一套写法：payload 里存命中的词、所属红线类目、对应企业 Excel
    条款、处置动作，答辩时可以直接展开给评委看「这句话为什么被拦」。

    11/12 B 级追加两件事（不只是记一笔就完了）：
    * 给商家打**特殊标记**（risk.mark）→ 下次外呼时 Agent 读得到，边界自动收紧；
    * 工单优先级提到 high，并补一条 kind='risk' 轨迹，运营一眼能看到「这家重点」。
    """
    if not items:
        return 0
    o = db.query_one("SELECT order_id, priority FROM work_orders WHERE merchant_id=?", (merchant_id,))
    if not o:
        return 0
    n = 0
    BR = {"A": "商家已认错", "B": "换了一种违规", "B2": "同一违规再次出现",
          "C": "🚨 严重违规 · 转人工"}
    for v in items:
        try:
            branch = v.get("branch") or ""
            label = BR.get(branch, "已教育 · 拉回主线")
            # 触发位置写成「第 N 环节之后」，与进度条上警告卡的位置严格一致
            pos = f"第{v.get('done_before') or 0}环节之后"
            add_event(
                o["order_id"], "violation",
                f"红线拦截 · {v.get('name', '违规诉求')}（{label}）",
                detail=(f"商家原话「{v.get('text', '')}」触发于{pos}"
                        f"（第{v.get('ts') or 0}秒）→ AI 已暂停业务纠正："
                        f"{(v.get('say') or v.get('reply') or '').rstrip('。；; ')[:80]}"
                        f"{('；' + v.get('outcome')) if v.get('outcome') else ''}"),
                actor="数字员工", ref=call_id,
                payload={"key": v.get("key"), "name": v.get("name"),
                         "level": v.get("level"), "phrase": v.get("phrase"),
                         "how": v.get("how"), "excel": v.get("excel"),
                         "action": v.get("action"), "text": v.get("text", ""),
                         "say": v.get("say", ""), "guide": v.get("guide", ""),
                         "close": v.get("close", ""), "tag": v.get("tag", ""),
                         "reply": v.get("reply", ""),
                         # 13/13 三分支复盘字段（工单看板 / 历史记录直接消费）
                         "seq": v.get("seq"),
                         "branch": branch, "severe": bool(v.get("severe")),
                         "count": v.get("count"), "stage5": v.get("stage5"),
                         "done_before": v.get("done_before"), "ts": v.get("ts"),
                         "ack": bool(v.get("ack")), "ack_text": v.get("ack_text", ""),
                         "outcome": v.get("outcome", "")})
            n += 1
            # ---- B 级类目 / 严重违规：商家特殊标记 + 工单升级 ----
            if v.get("level") == "B" or v.get("severe"):
                rk = risk.mark(merchant_id, v, call_id, v.get("text", ""))
                if rk:
                    add_event(
                        o["order_id"], "risk",
                        f"商家风险标记 · {rk['level_name']}",
                        detail=(f"标签：{'、'.join(rk['tags']) or v.get('name', '')}；"
                                f"累计 B 级 {rk['b_count']} 次；"
                                f"最近证据「{rk['last_text']}」→ 后续外呼自动收紧边界，"
                                f"需 {rk['level_desc']}"),
                        actor="数字员工", ref=call_id,
                        payload=rk)
                    if (o.get("priority") or "normal") != "high":
                        db.execute("UPDATE work_orders SET priority='high', updated_at=? "
                                   "WHERE order_id=?", (_now(), o["order_id"]))
        except Exception:
            continue
    return n


# 11/15 功能补丁：非违规特殊标记的展示口径（标题模板按 kind 区分）
_FLAG_LABELS = {
    "distrust": ("不信任 · 转人工", "商家明确表示不信任，第 {count} 次后转人工收线"),
    "pushback": ("反复找茬 · 收线", "拒绝整改 / 质疑类表态累计第 {count} 次，3 次礼貌收线"),
    "offtopic": ("闲聊 · 分级处置", "业务外闲聊第 {count} 次（1~2 拉回 / 3 警告 / 4 收线）"),
    # 11/15 真机修复⑥：商家全时无空 → 已启动转人工（与不信任转人工同一留痕口径）
    "no_day_free": ("全时无空 · 转人工", "商家表示哪一天都没空，已登记转人工专员另约时间跟进"),
}


def record_flags(call_id: str, merchant_id: Any, items: list) -> int:
    """把特殊标记（不信任 / 找茬）与闲聊分级事件写进工单轨迹（11/15 功能补丁）。

    与 record_violations 同一套写法，但 kind 用 'flag' / 'offtopic'：
    不进红线 KPI、不打商家风险标记，只在工单轨迹与看板徽标上展示。
    """
    if not items:
        return 0
    o = db.query_one("SELECT order_id FROM work_orders WHERE merchant_id=?", (merchant_id,))
    if not o:
        return 0
    n = 0
    for f in items:
        try:
            kind = f.get("kind") or "flag"
            label, tpl = _FLAG_LABELS.get(kind, ("特殊标记", "第 {count} 次"))
            add_event(
                o["order_id"], "offtopic" if kind == "offtopic" else "flag",
                f"{label}（第 {f.get('count') or 1} 次）",
                detail=(tpl.format(count=f.get("count") or 1)
                        + f"；商家原话「{(f.get('text') or '')[:40]}」"),
                actor="数字员工", ref=call_id,
                payload={"flag_kind": kind, "count": f.get("count"),
                         "ftype": f.get("ftype", ""), "text": f.get("text", "")})
            n += 1
        except Exception:
            continue
    return n


def sync_violations(call_id: str, items: list) -> int:
    """通话收尾：用最终的 violation_log 回填工单轨迹里每一条红线事件的处置结果。

    逐轮写入时只能记到「当轮」的状态（商家还没表态）；认错 / 换违规 / 屡教不改
    都发生在后续轮次，所以收尾时按 seq 覆盖一次，保证工单复盘与通话结果完全一致。
    """
    if not items:
        return 0
    try:
        rows = db.query("SELECT id,payload FROM work_order_events "
                        "WHERE kind='violation' AND ref=?", (call_id,))
    except Exception:
        return 0
    by_seq = {}
    for r in rows:
        try:
            p = json.loads(r.get("payload") or "{}")
        except Exception:
            p = {}
        if p.get("seq") is not None:
            by_seq[p["seq"]] = (r["id"], p)
    BR = {"A": "商家已认错", "B": "换了一种违规", "B2": "同一违规再次出现",
          "C": "🚨 严重违规 · 转人工"}
    n = 0
    for v in items:
        hit = by_seq.get(v.get("seq"))
        if not hit:
            continue
        rid, p = hit
        p.update({"branch": v.get("branch") or "", "severe": bool(v.get("severe")),
                  "ack": bool(v.get("ack")), "ack_text": v.get("ack_text") or "",
                  "outcome": v.get("outcome") or "", "count": v.get("count")})
        label = BR.get(v.get("branch") or "", "已教育 · 拉回主线")
        title = f"红线拦截 · {v.get('name') or '违规诉求'}（{label}）"
        detail = (f"商家原话「{v.get('text', '')}」触发于第{v.get('done_before') or 0}环节之后"
                  f"（第{v.get('ts') or 0}秒）→ AI 已暂停业务纠正："
                  f"{(v.get('say') or v.get('reply') or '').rstrip('。；; ')[:80]}"
                  f"{('；' + v.get('outcome')) if v.get('outcome') else ''}")
        try:
            db.execute("UPDATE work_order_events SET title=?, detail=?, payload=? WHERE id=?",
                       (title, detail, json.dumps(p, ensure_ascii=False), rid))
            n += 1
        except Exception:
            continue
    return n


def _touch(order_id: str) -> None:
    db.execute("UPDATE work_orders SET updated_at=? WHERE order_id=?", (_now(), order_id))


def _snapshot(order_id: str) -> dict:
    r = db.query_one("SELECT * FROM work_orders WHERE order_id=?", (order_id,))
    if not r:
        return {}
    return {
        "merchant_id": r["merchant_id"],
        "company": r["company"],
        "shop": r["shop"],
        "brand": r["brand"],
        "material": r["material"],
        "reason": r["reason"],
        "close_type": r["close_type"],
        "owner": r["owner"],
        "snapshot_at": _now(),
    }


# ---------------------------------------------------------------- 种子数据
OWNERS = ["张岚", "李哲", "王敏", "周舟"]


def _merchant_meta(m: dict) -> dict:
    extra = _j(m.get("extra_json"), {}) or {}
    reason = extra.get("驳回场景") or ""
    scen = db.query_one(
        "SELECT * FROM scenarios WHERE level2_category=? ORDER BY id LIMIT 1", (reason,))
    if not scen and reason:
        scen = db.query_one(
            "SELECT * FROM scenarios WHERE level2_category LIKE ? OR level2_category LIKE ? LIMIT 1",
            (f"%{reason[:4]}%", f"%{reason[-4:]}%"))
    brand = extra.get("品牌") or (m.get("shop_name") or "").replace("旗舰店", "").replace("专营店", "").replace("专卖店", "")
    reason = reason or (scen or {}).get("level2_category") or ""
    material = (scen or {}).get("level1_category") or ""
    if not material:
        # 场景库未命中时按关键词兜底，保证商家信息不留空
        for kw, val in [("商标", "商标注册证"), ("授权", "品牌授权书"),
                        ("质检", "质检报告"), ("生产", "生产许可证"), ("资质", "资质证照")]:
            if kw in reason:
                material = val
                break
    advice = f"按「{reason or '驳回要求'}」补正{material or '相关资质材料'}，加盖章后重新提交审核"
    return {
        "shop": m.get("shop_name") or "",
        "company": m.get("company_name") or "",
        "brand": brand,
        "material": material,
        "reason": reason,
        "close_type": (scen or {}).get("close_method") or "电话",
        "sn": extra.get("资审单号", ""),
        "contact": m.get("contact_name") or "",
        "phone": m.get("phone") or "",
        "advice": advice,
    }


# 演示状态编排：让五种工单状态在队列里都能被看到
DEMO_PLAN = {
    1: dict(status=ST_ACTIVE, progress=3, priority="high", owner="张岚",
            next_action="回访确认商家是否已重新提交第 21 类商标", due_min=-45),
    2: dict(status=ST_ACTIVE, progress=2, priority="normal", owner="李哲",
            next_action="补齐授权书法人签字页后再次外呼确认", due_min=-180),
    3: dict(status=ST_ESCALATED, progress=2, priority="high", owner="王敏",
            next_action="主管复核商标注册时间是否可按新规豁免", due_min=-20),
    4: dict(status=ST_PENDING, progress=0, priority="normal", owner="周舟",
            next_action="首次触达：告知授权链路缺少上游授权文件", due_min=-240),
    5: dict(status=ST_WRITTEN, progress=5, priority="normal", owner="周舟",
            next_action="", due_min=0),
}
# 少数商家走书面驳回（无需电话），用于展示京麦回写口径
CLOSE_TYPE_OVERRIDE = {5: "京麦驳回"}


def seed(force: bool = False) -> int:
    """为每个商家建一张工单（merchant_id 唯一）。已存在则跳过。"""
    ensure_schema()
    made = 0
    for m in db.query("SELECT * FROM merchants ORDER BY id"):
        mid = m["id"]
        if db.query_one("SELECT order_id FROM work_orders WHERE merchant_id=?", (mid,)):
            if not force:
                continue
            db.execute("DELETE FROM work_orders WHERE merchant_id=?", (mid,))
            db.execute("DELETE FROM work_order_events WHERE order_id NOT IN (SELECT order_id FROM work_orders)")
        meta = _merchant_meta(m)
        if mid in CLOSE_TYPE_OVERRIDE:
            meta["close_type"] = CLOSE_TYPE_OVERRIDE[mid]
        plan = DEMO_PLAN.get(mid, dict(status=ST_PENDING, progress=0, priority="normal",
                                       owner=OWNERS[mid % len(OWNERS)], next_action="首次触达", due_min=-120))
        oid = "WO-" + datetime.datetime.now().strftime("%y%m%d") + "-" + f"{mid:03d}"
        stat = db.query("SELECT * FROM calls WHERE merchant_id=? ORDER BY id DESC", (mid,))
        call_n = len(stat)
        cb_n = max(0, min(6, call_n // 5)) if call_n else 0
        last = stat[0] if stat else None
        summary = ""
        if last:
            res = _j(last.get("result_json"), {}) or {}
            resolved = res.get("resolved") or []
            pending = res.get("pending") or []
            bits = []
            if resolved:
                bits.append("已说明：" + "；".join(str(x) for x in resolved[:2]))
            if pending:
                bits.append("待补：" + "；".join(str(x) for x in pending[:2]))
            summary = " ｜ ".join(bits) or (res.get("stop_stage") and f"停在「{res['stop_stage']}」") or ""
        due = _ago(-plan["due_min"]) if plan["due_min"] else None

        db.execute(
            "INSERT INTO work_orders(order_id,merchant_id,shop,company,brand,material,reason,close_type,"
            "snapshot,status,stage,progress,priority,owner,call_count,callback_count,last_call_at,"
            "last_call_id,last_summary,next_action,due_at,note,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, mid, meta["shop"], meta["company"], meta["brand"], meta["material"], meta["reason"],
             meta["close_type"], json.dumps({**meta, "merchant_id": mid}, ensure_ascii=False),
             plan["status"], plan["progress"], plan["progress"], plan["priority"], plan["owner"],
             call_n, cb_n, (last or {}).get("started_at") or (last or {}).get("created_at"),
             (last or {}).get("call_id"), summary, plan["next_action"], due, "",
             _ago(60 * 26 + mid * 37), _now()))

        # 轨迹
        add_event(oid, "created", "工单创建",
                  f"{meta['shop']} · {meta['reason'] or '资质整改'} · 关单方式 {meta['close_type']}",
                  actor="系统", ref=f"merchant:{mid}", created_at=_ago(60 * 26 + mid * 37))
        for c in stat[:3]:
            res = _j(c.get("result_json"), {}) or {}
            stop = c.get("stop_stage") or ("已完成" if c.get("closed_loop") else "")
            add_event(oid, "call", f"第 {len(stat) - stat.index(c)} 通外呼 · {'闭环' if c.get('closed_loop') else '未闭环'}",
                      f"{'五要素齐全' if c.get('closed_loop') else ('停在：' + str(stop) if stop else '未记录停点')}"
                      f" · 时长 {_dur(c.get('started_at'), c.get('ended_at'))}",
                      actor="数字员工", ref=c.get("call_id") or "",
                      payload={"closed_loop": c.get("closed_loop"), "stop_stage": stop,
                               "turns": len(db.query("SELECT id FROM call_turns WHERE call_id=?", (c.get("call_id"),)))},
                      created_at=c.get("ended_at") or c.get("started_at"))
        if plan["status"] == ST_ACTIVE:
            add_event(oid, "callback", "发起回拨", f"运营 {plan['owner']} 发起第 {cb_n} 次回拨，商家未接",
                      actor=plan["owner"], created_at=_ago(150))
            add_event(oid, "note", "运营备注", "商家反馈财务在走流程，要求工作日 18 点后再联系",
                      actor=plan["owner"], created_at=_ago(96))
            add_event(oid, "schedule", "约定回拨", "约定 明日 18:30 再次回拨，确认是否已重新提交",
                      actor=plan["owner"], created_at=_ago(90))
        elif plan["status"] == ST_ESCALATED:
            add_event(oid, "escalate", "升级主管", "商家对驳回结论有异议，申请主管复核；商标注册时间存疑",
                      actor=plan["owner"], created_at=_ago(24))
        elif plan["status"] == ST_CLOSED:
            add_event(oid, "closed", "标记闭环", "五要素齐全，商家承诺 3 个工作日内重新提交材料",
                      actor=plan["owner"], created_at=_ago(320))
        elif plan["status"] == ST_WRITTEN:
            add_event(oid, "closed", "标记闭环 · 京麦回写完成", "已通过京麦驳回单回写整改结论，同步商家中心",
                      actor=plan["owner"], ref="jingmai", created_at=_ago(880))
        made += 1
    return made


def _dur(a: Optional[str], b: Optional[str]) -> str:
    if not a or not b:
        return "未接通"
    try:
        t0 = datetime.datetime.strptime(str(a)[:19], "%Y-%m-%d %H:%M:%S")
        t1 = datetime.datetime.strptime(str(b)[:19], "%Y-%m-%d %H:%M:%S")
        s = max(0, int((t1 - t0).total_seconds()))
        return f"{s // 60:02d}:{s % 60:02d}"
    except Exception:
        return "—"


# ---------------------------------------------------------------- 五环节进度
def flow5_done(flow5) -> int:
    """已完成环节数（flow_progress 已保证顺序连续，直接数 done 即可）。"""
    if not isinstance(flow5, list):
        return 0
    return sum(1 for s in flow5 if isinstance(s, dict) and s.get("done"))


def _legacy_flow5_progress(res: dict) -> int:
    """极旧通话（result_json 里没有 flow5）→ 按要素保守映射到五环节。"""
    elem = (res.get("compliance") or {}).get("elements") or {}
    state = res.get("state") or ""
    done = [
        bool(state and state != "PRE") or bool(elem.get("自报身份")),
        bool(elem.get("身份确认")),
        bool(elem.get("驳回原因") and elem.get("整改动作")),
        False,                       # 「商家答疑」旧数据无独立证据，绝不猜
        bool(res.get("closed_loop") and elem.get("结束语")),
    ]
    # 顺序约束：前一环节没完成，后面一律不算（与 flow_progress 同规则）
    n, ok = 0, True
    for d in done:
        if not d:
            ok = False
        if ok:
            n += 1
    return n


def _live_flow5(merchant_id):
    """进行中通话：直接读该会话 Agent 的 flow_progress()，与商家端实时同源。"""
    try:
        import server as _srv          # 延迟导入，避开 orders ↔ server 循环依赖
    except Exception:
        return None
    try:
        row = db.query_one(
            "SELECT call_id FROM calls WHERE merchant_id=? AND status IN ('ringing','in_progress') "
            "ORDER BY id DESC LIMIT 1", (merchant_id,))
        if not row:
            return None
        ag = ((_srv.SESSIONS or {}).get(row["call_id"]) or {}).get("agent")
        return ag.flow_progress() if ag is not None else None
    except Exception:
        return None


def order_flow5(merchant_id, progress=0):
    """工单「整改进度」唯一数据源：进行中通话 → 最近通话落库 flow5 → 按进度兜底。

    返回与 agent.flow_progress() 同结构的 5 项数组。两端阶段名、完成数、
    当前环节全部由这一份数据决定，不再各算各的。
    """
    live = _live_flow5(merchant_id)
    if isinstance(live, list) and live:
        return live
    try:
        row = db.query_one(
            "SELECT result_json FROM calls WHERE merchant_id=? ORDER BY id DESC LIMIT 1",
            (merchant_id,))
    except Exception:
        row = None
    if row:
        f5 = (_j(row.get("result_json"), {}) or {}).get("flow5")
        if isinstance(f5, list) and f5:
            return f5
    # 尚未外呼 / 极旧数据：按工单已记录进度生成同名同序的 5 项
    p = max(0, min(int(progress or 0), 5))
    names = stages5()
    return [{"id": i + 1, "name": names[i], "done": i < p,
             "current": i == p and p < 5, "ts": None} for i in range(5)]


# ---------------------------------------------------------------- 挂机回填
def sync_calls() -> int:
    """把已结束通话的最新结论回填到工单：last_summary / 通话数 / 进度 / 轨迹。"""
    ensure_schema()
    n = 0
    orders = db.query("SELECT * FROM work_orders")
    for o in orders:
        rows = db.query(
            "SELECT * FROM calls WHERE merchant_id=? AND status='completed' ORDER BY id DESC",
            (o["merchant_id"],))
        if not rows:
            continue
        last = rows[0]
        cid = last.get("call_id")
        seen = db.query_one(
            "SELECT id FROM work_order_events WHERE order_id=? AND kind='call' AND ref=?",
            (o["order_id"], cid))
        res = _j(last.get("result_json"), {}) or {}
        # 11/15：进度只认与商家接听端同源的 flow5，不再按旧的五项要素另算一套分
        f5 = res.get("flow5")
        prog = (flow5_done(f5) if isinstance(f5, list) and f5
                else _legacy_flow5_progress(res))
        summary = o["last_summary"] or ""
        if res.get("resolved") or res.get("pending"):
            bits = []
            if res.get("resolved"):
                bits.append("已说明：" + "；".join(str(x) for x in res["resolved"][:2]))
            if res.get("pending"):
                bits.append("待补：" + "；".join(str(x) for x in res["pending"][:2]))
            summary = " ｜ ".join(bits)
        elif res.get("stop_stage"):
            summary = f"停在「{res['stop_stage']}」，需再次外呼补齐要素"
        status = o["status"]
        if status == ST_PENDING and rows:
            status = ST_ACTIVE
        if res.get("closed_loop"):
            if status in (ST_PENDING, ST_ACTIVE):
                status = ST_CLOSED
        elif status in (ST_CLOSED, ST_WRITTEN):
            # 【11/22】最新一通未闭环 → 任务重新打开。旧版只升不降，导致
            # “已闭环 + 3/5”同时出现（工单状态是历史闭环，进度是最近一通）。
            status = ST_ACTIVE
        # 进度跟随最新一通；闭环时取 min(5, prog)，未闭环时按最新 prog 回写，
        # 不再用 MAX(progress,?) 把历史 5/5 永久锁住。
        new_prog = min(5, prog) if res.get("closed_loop") else max(0, min(prog, 5))
        db.execute(
            "UPDATE work_orders SET call_count=?, last_call_at=?, last_call_id=?, last_summary=?, "
            "progress=?, status=?, updated_at=? WHERE order_id=?",
            (len(rows), last.get("ended_at") or last.get("started_at"), cid, summary,
             new_prog, status, _now(), o["order_id"]))
        if not seen:
            add_event(o["order_id"], "call",
                      f"通话 · {'闭环' if res.get('closed_loop') else '未闭环'}",
                      f"{'五要素齐全' if res.get('closed_loop') else '停在：' + str(res.get('stop_stage') or '—')}"
                      f" · 时长 {_dur(last.get('started_at'), last.get('ended_at'))}",
                      actor="数字员工", ref=cid,
                      payload={"progress": prog, "closed_loop": res.get("closed_loop")},
                      created_at=last.get("ended_at") or last.get("started_at"))
            n += 1
    return n


# ---------------------------------------------------------------- 查询
def kpi() -> dict:
    rows = db.query("SELECT * FROM work_orders")
    total = len(rows)
    pend = sum(1 for r in rows if r["status"] == ST_PENDING)
    active = sum(1 for r in rows if r["status"] == ST_ACTIVE)
    closed = sum(1 for r in rows if r["status"] in (ST_CLOSED, ST_WRITTEN))
    written = sum(1 for r in rows if r["status"] == ST_WRITTEN)
    escalated = sum(1 for r in rows if r["status"] == ST_ESCALATED)
    jm = sum(1 for r in rows if "京麦" in (r["close_type"] or "") and r["status"] in (ST_CLOSED, ST_WRITTEN))
    calls = sum(r["call_count"] or 0 for r in rows)
    callbacks = sum(r["callback_count"] or 0 for r in rows)
    avg = round(sum(r["progress"] or 0 for r in rows) / total, 1) if total else 0
    rk = risk.stats()
    return {
        "total": total, "pending": pend, "active": active, "doing": pend + active,
        "closed": closed, "written": written, "escalated": escalated,
        "close_rate": round(closed / total * 100, 1) if total else 0,
        "jingmai": jm, "calls": calls, "callbacks": callbacks, "avg_progress": avg,
        # 11/12：被打上风险标记的商家数 / B 级累计次数（红线治理的收敛指标）
        "risk_marked": rk["merchants"], "risk_b_total": rk["b_total"],
        "risk_by_level": rk["by_level"],
    }


def list_orders(status: str = "all", q: str = "") -> dict:
    sync_calls()
    where, args = [], []
    if status and status not in ("all", "", "全部"):
        where.append("status=?")
        args.append(status)
    if q:
        where.append("(shop LIKE ? OR company LIKE ? OR brand LIKE ? OR reason LIKE ? OR order_id LIKE ?)")
        args += [f"%{q}%"] * 5
    sql = "SELECT * FROM work_orders" + (" WHERE " + " AND ".join(where) if where else "")
    rows = db.query(sql + " ORDER BY CASE status WHEN '已升级' THEN 0 WHEN '待跟进' THEN 1 WHEN '跟进中' THEN 2 ELSE 3 END, "
                            "CASE priority WHEN 'high' THEN 0 ELSE 1 END, updated_at DESC", tuple(args))
    # 商家风险标记一次性批量取回（避免每条工单一次查询）
    rks = risk.profiles([r["merchant_id"] for r in rows])
    # 11/15 第七轮：一次取回全部通话，按商家分组。工单标记 = 名下最近一通通话的
    # 标记，与「历史记录」里同一通通话逐字段一致；不做第二套工单级判定。
    by_merchant = {}
    for c in db.query(
            "SELECT call_id,merchant_id,status,closed_loop,stop_stage,result_json "
            "FROM calls ORDER BY id DESC"):
        by_merchant.setdefault(c["merchant_id"], []).append(c)
    for r in rows:
        ev = db.query_one("SELECT title,kind,created_at FROM work_order_events WHERE order_id=? ORDER BY id DESC", (r["order_id"],))
        r["last_event"] = ev or {}
        r["stages"] = STAGES
        # 11/15：列表卡的 x/5 与阶段名也取自同一份 flow5，和商家接听端一一对应
        r["flow5"] = order_flow5(r["merchant_id"], r.get("progress"))
        r["progress"] = flow5_done(r["flow5"])
        r["snapshot"] = _j(r["snapshot"], {}) or {}
        r["risk"] = rks.get(r["merchant_id"], {})
        recent = by_merchant.get(r["merchant_id"]) or []
        r["flags"] = call_marks.latest_call_flags(recent)
        r["flags_call_id"] = recent[0]["call_id"] if recent else ""
    return {"kpi": kpi(), "items": rows, "statuses": ALL_STATUS, "counts": kpi(),
            "mark_rules": call_marks.RULE_TEXT}


def get_order(oid: str) -> dict:
    sync_calls()
    o = db.query_one("SELECT * FROM work_orders WHERE order_id=?", (oid,))
    if not o:
        raise HTTPException(404, "工单不存在")
    o["snapshot"] = _j(o["snapshot"], {}) or {}
    # 11/15：详情「整改进度」用与商家接听端同源的 flow5 逐节点渲染
    f5 = order_flow5(o["merchant_id"], o.get("progress"))
    o["flow5"] = f5
    o["progress"] = flow5_done(f5)
    o["stage"] = o["progress"]
    events = db.query("SELECT * FROM work_order_events WHERE order_id=? ORDER BY id DESC", (oid,))
    for e in events:
        e["payload"] = _j(e["payload"], {}) or {}
        e["meta"] = EVENT_KINDS.get(e["kind"], {"label": e["kind"], "glyph": "·"})
    calls = db.query(
        "SELECT c.call_id,c.direction,c.status,c.started_at,c.ended_at,c.closed_loop,c.stop_stage,"
        "c.result_json,s.level2_category FROM calls c LEFT JOIN scenarios s ON s.id=c.scenario_id "
        "WHERE c.merchant_id=? ORDER BY c.id DESC LIMIT 12", (o["merchant_id"],))
    for c in calls:
        c["result"] = _j(c.get("result_json"), {}) or {}
        c.pop("result_json", None)
    # 11/15 第七轮：关联通话逐条挂标记；工单级标记取最近一通（与历史记录同源）
    call_marks.attach(calls)
    o["flags"] = call_marks.latest_call_flags(calls)
    o["flags_call_id"] = calls[0]["call_id"] if calls else ""
    return {"order": o, "events": events, "calls": calls, "stages": STAGES,
            "flow5": f5, "kinds": EVENT_KINDS, "risk": risk.profile(o["merchant_id"]),
            "flags": o["flags"], "mark_rules": call_marks.RULE_TEXT}


# ---------------------------------------------------------------- 动作
ACTIONS = {
    "callback": "发起回拨", "schedule": "约定回拨", "close": "标记闭环", "escalate": "升级主管",
    "assign": "指派跟进人", "note": "运营备注", "edit": "改资料", "reopen": "重新打开",
    "writeback": "京麦回写",
}


def _open_redial(order: dict) -> Optional[str]:
    live = db.query_one(
        "SELECT call_id FROM calls WHERE merchant_id=? AND status IN ('ringing','in_progress') "
        "ORDER BY id DESC LIMIT 1", (order["merchant_id"],))
    if live:
        return live["call_id"]
    scen = db.query_one("SELECT id FROM scenarios WHERE level2_category=? ORDER BY id LIMIT 1",
                        (order["reason"],))
    # 【11/14 修复·回拨场景】工单 reason 是简写（如「商标注册时间不符合」）、
    # 场景表是全名（如「商标注册时间不符合（过期或卡控时间不符合）」）——
    # 精确匹配失败会让回拨通话丢失场景（scenario_id=NULL，来电卡场景退化成默认值）。
    # 精确不中且 reason 非空时，按包含关系模糊兜底。
    if not scen and order["reason"]:
        scen = db.query_one("SELECT id FROM scenarios WHERE level2_category LIKE ? ORDER BY id LIMIT 1",
                            (f"%{order['reason']}%",))
    cid = uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO calls(call_id,merchant_id,scenario_id,direction,status,reject_reason,started_at,version_id) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (cid, order["merchant_id"], scen["id"] if scen else None, "redial", "ringing",
         order["reason"], _now(), None))
    return cid


def act(oid: str, action: str, body: Optional[dict] = None) -> dict:
    body = body or {}
    o = db.query_one("SELECT * FROM work_orders WHERE order_id=?", (oid,))
    if not o:
        raise HTTPException(404, "工单不存在")
    actor = (body.get("actor") or o["owner"] or "运营").strip()
    note = (body.get("note") or "").strip()
    out: dict = {"ok": True, "action": action}

    if action == "callback":
        cid = _open_redial(o)
        nst = o["status"] if o["status"] == ST_ACTIVE else ST_ACTIVE
        db.execute("UPDATE work_orders SET callback_count=callback_count+1,status=?,last_call_at=?,updated_at=? "
                   "WHERE order_id=?", (nst, _now(), _now(), oid))
        add_event(oid, "callback", "发起回拨", note or f"{actor} 发起回拨，等待商家接听", actor=actor, ref=cid or "")
        out["call_id"] = cid

    elif action == "schedule":
        due = body.get("due_at") or ""
        db.execute("UPDATE work_orders SET due_at=?,next_action=?,updated_at=? WHERE order_id=?",
                   (due, body.get("next_action") or o["next_action"], _now(), oid))
        add_event(oid, "schedule", "约定回拨", f"约定 {due or '待定'}" + (f" · {note}" if note else ""), actor=actor)

    elif action == "close":
        db.execute("UPDATE work_orders SET status=?,stage=5,progress=5,next_action='',due_at=NULL,updated_at=? WHERE order_id=?",
                   (ST_CLOSED, _now(), oid))
        add_event(oid, "closed", "标记闭环", note or "运营确认整改完成并关单", actor=actor)
        out["status"] = ST_CLOSED

    elif action == "writeback":
        db.execute("UPDATE work_orders SET status=?,stage=5,progress=5,next_action='',due_at=NULL,updated_at=? WHERE order_id=?",
                   (ST_WRITTEN, _now(), oid))
        add_event(oid, "closed", "京麦回写完成", note or "整改结论已回写京麦驳回单", actor=actor, ref="jingmai")
        out["status"] = ST_WRITTEN

    elif action == "escalate":
        db.execute("UPDATE work_orders SET status=?,priority='high',updated_at=? WHERE order_id=?",
                   (ST_ESCALATED, _now(), oid))
        add_event(oid, "escalate", "升级主管", note or "商家对驳回结论有异议，转主管复核", actor=actor)
        out["status"] = ST_ESCALATED

    elif action == "assign":
        owner = (body.get("owner") or "").strip() or "未指派"
        db.execute("UPDATE work_orders SET owner=?,updated_at=? WHERE order_id=?", (owner, _now(), oid))
        add_event(oid, "assign", "指派跟进人", f"跟进人变更为 {owner}" + (f" · {note}" if note else ""), actor=actor)
        out["owner"] = owner

    elif action == "note":
        if note:
            db.execute("UPDATE work_orders SET note=?,updated_at=? WHERE order_id=?", (note, _now(), oid))
            add_event(oid, "note", "运营备注", note, actor=actor)

    elif action == "edit":
        fields = {k: (body.get(k) or "").strip() for k in
                  ["shop", "company", "brand", "material", "reason", "close_type", "owner"] if body.get(k)}
        if fields:
            sets = ",".join(f"{k}=?" for k in fields)
            db.execute(f"UPDATE work_orders SET {sets},updated_at=? WHERE order_id=?",
                       tuple(fields.values()) + (_now(), oid))
            snap = _j(o["snapshot"], {}) or {}
            snap.update(fields)
            snap["snapshot_at"] = _now()
            db.execute("UPDATE work_orders SET snapshot=? WHERE order_id=?",
                       (json.dumps(snap, ensure_ascii=False), oid))
            add_event(oid, "edit", "商家资料更新",
                      "；".join(f"{k} → {v}" for k, v in fields.items()), actor=actor)

    elif action == "reopen":
        db.execute("UPDATE work_orders SET status=?,priority='normal',updated_at=? WHERE order_id=?",
                   (ST_ACTIVE, _now(), oid))
        add_event(oid, "reopen", "重新打开", note or "工单重新打开，回到跟进中", actor=actor)
        out["status"] = ST_ACTIVE
    else:
        raise HTTPException(400, f"不支持的动作：{action}")

    _touch(oid)
    out["order"] = db.query_one("SELECT * FROM work_orders WHERE order_id=?", (oid,))
    return out


# ---------------------------------------------------------------- 路由
@router.get("")
def api_list(status: str = "all", q: str = ""):
    return list_orders(status, q)


@router.post("/sync")
def api_sync():
    return {"ok": True, "appended": sync_calls(), "kpi": kpi()}


@router.post("/seed")
def api_seed(force: bool = False):
    return {"ok": True, "created": seed(force=force)}


@router.get("/kpi")
def api_kpi():
    sync_calls()
    return kpi()


@router.get("/{oid}")
def api_detail(oid: str):
    return get_order(oid)


@router.post("/{oid}/action")
def api_action(oid: str, body: dict = None):
    return act(oid, (body or {}).get("action", ""), body or {})
