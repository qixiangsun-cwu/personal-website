# -*- coding: utf-8 -*-
"""商家风险标记（11/12 新增，独立文件）。

为什么要单独一张表
------------------
红线 B 级命中时，光「挂断 + 转专员」不够 —— 下次再外呼，数字员工对这家商家
还是一无所知，等于白拦。所以要把违规事实沉淀成**商家维度的长期标记**：

  merchant_risk  一家商家一行：等级 / 标签 / 命中次数 / 最近一次证据
                  每次 B 级命中都累加，等级随次数自动升级。

三个消费方
----------
1. 工单看板：列表与详情展示「重点关注」徽章 + 风险标签 + 最近一次原话；
2. 通话 Agent：外呼前读画像，写进系统提示词（边界更严、不许任何松动），
   让「上次想造假，这次还来」的商家被区别对待 —— 这是记忆，不是话术；
3. 指标页：风险标记商家数 / B 级累计次数，答辩时是一张「治理闭环」的凭据。

等级规则
--------
  b_count == 0      → 不建标记（A 级拦回只统计，不打标，避免误伤）
  b_count == 1      → watch 关注
  b_count >= 2      → high 重点监控
  b_count >= 3      → ban 限制外呼（人工介入）

只读写本表，不改 merchants / work_orders，回滚成本为零。
"""

from __future__ import annotations

import json
import datetime
from typing import Any, Optional

import db

LEVELS = {
    "watch": {"name": "关注", "cls": "warn", "desc": "首次提出违规诉求，已被拒绝并转专员"},
    "high": {"name": "重点监控", "cls": "bad", "desc": "多次提出违规诉求，后续外呼需主管在场"},
    "ban": {"name": "限制外呼", "cls": "bad", "desc": "屡次违规，暂停自动外呼，转人工处理"},
}


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_schema() -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS merchant_risk(
            merchant_id   INTEGER PRIMARY KEY,
            level         TEXT DEFAULT 'watch',
            tags          TEXT DEFAULT '[]',
            score         INTEGER DEFAULT 0,
            b_count       INTEGER DEFAULT 0,
            a_count       INTEGER DEFAULT 0,
            last_call_id  TEXT DEFAULT '',
            last_key      TEXT DEFAULT '',
            last_text     TEXT DEFAULT '',
            first_at      TEXT,
            last_at       TEXT,
            note          TEXT DEFAULT ''
        )
    """)


def _level(b_count: int) -> str:
    if b_count >= 3:
        return "ban"
    if b_count >= 2:
        return "high"
    return "watch"


def mark(merchant_id: Any, vio: dict, call_id: str = "", text: str = "") -> Optional[dict]:
    """记一次违规并落商家标记（B 级必调；A 级只累加 a_count 不打标）。

    返回更新后的标记 dict（供工单事件 / 前端实时展示）；参数不合法返回 None。
    """
    if not merchant_id:
        return None
    ensure_schema()
    level_in = (vio.get("level") or "A").upper()
    key = vio.get("key") or ""
    tag = vio.get("tag") or vio.get("name") or ""
    try:
        row = db.query_one("SELECT * FROM merchant_risk WHERE merchant_id=?", (merchant_id,))
        if row:
            tags = json.loads(row["tags"] or "[]")
            b_count = row["b_count"] or 0
            a_count = row["a_count"] or 0
        else:
            tags, b_count, a_count = [], 0, 0
        if tag and tag not in tags:
            tags.append(tag)
        if level_in == "B":
            b_count += 1
            score = (row["score"] if row else 0) or 0
            score += 10
        else:
            a_count += 1
            score = (row["score"] if row else 0) or 0
            score += 2
        lv = _level(b_count) if b_count else "watch"
        now = _now()
        if row:
            db.execute(
                "UPDATE merchant_risk SET level=?,tags=?,score=?,b_count=?,a_count=?,"
                "last_call_id=?,last_key=?,last_text=?,last_at=? WHERE merchant_id=?",
                (lv, json.dumps(tags, ensure_ascii=False), score, b_count, a_count,
                 call_id or row["last_call_id"], key or row["last_key"],
                 (text or row["last_text"] or "")[:80], now, merchant_id))
        else:
            db.execute(
                "INSERT INTO merchant_risk(merchant_id,level,tags,score,b_count,a_count,"
                "last_call_id,last_key,last_text,first_at,last_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (merchant_id, lv, json.dumps(tags, ensure_ascii=False), score, b_count,
                 a_count, call_id, key, (text or "")[:80], now, now))
    except Exception:
        return None
    return profile(merchant_id)


def profile(merchant_id: Any) -> dict:
    """单个商家的风险画像；无标记返回 {}（调用方无需判 None）。"""
    if not merchant_id:
        return {}
    ensure_schema()
    try:
        r = db.query_one("SELECT * FROM merchant_risk WHERE merchant_id=?", (merchant_id,))
    except Exception:
        return {}
    return _shape(r) if r else {}


def profiles(ids) -> dict:
    """批量取画像（工单列表用，避免 N+1 查询）：{merchant_id: profile}。"""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    ensure_schema()
    marks = ",".join("?" * len(ids))
    try:
        rows = db.query(f"SELECT * FROM merchant_risk WHERE merchant_id IN ({marks})", tuple(ids))
    except Exception:
        return {}
    return {r["merchant_id"]: _shape(r) for r in rows}


def _shape(r: dict) -> dict:
    try:
        tags = json.loads(r.get("tags") or "[]")
    except Exception:
        tags = []
    lv = r.get("level") or "watch"
    meta = LEVELS.get(lv, LEVELS["watch"])
    return {
        "merchant_id": r.get("merchant_id"),
        "level": lv, "level_name": meta["name"], "level_cls": meta["cls"],
        "level_desc": meta["desc"],
        "tags": tags,
        "score": r.get("score") or 0,
        "b_count": r.get("b_count") or 0,
        "a_count": r.get("a_count") or 0,
        "last_call_id": r.get("last_call_id") or "",
        "last_key": r.get("last_key") or "",
        "last_text": r.get("last_text") or "",
        "first_at": r.get("first_at") or "",
        "last_at": r.get("last_at") or "",
        "note": r.get("note") or "",
    }


def stats() -> dict:
    """指标页口径：被打标商家数 / 各等级分布 / B 级累计次数。"""
    ensure_schema()
    try:
        rows = db.query("SELECT * FROM merchant_risk")
    except Exception:
        rows = []
    by_level: dict = {}
    for r in rows:
        lv = r.get("level") or "watch"
        by_level[lv] = by_level.get(lv, 0) + 1
    tag_cnt: dict = {}
    for r in rows:
        try:
            for t in json.loads(r.get("tags") or "[]"):
                tag_cnt[t] = tag_cnt.get(t, 0) + 1
        except Exception:
            continue
    return {
        "merchants": len(rows),
        "b_total": sum((r.get("b_count") or 0) for r in rows),
        "by_level": by_level,
        "level_names": {k: v["name"] for k, v in LEVELS.items()},
        "by_tag": tag_cnt,
    }


def clear(merchant_id: Any) -> None:
    """解除标记（运营误判时用；正常链路不调用）。"""
    if merchant_id:
        db.execute("DELETE FROM merchant_risk WHERE merchant_id=?", (merchant_id,))
