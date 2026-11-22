-- 自动生成：来自交付包 data/zhiyu.db 的真实结构（不含数据）

-- table: call_scripts

CREATE TABLE call_scripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scene TEXT DEFAULT '',                -- 场景
        script_text TEXT NOT NULL,            -- 开头语+驳回话术+结束语
        script_type TEXT DEFAULT ''           -- 电话外呼体 / 京麦书面体
    );

-- table: call_turns

CREATE TABLE call_turns (
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
        created_at TEXT DEFAULT (datetime('now','localtime'))
    , latency_ms REAL DEFAULT 0, e2e_ms REAL DEFAULT 0, stage_ms TEXT DEFAULT '{}');

-- table: calls

CREATE TABLE calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id TEXT UNIQUE NOT NULL,         -- 业务通话ID（UUID）
        merchant_id INTEGER REFERENCES merchants(id),
        scenario_id INTEGER REFERENCES scenarios(id),
        direction TEXT DEFAULT 'outbound',    -- outbound 外呼 / inbound 商家来电 / redial 回拨
        status TEXT DEFAULT 'ringing',        -- ringing / in_progress / completed / failed / rejected / missed
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

-- table: knowledge_chunks

CREATE TABLE knowledge_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,                 -- scenario / qa / special / script / merchant / custom
        title TEXT DEFAULT '',
        content TEXT NOT NULL,
        meta_json TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );

-- table: knowledge_fts

CREATE VIRTUAL TABLE knowledge_fts USING fts5(
        title, content,
        tokenize='trigram'
    );

-- table: merchant_risk

CREATE TABLE merchant_risk(
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
        );

-- table: merchants

CREATE TABLE merchants (
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

-- table: qa_pairs

CREATE TABLE qa_pairs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        module TEXT DEFAULT '',               -- 模块：授权 / 文审
        question TEXT NOT NULL,
        answer TEXT NOT NULL
    );

-- table: rsi_versions

CREATE TABLE rsi_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version TEXT NOT NULL,                -- v1.0 / v1.1 ...
        parent_version TEXT DEFAULT '',
        metrics_json TEXT DEFAULT '{}',       -- 本版本评测指标
        changes_json TEXT DEFAULT '[]',       -- 补丁列表（before/after diff）
        summary TEXT DEFAULT '',              -- 失败归因与解决方案说明
        status TEXT DEFAULT 'inactive'        -- active / inactive / rolled_back
    , manual_test_json TEXT DEFAULT '{}', engineer_json TEXT DEFAULT '');

-- table: scenarios

CREATE TABLE scenarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        level1_menu TEXT DEFAULT '',          -- 一级菜单（发生在哪个环节）
        level1_category TEXT DEFAULT '',      -- 一级分类（材料/规则大类）
        level2_category TEXT DEFAULT '',      -- 二级分类（具体驳回原因）
        close_method TEXT DEFAULT '',         -- 关单方式：电话 / 京麦驳回
        script_text TEXT DEFAULT '',          -- 资质驳回商家参考话术
        source TEXT DEFAULT 'xlsx',           -- xlsx / import / rsi_patch
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );

-- table: settings

CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );

-- table: special_cases

CREATE TABLE special_cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_script TEXT DEFAULT '',          -- 客服话术
        followup_question TEXT DEFAULT '',    -- 商家后续咨询（京麦驳回标记也算）
        answer TEXT DEFAULT ''                -- 客服回答
    );

-- table: term_map

CREATE TABLE term_map(
        wrong TEXT PRIMARY KEY,
        right TEXT NOT NULL,
        source TEXT DEFAULT 'rsi',
        created_at TEXT DEFAULT ''
    );

-- table: tickets

CREATE TABLE tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id TEXT UNIQUE REFERENCES calls(call_id),
        merchant_id INTEGER REFERENCES merchants(id),
        resolved_json TEXT DEFAULT '[]',      -- 本次外呼解决了什么
        pending_json TEXT DEFAULT '[]',       -- 待解决事项
        emotion_summary TEXT DEFAULT '',      -- 商家情绪总结
        compliance_rate REAL DEFAULT 1.0,     -- 必说话术合规率
        status TEXT DEFAULT 'open',           -- open / resolved / pending_resubmit
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );

-- table: unknown_terms

CREATE TABLE unknown_terms(
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

-- table: work_order_events

CREATE TABLE work_order_events(
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id   TEXT NOT NULL,
            kind       TEXT NOT NULL,
            title      TEXT DEFAULT '',
            detail     TEXT DEFAULT '',
            actor      TEXT DEFAULT '系统',
            ref        TEXT DEFAULT '',
            payload    TEXT DEFAULT '{}',
            created_at TEXT
        );

-- table: work_orders

CREATE TABLE work_orders(
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
        );

-- index: idx_wo_events

CREATE INDEX idx_wo_events ON work_order_events(order_id, id);

-- index: uq_call_turns_turn

CREATE UNIQUE INDEX uq_call_turns_turn ON call_turns(call_id, turn_no, role);

-- index: uq_knowledge

CREATE UNIQUE INDEX uq_knowledge ON knowledge_chunks(source, title, content);
