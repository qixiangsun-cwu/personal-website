# -*- coding: utf-8 -*-
"""SQLite 数据库层：表结构定义与访问封装。

所有数据存在本地 data/zhiyu.db，比赛现场拷贝即用。
设计原则：
1. 商家资料、场景话术、QA、通话记录、工单全部结构化存储（供看板/工单实时查询）；
2. 知识库表 + FTS5 trigram 索引（供 Agent RAG 语义检索），导入数据时"双写"同时完成；
3. 不把公司信息写死——商家/场景/QA 均可通过导入接口随时增量更新。
"""

import json
import math
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ZHIYU_DB", os.path.join(BASE_DIR, "..", "data", "zhiyu.db"))

# 线程锁：SQLite 写并发保护（FastAPI 多线程访问）
_lock = threading.Lock()


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_conn = None
def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = get_conn()
        init_schema(_conn)
    return _conn


def init_schema(c: sqlite3.Connection) -> None:
    """建表（幂等）。"""
    c.executescript("""
    CREATE TABLE IF NOT EXISTS merchants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_name TEXT NOT NULL,           -- 公司全称
        shop_name TEXT DEFAULT '',            -- 店铺名称
        contact_name TEXT DEFAULT '',         -- 联系人
        phone TEXT DEFAULT '',                -- 联系电话（演示用虚拟号）
        status TEXT DEFAULT 'active',         -- active / archived
        source TEXT DEFAULT 'manual',         -- manual / import
        extra_json TEXT DEFAULT '{}',         -- 扩展字段（资质单号等）
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );

    -- 资审场景库（文件一·场景汇总，68条，三级分类已向下填充）
    CREATE TABLE IF NOT EXISTS scenarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        level1_menu TEXT DEFAULT '',          -- 一级菜单（发生在哪个环节）
        level1_category TEXT DEFAULT '',      -- 一级分类（材料/规则大类）
        level2_category TEXT DEFAULT '',      -- 二级分类（具体驳回原因）
        close_method TEXT DEFAULT '',         -- 关单方式：电话 / 京麦驳回
        script_text TEXT DEFAULT '',          -- 资质驳回商家参考话术
        source TEXT DEFAULT 'xlsx',           -- xlsx / import / rsi_patch
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );

    -- 驳回话术整句库（文件二·52条）
    CREATE TABLE IF NOT EXISTS call_scripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scene TEXT DEFAULT '',                -- 场景
        script_text TEXT NOT NULL,            -- 开头语+驳回话术+结束语
        script_type TEXT DEFAULT ''           -- 电话外呼体 / 京麦书面体
    );

    -- 高频 QA（文件二·11组）
    CREATE TABLE IF NOT EXISTS qa_pairs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        module TEXT DEFAULT '',               -- 模块：授权 / 文审
        question TEXT NOT NULL,
        answer TEXT NOT NULL
    );

    -- 特殊场景剧本（文件二·28条）
    CREATE TABLE IF NOT EXISTS special_cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_script TEXT DEFAULT '',          -- 客服话术
        followup_question TEXT DEFAULT '',    -- 商家后续咨询（京麦驳回标记也算）
        answer TEXT DEFAULT ''                -- 客服回答
    );

    -- 通话记录
    CREATE TABLE IF NOT EXISTS calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id TEXT UNIQUE NOT NULL,         -- 业务通话ID（UUID）
        merchant_id INTEGER REFERENCES merchants(id),
        scenario_id INTEGER REFERENCES scenarios(id),
        direction TEXT DEFAULT 'outbound',    -- outbound 外呼 / inbound 商家来电 / redial 回拨
        status TEXT DEFAULT 'ringing',        -- ringing / in_progress / completed / failed / rejected / missed
                                             -- 11/12: pending_followup 待跟进(未终结) / superseded 已被二次呼叫承接
        version_id INTEGER,                   -- 本次通话使用的数字员工版本
        reject_reason TEXT DEFAULT '',        -- 本通电话要告知的驳回原因快照
        started_at TEXT,
        answered_at TEXT,
        ended_at TEXT,
        closed_loop INTEGER DEFAULT 0,        -- 是否任务闭环（五段式走完+回写）
        stop_stage TEXT DEFAULT '',           -- 停在哪个环节（未闭环归因用）
        result_json TEXT DEFAULT '{}',        -- 结构化回写结果
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );

    -- 对话轮次（工单明细：京东端全程可见）
    CREATE TABLE IF NOT EXISTS call_turns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id TEXT REFERENCES calls(call_id),
        turn_no INTEGER,
        role TEXT,                            -- agent / merchant / system
        text TEXT NOT NULL,
        asr_confidence REAL DEFAULT 1.0,      -- ASR置信度
        intent TEXT DEFAULT '',               -- 意图标签
        state TEXT DEFAULT '',                -- 状态机状态
        emotion TEXT DEFAULT 'neutral',       -- 情绪：neutral/anxious/angry/satisfied
        compliance TEXT DEFAULT 'PASS',       -- 合规检查结果
        interrupted INTEGER DEFAULT 0,        -- 是否被商家打断
        latency_ms REAL DEFAULT 0,            -- 【旧口径】仅 LLM 生成耗时，保留兼容，不用于入围判定（罗 11/12）
        e2e_ms REAL DEFAULT 0,               -- 【赛题口径】用户停说→浏览器首帧播放（毫秒）（罗 11/12）
        stage_ms TEXT DEFAULT '{}',          -- 分段时延预算 JSON（罗 11/12）
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );

    -- 工单（每通电话一份：已解决 / 待解决）
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id TEXT UNIQUE REFERENCES calls(call_id),
        merchant_id INTEGER REFERENCES merchants(id),
        resolved_json TEXT DEFAULT '[]',      -- 本次外呼解决了什么
        pending_json TEXT DEFAULT '[]',       -- 待解决事项
        emotion_summary TEXT DEFAULT '',      -- 商家情绪总结
        compliance_rate REAL DEFAULT 0.0,     -- 必说话术合规率（11/11：缺失按 0 计，不再默认满分）
        status TEXT DEFAULT 'open',           -- open / resolved / pending_resubmit
                                             -- 11/12: pending_followup 待二次/择日呼叫(不进指标分母)
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );

    -- 知识库分块（RAG 检索源，双写目标之二）
    CREATE TABLE IF NOT EXISTS knowledge_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,                 -- scenario / qa / special / script / merchant / custom
        title TEXT DEFAULT '',
        content TEXT NOT NULL,
        meta_json TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
        title, content,
        tokenize='trigram'
    );

    -- RSI 版本与指标
    CREATE TABLE IF NOT EXISTS rsi_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version TEXT NOT NULL,                -- v1.0 / v1.1 ...
        parent_version TEXT DEFAULT '',
        metrics_json TEXT DEFAULT '{}',       -- 本版本评测指标
        changes_json TEXT DEFAULT '[]',       -- 补丁列表（before/after diff）
        summary TEXT DEFAULT '',              -- 失败归因与解决方案说明
        status TEXT DEFAULT 'inactive'        -- active / inactive / rolled_back
    );

    -- 系统级 KV（当前激活版本、配置等）
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)
    # 幂等迁移：旧库补列
    cols = {r[1] for r in c.execute("PRAGMA table_info(call_turns)").fetchall()}
    # 罗 11/12：时延口径升级 —— 旧库启动时自动补齐 e2e_ms/stage_ms，不破坏任何历史数据
    for col, ddl in (
        ("latency_ms", "ALTER TABLE call_turns ADD COLUMN latency_ms REAL DEFAULT 0"),
        ("e2e_ms", "ALTER TABLE call_turns ADD COLUMN e2e_ms REAL DEFAULT 0"),
        ("stage_ms", "ALTER TABLE call_turns ADD COLUMN stage_ms TEXT DEFAULT '{}'"),
    ):
        if col not in cols:
            c.execute(ddl)
    # 11/11：轮次去重 —— 同一通电话同一轮同一角色只允许一条记录，
    # 防止重连/重复回调把商家发言重复计数，让「平均轮次」指标失真。
    c.execute("DELETE FROM call_turns WHERE id NOT IN "
              "(SELECT MIN(id) FROM call_turns GROUP BY call_id, turn_no, role)")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_call_turns_turn "
              "ON call_turns(call_id, turn_no, role)")
    # 11/12：RSI 版本人工实测记录列（对比窗口用），旧库幂等补齐
    vcols = {r[1] for r in c.execute("PRAGMA table_info(rsi_versions)").fetchall()}
    if "manual_test_json" not in vcols:
        c.execute("ALTER TABLE rsi_versions ADD COLUMN manual_test_json TEXT DEFAULT '{}'")
    # 11/12：工程师 Agent 补丁提案与执行状态列，旧库幂等补齐
    if "engineer_json" not in vcols:
        c.execute("ALTER TABLE rsi_versions ADD COLUMN engineer_json TEXT DEFAULT ''")
    c.commit()


@contextmanager
def atomic():
    """同一把写锁内的多语句事务：要么全部提交，要么全部回滚。

    server.finalize_call 用它把「calls 结果回写」与「tickets 建单」绑成一次原子操作，
    避免中途异常留下"有通话没工单"的半截状态。
    """
    with _lock:
        c = conn()
        c.commit()  # 收掉可能残留的读事务，再显式开事务
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def query(sql: str, params: tuple = ()) -> list:
    with _lock:
        cur = conn().execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: tuple = ()):
    rows = query(sql, params)
    return rows[0] if rows else None


def prior_calls(merchant_id, limit: int = 3, exclude_call_id: str = "") -> list:
    """取某商家最近的若干通历史通话摘要（长会话记忆用）。

    返回 [{call_id, ended_at, direction, closed_loop, stop_stage, resolved, pending}]，
    resolved/pending 取自 result_json。按结束时间倒序，排除当前通话。
    无历史通话时返回空列表（首次外呼不注入记忆段）。
    """
    if not merchant_id:
        return []
    sql = ("SELECT call_id, ended_at, direction, closed_loop, stop_stage, result_json "
           "FROM calls WHERE merchant_id=? AND status IN ('completed','failed','rejected','missed') ")
    params = [merchant_id]
    if exclude_call_id:
        sql += "AND call_id<>? "
        params.append(exclude_call_id)
    sql += "ORDER BY ended_at DESC LIMIT ?"
    params.append(limit)
    rows = query(sql, tuple(params))
    out = []
    for r in rows:
        try:
            rj = json.loads(r.get("result_json") or "{}")
        except Exception:
            rj = {}
        out.append({
            "call_id": r.get("call_id"),
            "ended_at": r.get("ended_at") or "",
            "direction": r.get("direction") or "",
            "closed_loop": bool(r.get("closed_loop")),
            "stop_stage": r.get("stop_stage") or "",
            "resolved": rj.get("resolved") or [],
            "pending": rj.get("pending") or [],
        })
    return out


def execute(sql: str, params: tuple = ()) -> int:
    with _lock:
        cur = conn().execute(sql, params)
        conn().commit()
        return cur.lastrowid


def executemany(sql: str, rows: list) -> None:
    with _lock:
        conn().executemany(sql, rows)
        conn().commit()


# ---------------- 知识库（RAG）双写辅助 ----------------

def add_knowledge(source: str, title: str, content: str, meta: dict | None = None) -> int:
    """写入知识库分块并同步 FTS 索引（双写的"知识库"一侧）。"""
    kid = execute(
        "INSERT INTO knowledge_chunks(source,title,content,meta_json) VALUES(?,?,?,?)",
        (source, title, content, json.dumps(meta or {}, ensure_ascii=False)),
    )
    executemany("INSERT INTO knowledge_fts(rowid,title,content) VALUES(?,?,?)",
                [(kid, title, content)])
    return kid


# ---------------- 知识库检索（11/12 重写） ----------------
# 旧实现的三个硬伤：
#   1) knowledge_fts 用 trigram 分词器，而 trigram 本质是「≥3 字的子串精确匹配」，
#      于是「驳回」「商标」这类 2 字查询产生不了 trigram，必然 0 命中；
#      「为什么驳回了」要求正文出现这 6 个字连续子串，同样 0 命中。
#   2) LIKE 回退用 "%".join(kws) 拼成 "%驳回%商标%"，要求两个词按顺序相邻出现。
#   3) 检索后才发现是商家档案（source='merchant'），top3 被档案占满再全丢掉。
# 现在改为：SQL 层排除档案 + 字符 bigram 倒排 + IDF 加权打分（中文短查询友好），
# 语料在内存缓存，写入时按 (行数, 最大 id) 失效重建。
_KN_STOP_CHARS = set("的了呢吧啊呀哦嗯是在我你他她它们这那么什怎为要有无和与及或就都也还又很太更最不没别把被让给对从向以所而且但如果因为所以个一上下里外到过着地得之其与")
_KN_STOP_WORDS = ("请问", "麻烦", "我想", "我们", "你们", "他们", "什么", "怎么", "为什么",
                  "是不是", "可以", "能不能", "一下", "已经", "就是", "但是", "还有",
                  "然后", "现在", "上次", "之前", "这个", "那个", "那么", "这样", "那样",
                  "的话", "一个", "什么样", "怎么样", "怎么办", "帮我", "你看", "我想问")
_KN_CACHE = {"sig": None, "rows": [], "df": {}, "n": 0}


def _kn_norm(text: str) -> str:
    """归一化：只留中文/数字/字母，去掉停用词短语，减少噪声 gram。"""
    t = (text or "").lower()
    for w in _KN_STOP_WORDS:
        t = t.replace(w, " ")
    t = re.sub(r"[^\u4e00-\u9fff0-9a-zA-Z]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _kn_grams(text: str) -> dict:
    """字符 bigram 计数。中文无需分词，2 字粒度对短查询召回最好。"""
    t = _kn_norm(text).replace(" ", "")
    out: dict = {}
    for i in range(len(t) - 1):
        g = t[i:i + 2]
        if g[0] in _KN_STOP_CHARS and g[1] in _KN_STOP_CHARS:
            continue          # 全是虚词的 gram 直接丢弃
        out[g] = out.get(g, 0) + 1
    return out


def _kn_sig() -> tuple:
    row = query_one("SELECT COUNT(*) AS c, COALESCE(MAX(id),0) AS m FROM knowledge_chunks")
    return (row["c"], row["m"]) if row else (0, 0)


def _kn_corpus() -> dict:
    """加载并缓存语料（含每个 chunk 的 bigram 计数与文档频率）。"""
    sig = _kn_sig()
    if _KN_CACHE["sig"] == sig:
        return _KN_CACHE
    rows = query("SELECT id, source, title, content FROM knowledge_chunks")
    df: dict = {}
    prepared = []
    for r in rows:
        tg = _kn_grams(r["title"] or "")
        cg = _kn_grams(r["content"] or "")
        for g in set(tg) | set(cg):
            df[g] = df.get(g, 0) + 1
        prepared.append({"id": r["id"], "source": r["source"], "title": r["title"],
                         "content": r["content"], "_tg": tg, "_cg": cg,
                         "_clen": max(1, len(r["content"] or ""))})
    _KN_CACHE.update(sig=sig, rows=prepared, df=df, n=max(1, len(prepared)))
    return _KN_CACHE


def search_knowledge(text: str, top_k: int = 3, exclude_sources=("merchant",),
                     min_score: float = 0.20) -> list:
    """中文短查询友好的知识检索（字符 bigram + IDF 加权）。

    * 商家档案（source='merchant'）在 SQL/加载阶段就排除，不再占用 top_k 名额；
    * 打分：query 每个 bigram 的 IDF × 该 chunk 内的词频（标题权重 2.5 倍），
      再按 chunk 长度做轻度归一，避免长文档天然占优；
    * 返回结构与旧版一致（id/source/title/content/score），上层无需改动。
    """
    q = (text or "").strip()[:200]
    if not q:
        return []
    corpus = _kn_corpus()
    if not corpus["rows"]:
        return []
    qg = _kn_grams(q)
    if not qg:
        return []
    skip = set(s.lower() for s in (exclude_sources or ()))
    n = corpus["n"]
    df = corpus["df"]
    weights = {}
    for g, cnt in qg.items():
        d = df.get(g, 0)
        if d == 0:
            continue
        idf = math.log((n + 1.0) / (d + 0.5))
        weights[g] = (1.0 + math.log(cnt)) * idf
    total_w = sum(weights.values())
    if total_w <= 0:
        return []
    scored = []
    for r in corpus["rows"]:
        if (r["source"] or "").lower() in skip:
            continue
        s = 0.0
        for g, w in weights.items():
            tc = r["_tg"].get(g, 0)
            cc = r["_cg"].get(g, 0)
            if tc or cc:
                s += w * (2.5 * tc + cc)
        if s <= 0:
            continue
        # 长度轻度归一，避免长文档天然占优
        s = s / total_w / (1.0 + 0.015 * (r["_clen"] ** 0.5))
        scored.append((s, r))
    if not scored:
        return []
    scored.sort(key=lambda x: -x[0])
    best = scored[0][0]
    # 同一标题只留一条（分块入库会产生重复标题，白占 top_k 名额）；
    # 相对阈值 0.5：实测强相关 ≥0.9、噪声 ≈0.2，切成两档后噪声不再混进提示词。
    out, seen = [], set()
    for s, r in scored:
        if s < max(min_score, best * 0.5):
            break
        sig = (r["source"] or "", (r["title"] or "").strip())
        if sig in seen:
            continue
        seen.add(sig)
        out.append({"id": r["id"], "source": r["source"], "title": r["title"],
                    "content": r["content"], "score": round(1.0 / (0.1 + s), 4)})
        if len(out) >= max(1, top_k):
            break
    return out


def get_setting(key: str, default: str = "") -> str:
    row = query_one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    execute("INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
