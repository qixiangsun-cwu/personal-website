"""工程师 Agent —— RSI 闭环的「自改」环节（11/21 按赛题口径改道）

定位：接收人工勾选采纳的诊断（诊断师产出），生成最小补丁，
由确定性执行器完成 备份→应用→语法/冒烟检查→失败自动回滚 的全流程。
人工审核是硬闸门：补丁生成后必须人工点「应用」才落盘，应用后可一键撤销。

## 为什么改道（与赛题原文对齐）

赛题要求 RSI 能「自动优化**提示词**、**业务话术**、**检索知识**和**工具调用策略**」。
11/21 之前这里只开放 agent.py 的常量区，也就是**只能改源码里的字符串**，
既改不到提示词，也改不到知识库 —— 自进化的对象和赛题对不上。

现在开放的四条通道与赛题四个优化对象一一对应：

| 通道     | 赛题对象     | 落点                              | 补丁 form        |
|----------|--------------|-----------------------------------|------------------|
| 提示词   | 自动优化提示词 | backend/prompts.py 常量区         | file + old/new   |
| 业务话术 | 业务话术     | backend/agent.py 常量区（FIXED_*）| file + old/new   |
| 检索知识 | 检索知识     | knowledge_chunks（rsi_patch）     | kind=knowledge   |
| 工具策略 | 工具调用策略 | backend/prompts.py 的 TOOL_DESC   | file + old/new   |

提示词与工具描述都住在 prompts.py，所以「改提示词」和「改工具描述」是同一类补丁，
只是 old/new 落在不同常量上。这样自进化**不再需要改 Python 逻辑代码**：
改的是数据化的提示词与知识，可回滚、可演示、可复现。

模型配置（.env，OpenAI 兼容格式，方便切 Qwen）：
    ENGINEER_API_KEY   默认取 ZHIPU_API_KEY
    ENGINEER_BASE_URL  默认 https://open.bigmodel.cn/api/paas/v4
    ENGINEER_MODEL     默认 glm-4-flash

权限边界（L1 提示词/话术 + L2 参数 + L3 知识；真实业务逻辑代码仍不开放）：
    backend/prompts.py   常量区（`# ============ 七、组装辅助` 之前）：提示词 / 状态推进要求 / 工具描述
    backend/agent.py     常量区（`def check_compliance` 之前）：固定话术 FIXED_* / 轮次闸门参数 / 禁语表
    backend/server.py    TARGET_* 达标线四行
    knowledge_chunks     kind="knowledge" 的知识条目（带 source 标记，可整批回滚）
"""

import json
import os
import re
import shutil
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_ROOT = os.path.join(BASE_DIR, ".eng_backup")

# 白名单：文件 → 该文件允许修改的区域（region_end 为区域结束锚点，锚点行本身不含在内）
WHITELIST = {
    # 【11/21 新增 · 提示词通道】赛题「自动优化提示词」+「工具调用策略」两条都落在这里。
    # 只开放常量区（七、组装辅助 之前）：下面的 inbound_prompt/state_hint_of/tool_desc_of
    # 是运行时函数，工程师生成补丁时看不到它们，也就改不坏它们。
    "backend/prompts.py": {"region_end": "# ============ 七、组装辅助",
                           "desc": "提示词模板 / 状态推进要求 / 工具描述"},
    # 【业务话术通道】固定话术 FIXED_* 与轮次参数（原通道，保留）
    "backend/agent.py": {"region_end": "def check_compliance",
                         "desc": "话术常量 / 轮次参数 / 禁语表"},
    "backend/server.py": {"region_end": None, "only_lines": r"^TARGET_",
                          "desc": "达标线常量（TARGET_*）"},
}
# 禁区：白名单内但工程师无权触碰的代码块（11/12 冉的领地：要素正则由队友人工维护）
EXCLUDE_BLOCKS = {
    "backend/agent.py": [{"start": "ELEMENT_PATTERNS = {",
                          "note": "（ELEMENT_PATTERNS 要素识别正则：由队友人工维护，工程师无权修改）"}],
}
MAX_PATCHES = 4
FORBIDDEN_IN_PATCH = ("保证通过", "绝对能过", "一定能过", "包过", "百分百",
                      "封店", "罚款", "处罚", "身份证", "银行卡", "验证码", "密码")

# ---------------------------------------------------------------- 检索知识通道
# 知识补丁不落源码，而是写进 knowledge_chunks；统一打这个 source 标记，
# 于是「一键撤销」「按版本回滚」「哪些知识是自进化产出的」都能精确筛出来。
KNOWLEDGE_SOURCE = "rsi_patch"
KNOWLEDGE_MAX_CHARS = 400        # 单条知识上限：RAG 每轮只喂 220 字，太长会被截断浪费
KNOWLEDGE_MAX_PER_RUN = 3        # 一次自进化最多新增几条知识，防空转刷库


def _excluded_spans(content: str, rel_path: str) -> list:
    """计算禁区块在原文中的 (start, end, note) 位置列表。"""
    spans = []
    for blk in EXCLUDE_BLOCKS.get(rel_path, []):
        i = content.find(blk["start"])
        if i < 0:
            continue
        j = content.find("\n}", i)          # dict 块以独占一行的 } 结束
        if j < 0:
            continue
        spans.append((i, j + 2, blk["note"]))
    return spans


def _region_of(rel_path: str) -> str:
    """提取某文件的白名单区域文本（只把这个区域喂给工程师，从源头限权）。

    禁区块（如 ELEMENT_PATTERNS，冉的领地）会被替换成占位注释——工程师看得到
    "这里有块禁区"，但拿不到原文，也就无法构造逐字匹配的 old 片段来改它。
    """
    path = os.path.join(BASE_DIR, os.path.basename(rel_path))
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    cfg = WHITELIST[rel_path]
    if cfg.get("region_end"):
        idx = content.find(cfg["region_end"])
        region = content[:idx] if idx >= 0 else content
    elif cfg.get("only_lines"):
        pat = re.compile(cfg["only_lines"])
        return "".join(ln for ln in content.splitlines(keepends=True) if pat.match(ln))
    else:
        region = content
    for s, e, note in _excluded_spans(region, rel_path):
        region = region[:s] + note + region[e:]
    return region


def build_context() -> dict:
    return {p: {"desc": c["desc"], "code": _region_of(p)} for p, c in WHITELIST.items()}


# ---------------- 工程师生成补丁 ----------------

def _llm_chat(prompt: str) -> str:
    """OpenAI 兼容 chat/completions（智谱 / DashScope 通吃），失败抛异常。"""
    import requests
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    api_key = os.getenv("ENGINEER_API_KEY") or os.getenv("ZHIPU_API_KEY", "")
    base = (os.getenv("ENGINEER_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    model = os.getenv("ENGINEER_MODEL") or "glm-4-flash"
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "temperature": 0.1, "max_tokens": 1600,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"工程师LLM HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]["content"]


ENGINEER_PROMPT = """你是外呼系统的工程师Agent，根据已采纳的诊断对系统做最小修复。

【可修改区域（只有这些，逐字原文）】
{context}

【已采纳的诊断】
{diagnoses}

你有两条可选修复手段，按「改数据 > 改代码」的优先级选择：

A. 改提示词 / 业务话术 / 工具描述（file 补丁，old/new 逐字替换）
   · backend/prompts.py —— 提示词模板、各状态推进要求、工具描述（首选）
   · backend/agent.py   —— 固定话术 FIXED_* 等业务话术常量
B. 补检索知识（kind=knowledge 补丁，直接写进 FTS 知识库，不动任何文件）
   · 当诊断的根因是「知识库缺某条业务口径」时用它，
     例如商家问发票有效期几个月、数量金额门槛，而知识库里没有。

输出JSON（不要多余文字）：
{{"patches": [
  {{"file": "backend/prompts.py",
    "old": "要被替换的现有片段（必须从可修改区域逐字复制，含缩进）",
    "new": "替换后的内容",
    "desc": "一句话说明改了什么、对应哪条诊断"}},
  {{"kind": "knowledge", "op": "upsert",
    "title": "知识标题（不超过20字，如「发票有效期按品类分档」）",
    "content": "可直接念给商家的业务口径（不超过{knowledge_max_chars}字，具体到数字与做法）",
    "desc": "补这条知识对应哪条诊断"}}
]}}
硬性约束：
1. file 补丁只允许修改常量定义（提示词文本、话术字符串、数值参数），
   禁止改函数/类逻辑、禁止增删函数、禁止动 import；
1.5 标注"由队友人工维护"的代码块（如 ELEMENT_PATTERNS 要素正则）是禁区，绝对禁止出现在 old/new 中；
2. old 必须逐字唯一匹配原文，new 与 old 结构对齐（引号/括号/逗号风格一致）；
3. 内容不得含禁语与夸大承诺（保证通过/包过/百分百/罚款/封店等），不得索要敏感信息；
4. 知识补丁不得含未填占位符（【】或 XX），必须是能直接说出口的完整口径；
5. 最多 {max_patches} 条补丁（知识补丁最多 {knowledge_max_per_run} 条）；
   若诊断均无法在白名单内修复，输出 {{"patches": []}} 并在 desc 说明。"""


def _patch_kind(p: dict) -> str:
    """补丁类型：knowledge（写知识库）/ file（改白名单文件）。"""
    k = (p.get("kind") or "file").strip().lower()
    return "knowledge" if k in ("knowledge", "kb", "rag") else "file"


def run_engineer(diagnoses: list) -> dict:
    """勾选的诊断 → 补丁提案（不落盘，只生成提案供人工审核）。"""
    if not diagnoses:
        return {"ok": False, "msg": "没有勾选任何诊断，先生成诊断并在卡片上勾选「采纳」"}
    prompt = ENGINEER_PROMPT.format(
        context=json.dumps(build_context(), ensure_ascii=False, indent=1),
        diagnoses=json.dumps(diagnoses, ensure_ascii=False, indent=1),
        max_patches=MAX_PATCHES,
        knowledge_max_chars=KNOWLEDGE_MAX_CHARS,
        knowledge_max_per_run=KNOWLEDGE_MAX_PER_RUN)
    raw = _llm_chat(prompt)
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    data = json.loads(raw)
    patches, rejected = [], []
    n_knowledge = 0
    for p in (data.get("patches") or [])[:MAX_PATCHES]:
        if _patch_kind(p) == "knowledge":
            n_knowledge += 1
            if n_knowledge > KNOWLEDGE_MAX_PER_RUN:
                rejected.append({**p, "errors": [f"知识补丁超过 {KNOWLEDGE_MAX_PER_RUN} 条上限"]})
                continue
        errs = _validate_patch(p)
        (rejected if errs else patches).append({**p, "errors": errs} if errs else p)
    return {"ok": True, "patches": patches, "rejected": rejected,
            "model": os.getenv("ENGINEER_MODEL") or "glm-4-flash"}


def _validate_knowledge(p: dict) -> list:
    """校验一条知识补丁：能直接念、可溯源、不含禁语与占位符。"""
    errs = []
    title = (p.get("title") or "").strip()
    content = (p.get("content") or "").strip()
    if not title:
        errs.append("知识补丁缺 title")
    elif len(title) > 40:
        errs.append("title 超过 40 字")
    if not content:
        errs.append("知识补丁缺 content")
    elif len(content) > KNOWLEDGE_MAX_CHARS:
        errs.append(f"content 超过 {KNOWLEDGE_MAX_CHARS} 字（RAG 每轮只喂 220 字）")
    for bad in FORBIDDEN_IN_PATCH:
        if bad in content or bad in title:
            errs.append(f"含禁语: {bad}")
    # 未填占位符绝对不能进知识库：会被模型原样念给商家（真实翻车过的老问题）
    if re.search(r"【[^】]*】|XX+|xxx", content):
        errs.append("content 含未填占位符（【】/XX），必须是可直接说出口的完整口径")
    if (p.get("op") or "upsert").lower() not in ("upsert", "delete"):
        errs.append("op 只支持 upsert / delete")
    return errs


def _validate_patch(p: dict) -> list:
    if _patch_kind(p) == "knowledge":
        return _validate_knowledge(p)
    errs = []
    if p.get("file") not in WHITELIST:
        errs.append(f"文件不在白名单: {p.get('file')}")
        return errs
    old, new = p.get("old") or "", p.get("new")
    if not old.strip():
        errs.append("old 为空")
    if not isinstance(new, str) or not new.strip():
        errs.append("new 为空")
    if old == new:
        errs.append("old 与 new 相同（无效补丁）")
    for bad in FORBIDDEN_IN_PATCH:
        if bad in (new or ""):
            errs.append(f"new 含禁语: {bad}")
    # 区域校验：old 必须完整落在白名单区域内
    path = os.path.join(BASE_DIR, os.path.basename(p["file"]))
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    cfg = WHITELIST[p["file"]]
    if cfg.get("region_end"):
        idx = content.find(cfg["region_end"])
        region = content[:idx] if idx >= 0 else ""
    else:
        pat = re.compile(cfg["only_lines"])
        region = "".join(ln for ln in content.splitlines(keepends=True) if pat.match(ln))
    # 禁区校验（11/12）：ELEMENT_PATTERNS 等禁区块由队友人工维护，工程师无权触碰
    for s, e, _note in _excluded_spans(content, p["file"]):
        span = content[s:e]
        if old and old in span:
            errs.append("触碰禁区：该代码块由队友人工维护，工程师无权修改")
            return errs
    if "ELEMENT_PATTERNS" in (new or "") and old != new:
        errs.append("new 涉及禁区 ELEMENT_PATTERNS（要素正则由队友维护）")
        return errs
    if old not in region:
        errs.append("old 不在白名单区域内（拒绝越权修改）")
    elif content.count(old) != 1:
        errs.append(f"old 在文件中出现 {content.count(old)} 次（要求唯一）")
    return errs


# ---------------- 确定性执行器：备份→应用→检查→失败自动回滚 ----------------

def _py_ok(path: str) -> bool:
    import py_compile
    try:
        py_compile.compile(path, doraise=True)
        return True
    except Exception:
        return False


def _apply_knowledge(p: dict) -> int:
    """把一条知识补丁写进 FTS 知识库，返回可精确回滚的 chunk id。

    同标题先删后写（upsert）：自进化反复采纳同一条诊断时不会把知识库刷成重复条目。
    """
    import db
    title = (p.get("title") or "").strip()
    content = (p.get("content") or "").strip()
    op = (p.get("op") or "upsert").strip().lower()
    old = db.query("SELECT id FROM knowledge_chunks WHERE source=? AND title=?",
                   (KNOWLEDGE_SOURCE, title))
    if op == "delete":
        if not old:
            raise ValueError(f"没有找到可删除的知识条目：{title}")
        _drop_knowledge([r["id"] for r in old])
        return -1
    if old:
        _drop_knowledge([r["id"] for r in old])
    return db.add_knowledge(KNOWLEDGE_SOURCE, title, content,
                            {"from": "rsi", "ts": time.strftime("%Y-%m-%d %H:%M:%S")})


def _drop_knowledge(ids: list) -> int:
    """按 id 精确删除知识分块与它的 FTS 索引行（幂等，供回滚用）。"""
    if not ids:
        return 0
    import db
    n = 0
    for kid in ids:
        if not isinstance(kid, int) or kid <= 0:
            continue
        db.execute("DELETE FROM knowledge_chunks WHERE id=?", (kid,))
        db.execute("DELETE FROM knowledge_fts WHERE rowid=?", (kid,))
        n += 1
    return n


def apply_patches(patches: list) -> dict:
    """三道闸：备份 → 逐条应用 → 语法+导入冒烟，任一失败全量回滚。

    file 补丁（提示词/话术/工具描述）与 knowledge 补丁（检索知识）走同一套闸门：
      · file  → 备份 .py 文件，失败时按文件还原；
      · knowledge → 记录写入的 chunk id，失败时按 id 精确删除（含 FTS 索引）。
    返回里的 knowledge_ids 会随版本记录落库，供「一键撤销」精确回滚知识侧改动。
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BACKUP_ROOT, ts)
    file_patches = [p for p in patches if _patch_kind(p) == "file"]
    kb_patches = [p for p in patches if _patch_kind(p) == "knowledge"]
    targets = sorted({p["file"] for p in file_patches})
    log = []
    if not patches:
        return {"ok": False, "rolled_back": False, "log": ["没有补丁"], "msg": "没有补丁"}

    for rel in targets:
        src = os.path.join(BASE_DIR, os.path.basename(rel))
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(src, os.path.join(backup_dir, os.path.basename(rel)))
        log.append(f"[备份] {rel} → {os.path.relpath(backup_dir, BASE_DIR)}")

    for p in file_patches:
        path = os.path.join(BASE_DIR, os.path.basename(p["file"]))
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        if content.count(p["old"]) != 1:
            log.append(f"[中止] {p['file']} 的 old 片段不再唯一，全部回滚")
            _restore(backup_dir, targets)
            return {"ok": False, "rolled_back": True, "log": log,
                    "msg": "补丁应用前校验失败（代码已被改动？），已回滚"}
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content.replace(p["old"], p["new"], 1))
        log.append(f"[应用] {p['file']} · {p.get('desc', '')[:60]}")

    kb_written = []
    for p in kb_patches:
        try:
            kid = _apply_knowledge(p)
        except Exception as e:
            log.append(f"[失败] 知识补丁写入失败：{str(e)[:100]}")
            _restore(backup_dir, targets)
            _drop_knowledge(kb_written)
            return {"ok": False, "rolled_back": True, "log": log,
                    "msg": "知识补丁写入失败，已回滚文件与已写入的知识条目"}
        kb_written.append(kid)
        log.append(f"[知识] {(p.get('title') or '')[:20]} · {p.get('desc', '')[:40]}"
                   f"（chunk id={kid}）")

    # 冒烟：语法 + 模块可导入
    for rel in targets:
        path = os.path.join(BASE_DIR, os.path.basename(rel))
        if not _py_ok(path):
            log.append(f"[失败] {rel} 语法检查未通过")
            _restore(backup_dir, targets)
            _drop_knowledge(kb_written)
            return {"ok": False, "rolled_back": True, "log": log,
                    "msg": f"{rel} 语法检查失败，已自动回滚到补丁前状态"}
        log.append(f"[语法OK] {rel}")
    try:
        import importlib, sys
        sys.path.insert(0, BASE_DIR)
        for mod in ("prompts", "agent", "server"):
            if any(rel.endswith(mod + ".py") for rel in targets):
                importlib.import_module(mod)
                importlib.reload(sys.modules[mod])
        log.append("[冒烟OK] 模块可正常导入")
    except Exception as e:
        log.append(f"[失败] 模块导入冒烟失败: {str(e)[:120]}")
        _restore(backup_dir, targets)
        _drop_knowledge(kb_written)
        return {"ok": False, "rolled_back": True, "log": log,
                "msg": "冒烟测试失败，已自动回滚到补丁前状态"}

    log.append("[完成] 全部补丁已应用。注意：运行中的服务需重启后生效（一键关闭→一键启动）")
    return {"ok": True, "rolled_back": False, "log": log, "backup_dir": backup_dir,
            "knowledge_ids": [k for k in kb_written if isinstance(k, int) and k > 0],
            "msg": "补丁已应用并通过语法/冒烟检查；需重启服务生效，可随时一键撤销"}


def rollback_knowledge(ids: list) -> int:
    """撤销某个版本的检索知识补丁（公开入口，server 的一键撤销会调它）。"""
    return _drop_knowledge(ids or [])


def _restore(backup_dir: str, targets: list) -> None:
    for rel in targets:
        bak = os.path.join(backup_dir, os.path.basename(rel))
        if os.path.exists(bak):
            shutil.copy2(bak, os.path.join(BASE_DIR, os.path.basename(rel)))


def rollback_backup(backup_dir: str, knowledge_ids: list | None = None) -> dict:
    """一键撤销：还原 .py 备份 + 删除该版本写入的知识条目。

    knowledge_ids 是该版本 apply_patches 返回的知识 chunk id 列表；
    旧版本记录里没有这个字段，传 None 时只还原文件（向后兼容）。
    """
    names, dropped = [], 0
    if backup_dir and os.path.isdir(backup_dir):
        names = [n for n in os.listdir(backup_dir) if n.endswith(".py")]
        _restore(backup_dir, [f"backend/{n}" for n in names])
    dropped = _drop_knowledge(knowledge_ids or [])
    if not names and not dropped:
        return {"ok": False, "msg": "备份目录不存在，且没有需要撤销的知识补丁"}
    parts = []
    if names:
        parts.append(f"恢复 {len(names)} 个文件")
    if dropped:
        parts.append(f"删除 {dropped} 条自进化知识")
    return {"ok": True, "msg": "已" + "、".join(parts), "files": names,
            "knowledge_dropped": dropped}
