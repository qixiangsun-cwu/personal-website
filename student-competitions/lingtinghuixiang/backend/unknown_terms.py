# -*- coding: utf-8 -*-
"""未知词台账 —— 「猜 + 确认 + 自动沉淀」，代替人工一个个加词（11/22 新增）

## 为什么要有它

真机里商家说的是「材料」，ASR 听成「拆楼」；AI 没有依据，就**编了一个定义**
（「拆楼是指商家提供的资质材料存在造假情况」）。而人工往同音表里加词是条死路：

    「续脉」→ 加；「去脉」→ 又加；「听麦」→ 再加；「拆楼」……

**听错是一个开放集合，词表是封闭集合**，永远加不完。所以这里做的是机制，不是词表：

    第 1 次遇到不认识的词  → 模型按提示词红线第 9 条**反问确认**（"您是说材料吗？"）
    第 2 次还没确认       → 如实说"这个词我这边确实没有相关信息"，**不许再猜**
    每一次都记进台账        → RSI「待审核」队列里能看到"商家反复问、知识库没有"的词
    人工/RSI 审核通过      → 写进 term_map，**从下一通电话起自动生效**（不用改代码）

这样词表是**长出来的**，不是人一个个敲进去的；而"编定义"这条路在提示词和机制两层都被堵住。

## 表

    unknown_terms  term(唯一) / count / sample_text / call_id / status / mapped_to
    term_map       wrong(主键) / right / source      ← 审核通过后的映射，运行时即时生效
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

# 第几次开始"如实说不了解"（用户口径：两遍以上）
ADMIT_AT = 2

# 「X是什么 / X是啥 / X啥意思 / X怎么念」——只有这种求解释句式才去提取被问词
_TERM_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z]{2,6})(?:是什么|是啥|啥意思|什么意思|是干什么的|干什么用的|怎么念)")
_Q_TAIL = re.compile(r"(?:是什么|是啥|啥意思|什么意思|怎么念|在哪个|怎么弄|哪儿)")

_map_cache: Optional[Dict[str, str]] = None


def _conn():
    import db
    return db.get_conn()


def init() -> None:
    """建表（幂等）。"""
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS unknown_terms(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        term TEXT NOT NULL UNIQUE,
        count INTEGER DEFAULT 1,
        sample_text TEXT DEFAULT '',
        call_id TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        mapped_to TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        updated_at TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS term_map(
        wrong TEXT PRIMARY KEY,
        right TEXT NOT NULL,
        source TEXT DEFAULT 'rsi',
        created_at TEXT DEFAULT ''
    );
    """)


def extract_term(text: str) -> str:
    """从「X是什么」这类句子里取出被问的 X；不是求解释句式就返回空串。"""
    s = (text or "").strip()
    if not s:
        return ""
    m = _TERM_RE.search(s)
    if m:
        return m.group(1).strip()
    return ""


def is_probable_lookup(text: str) -> bool:
    return bool(extract_term(text))


def is_known(term: str) -> bool:
    """这个词是不是已知业务词（已知就不进台账，避免把京麦/材料也记成未知）。"""
    t = (term or "").strip()
    if not t:
        return True
    try:
        if t in _term_map():
            return True
    except Exception:
        pass
    try:
        import reply_policy as rp
        if t in rp.ASR_HOMOPHONE_MAP.values():
            return True
        if rp._BUSINESS_SIGNAL_RE.search(t):
            return True
    except Exception:
        pass
    try:
        import db
        if db.search_knowledge(t, top_k=1):
            return True
    except Exception:
        pass
    return False


def note(term: str, text: str = "", call_id: str = "") -> Dict[str, Any]:
    """记一次（幂等自增）。返回 {term, count, known}，供路由决定要不要"如实说不了解"。"""
    t = (term or "").strip()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    out = {"term": t, "count": 0, "known": True}
    if not t:
        return out
    try:
        init()
        c = _conn()
        # 单条 upsert：首次写入 count=1，后续每命中一次 +1。
        # （旧写法「先 INSERT OR IGNORE 再无条件 UPDATE」会让第一次就变成 2，
        #   导致"第 1 次就承认不了解"，与用户口径"两遍以上"不符。）
        c.execute(
            "INSERT INTO unknown_terms(term,count,sample_text,call_id,status,"
            "created_at,updated_at) VALUES(?,1,?,?,'pending',?,?) "
            "ON CONFLICT(term) DO UPDATE SET count=count+1, "
            "sample_text=excluded.sample_text, updated_at=excluded.updated_at",
            (t, (text or "")[:80], call_id or "", now, now))
        c.commit()
        row = c.execute("SELECT count, status FROM unknown_terms WHERE term=?", (t,)).fetchone()
        out["count"] = int(row[0]) if row else 1
        out["status"] = (row[1] if row else "pending")
    except Exception:
        out["count"] = 1
    out["known"] = is_known(t)
    return out


def should_admit(info: Optional[dict]) -> bool:
    """第 ADMIT_AT 次仍未确认的未知词 → 该如实说不了解，不许继续猜。"""
    if not info:
        return False
    return (not info.get("known")) and int(info.get("count") or 0) >= ADMIT_AT


def _term_map(refresh: bool = False) -> Dict[str, str]:
    global _map_cache
    if _map_cache is None or refresh:
        _map_cache = {}
        try:
            init()
            for row in _conn().execute("SELECT wrong, right FROM term_map"):
                _map_cache[str(row[0])] = str(row[1])
        except Exception:
            pass
    return _map_cache


def apply_norm(text: str) -> str:
    """把**已审核通过**的误听词替换成正确业务词（运行时即时生效，不用改代码）。"""
    s = text or ""
    if not s:
        return s
    try:
        m = _term_map()
    except Exception:
        return s
    if not m:
        return s
    for wrong, right in m.items():
        if wrong and wrong in s:
            s = s.replace(wrong, right)
    return s


def pending(limit: int = 50) -> List[Dict[str, Any]]:
    """RSI「待审核」列表：商家反复问、但知识库没有的词（按次数倒序）。"""
    try:
        init()
        rows = _conn().execute(
            "SELECT id,term,count,sample_text,status,mapped_to,updated_at "
            "FROM unknown_terms WHERE status='pending' ORDER BY count DESC, id DESC LIMIT ?",
            (int(limit),)).fetchall()
    except Exception:
        return []
    return [{"id": r[0], "term": r[1], "count": r[2], "sample_text": r[3],
             "status": r[4], "mapped_to": r[5], "updated_at": r[6]} for r in rows]


def approve(term_or_id: Any, right: str, source: str = "rsi") -> Dict[str, Any]:
    """审核通过：把「误听词 → 正确词」写进 term_map，并标记台账。

    从下一通电话起，`apply_norm()` 会自动替换这个词 —— **不需要改代码、不需要重启理解逻辑**。
    """
    right = (right or "").strip()
    if not right:
        return {"ok": False, "msg": "正确词不能为空"}
    try:
        init()
        c = _conn()
        row = None
        if isinstance(term_or_id, int) or str(term_or_id).isdigit():
            row = c.execute("SELECT term FROM unknown_terms WHERE id=?",
                            (int(term_or_id),)).fetchone()
        term = (row[0] if row else str(term_or_id)).strip()
        if not term:
            return {"ok": False, "msg": "找不到该词"}
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO term_map(wrong,right,source,created_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(wrong) DO UPDATE SET right=excluded.right, source=excluded.source",
                  (term, right, source, now))
        c.execute("UPDATE unknown_terms SET status='approved', mapped_to=?, updated_at=? "
                  "WHERE term=?", (right, now, term))
        c.commit()
        _term_map(refresh=True)
        return {"ok": True, "term": term, "right": right,
                "msg": f"已加入术语映射：{term} → {right}（下一轮自动生效）"}
    except Exception as e:
        return {"ok": False, "msg": f"{type(e).__name__}: {e}"}


def reject(term_or_id: Any) -> Dict[str, Any]:
    """审核不通过（确实是闲聊/外词）：标记掉，不再出现在待审核里。"""
    try:
        init()
        c = _conn()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(term_or_id, int) or str(term_or_id).isdigit():
            c.execute("UPDATE unknown_terms SET status='rejected', updated_at=? WHERE id=?",
                      (now, int(term_or_id)))
        else:
            c.execute("UPDATE unknown_terms SET status='rejected', updated_at=? WHERE term=?",
                      (now, str(term_or_id)))
        c.commit()
        return {"ok": True, "msg": "已标记为外词"}
    except Exception as e:
        return {"ok": False, "msg": f"{type(e).__name__}: {e}"}


def stats() -> Dict[str, Any]:
    try:
        init()
        c = _conn()
        total = c.execute("SELECT COUNT(*) FROM unknown_terms").fetchone()[0]
        pend = c.execute("SELECT COUNT(*) FROM unknown_terms WHERE status='pending'").fetchone()[0]
        mapped = c.execute("SELECT COUNT(*) FROM term_map").fetchone()[0]
        return {"total": total, "pending": pend, "mapped": mapped}
    except Exception:
        return {"total": 0, "pending": 0, "mapped": 0}
