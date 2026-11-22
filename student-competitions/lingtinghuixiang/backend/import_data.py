# -*- coding: utf-8 -*-
"""一键导入：把组委会给的两个 xlsx + 商家名单导入 SQLite（结构化）+ 知识库FTS索引（双写）。

用法：
    python import_data.py                 # 导入组委会两个xlsx + 演示商家
    python import_data.py --file x.xlsx   # 现场拿到新数据一键加入（增量，不清空已有）

处理要点（来自赛题数据说明 docx 的提示）：
1. 场景汇总三级分类纵向合并 → 向下填充，否则 68 条里只有 5 条能拿到一级菜单；
2. 特殊场景表"商家后续咨询"列混入"京麦驳回"标记 → 归为场景标记而非真实问题；
3. 个别错漏/残缺话术 → 过滤明显噪声（无话术行、残缺结尾不单独处理，仅跳过空行）。
"""

import argparse
import json
import os
import re
import sys

try:
    import openpyxl
except ImportError:
    # 11/11：必说要素抽取函数需要能被零依赖的工具脚本复用，
    # 因此把 Excel 依赖降级为「只有真正读 xlsx 时才需要」。
    openpyxl = None

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

# 11/11：去掉原开发机的本机绝对路径，改为按候选目录自动探测赛题资料，
# 让「拷走即用」与「07 数据一键导入」在任意机器上都能找到 xlsx。
# 也可用环境变量 ZHIYU_DATA_DIR 显式指定资料目录。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_XLSX_MAIN_NAME = "[POP资审]资审关单场景话术V1.0.xlsx"
_CANDIDATES = [
    os.path.join(_ROOT, "..", "组委会给的赛题要求和数据"),
    os.path.join(_ROOT, "..", "..", "XINJIEDUAN", "赛题说明和数据", "智语外呼-赛题说明"),
    os.path.join(_ROOT, "data"),
]
DATA_DIR = os.environ.get("ZHIYU_DATA_DIR") or next(
    (p for p in _CANDIDATES if os.path.isfile(os.path.join(p, _XLSX_MAIN_NAME))),
    _CANDIDATES[0])
XLSX_MAIN = os.path.join(DATA_DIR, _XLSX_MAIN_NAME)
XLSX_QA = os.path.join(DATA_DIR, "驳回外呼QA.xlsx")

# 11/11：必说要素结构化标注。把场景话术里的「驳回原因 / 整改动作」抽成字段，
# 写进知识库 chunk 的正文与 meta，让 LLM Prompt 与合规质检读同一份依据，
# 避免模型自由发挥漏掉要素、而质检又拿另一套规则去判。
REQUIRED_ELEMENTS_META = ("自报身份", "身份确认", "驳回原因", "整改动作", "结束语")


def extract_script_elements(script: str) -> dict:
    """从一条场景参考话术里抽出驳回原因句与整改动作句。"""
    parts = [p.strip() for p in re.split(r"[。！？；\n]", script or "") if p.strip()]
    reason = next((p for p in parts if re.search(
        r"因|原因|不符合|缺少|不完整|过期|失效|驳回", p)), "")
    action = next((p for p in parts if re.search(
        r"请您|需您|重新|补充|补齐|上传|提交|修改|开具|提供", p)), "")
    return {"reason": reason, "action": action}

DEMO_MERCHANTS = [
    ("杭州云裳服饰有限公司", "云裳服饰旗舰店", "陈经理", "13800001001",
     {"资审单号": "ZS-2026-1001", "驳回场景": "商标状态不符合要求"}),
    ("苏州锦程数码专营店", "锦程数码京东自营", "刘主管", "13800001002",
     {"资审单号": "ZS-2026-1002", "驳回场景": "授权书内容不完整"}),
    ("广州晨曦美妆有限公司", "晨曦美妆官方旗舰店", "王女士", "13800001003",
     {"资审单号": "ZS-2026-1003", "驳回场景": "商标注册时间不符合"}),
    ("成都巴蜀食品有限公司", "巴蜀味道旗舰店", "张先生", "13800001004",
     {"资审单号": "ZS-2026-1004", "驳回场景": "授权链路不完整"}),
    ("深圳迅捷电子科技有限公司", "迅捷电子京东专卖店", "李经理", "13800001005",
     {"资审单号": "ZS-2026-1005", "驳回场景": "资质上传有误"}),
]


def cell(v) -> str:
    if v is None:
        return ""
    return str(v).replace("\r", "").strip()


def norm(v) -> str:
    """合并单元格内的换行归一，便于分类树对齐。"""
    return cell(v).replace("\n", "").replace(" ", "")


def import_scenarios(clear: bool) -> int:
    """文件一：场景汇总主表 → scenarios 表 + 知识库。"""
    if openpyxl is None:
        raise RuntimeError("未安装 openpyxl：pip install openpyxl")
    wb = openpyxl.load_workbook(XLSX_MAIN, data_only=True)
    ws = wb["场景汇总"]
    rows = list(ws.iter_rows(values_only=True))

    # 定位表头行（含"一级菜单"，在第0列）
    head_i = None
    for i, r in enumerate(rows[:20]):
        if r and norm(r[0]) == "一级菜单":
            head_i = i
            break
    if head_i is None:
        raise RuntimeError("未找到场景汇总表头（一级菜单）")

    records, fill1, fill2, fill3 = [], "", "", ""
    for r in rows[head_i + 1:]:
        if not r or not any(c is not None and cell(c) for c in r[:6]):
            continue  # 完全空行才跳过；续行（分类列为空）需参与向下填充
        m1, m2, m3 = norm(r[0]), norm(r[1]), norm(r[2])
        if m1:
            fill1 = m1
        if m2:
            fill2 = m2
        if m3:
            fill3 = m3
        method, script = cell(r[3]), cell(r[4])
        if not fill3:
            continue
        records.append((fill1, fill2, fill3, method, script))

    if clear:
        db.execute("DELETE FROM scenarios")
        db.execute("DELETE FROM knowledge_chunks WHERE source IN ('scenario','script_body')")
        db.execute("DELETE FROM knowledge_fts WHERE rowid NOT IN (SELECT id FROM knowledge_chunks)")

    db.executemany(
        "INSERT INTO scenarios(level1_menu,level1_category,level2_category,close_method,script_text) "
        "VALUES(?,?,?,?,?)", records)

    # 双写知识库：每条场景一个分块（二级分类 + 关单方式 + 话术）
    for (m1, m2, m3, method, script) in records:
        if not script:
            continue
        elems = extract_script_elements(script)
        content = (f"资审驳回场景【{m3}】。一级环节：{m1}；材料大类：{m2}。"
                   f"关单方式：{method}。"
                   f"【必说要素】驳回原因：{elems['reason'] or m3}；"
                   f"整改动作：{elems['action'] or '按京麦后台驳回意见补齐材料后重新提交'}。"
                   f"商家参考话术：{script}")
        db.add_knowledge("scenario", m3, content,
                         {"close_method": method, "level1": m1, "level2": m2,
                          "required_elements": list(REQUIRED_ELEMENTS_META),
                          "reason_text": elems["reason"], "action_text": elems["action"]})
    return len(records)


def import_qa(clear: bool) -> dict:
    """文件二：三个工作表 → call_scripts / qa_pairs / special_cases + 知识库。"""
    if openpyxl is None:
        raise RuntimeError("未安装 openpyxl：pip install openpyxl")
    wb = openpyxl.load_workbook(XLSX_QA, data_only=True)

    # 1) 驳回话术整句库（场景列纵向不合并，靠空行分段，直接收集非空话术）
    ws = wb["驳回话术（开头语+驳回话术+结束语）"]
    scripts, scene = [], ""
    for r in ws.iter_rows(min_row=2, values_only=True):
        sc, st = cell(r[0]), cell(r[1])
        if sc:
            scene = sc
        if st:
            stype = "电话外呼体" if ("请问您是" in st or "感谢您的接听" in st) else "京麦书面体"
            scripts.append((scene, st, stype))

    # 2) 高频QA
    ws = wb["高频QA"]
    qas = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        mod, q, a = cell(r[0]), cell(r[1]), cell(r[2])
        if q and a:
            qas.append((mod, q, a))

    # 3) 特殊场景（B=客服话术 C=商家后续咨询 D=客服回答；C列"京麦驳回"为场景标记）
    ws = wb["特殊场景"]
    specials = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        st, fq, ans = cell(r[1]), cell(r[2]), cell(r[3])
        if st and norm(fq) == "京麦驳回":
            fq, ans = "", ans  # 标记而非问题
        if st:
            specials.append((st, fq, ans))

    if clear:
        db.execute("DELETE FROM call_scripts")
        db.execute("DELETE FROM qa_pairs")
        db.execute("DELETE FROM special_cases")
        db.execute("DELETE FROM knowledge_chunks WHERE source IN ('qa','special','script')")
        db.execute("DELETE FROM knowledge_fts WHERE rowid NOT IN (SELECT id FROM knowledge_chunks)")

    db.executemany("INSERT INTO call_scripts(scene,script_text,script_type) VALUES(?,?,?)", scripts)
    db.executemany("INSERT INTO qa_pairs(module,question,answer) VALUES(?,?,?)", qas)
    db.executemany("INSERT INTO special_cases(call_script,followup_question,answer) VALUES(?,?,?)", specials)

    # 双写知识库
    for (scene, st, stype) in scripts:
        db.add_knowledge("script", scene or stype, st, {"type": stype})
    for (mod, q, a) in qas:
        db.add_knowledge("qa", q, f"商家问：{q}\n客服答：{a}", {"module": mod})
    for (st, fq, ans) in specials:
        if fq:
            db.add_knowledge("special", fq, f"话术：{st}\n商家追问：{fq}\n客服回答：{ans}")
    return {"scripts": len(scripts), "qa": len(qas), "special": len(specials)}


def import_merchants(rows: list) -> int:
    """商家名单导入（现场新数据一键加入）。rows: (company, shop, contact, phone, extra)"""
    db.executemany(
        "INSERT INTO merchants(company_name,shop_name,contact_name,phone,extra_json,source) "
        "VALUES(?,?,?,?,?,'import')", rows)
    for r in rows:
        db.add_knowledge("merchant", r[0],
                         f"商家资料：公司{r[0]}，店铺{r[1]}，联系人{r[2]}，电话{r[3]}。附加：{json.dumps(r[4], ensure_ascii=False)}")
    return len(rows)


def import_demo_merchants() -> int:
    exists = db.query_one("SELECT COUNT(*) AS n FROM merchants")["n"]
    if exists:
        return 0
    db.executemany(
        "INSERT INTO merchants(company_name,shop_name,contact_name,phone,extra_json,source) "
        "VALUES(?,?,?,?,?,'demo')",
        [(c, s, p, ph, json.dumps(x, ensure_ascii=False)) for c, s, p, ph, x in DEMO_MERCHANTS])
    for c, s, p, ph, x in DEMO_MERCHANTS:
        db.add_knowledge("merchant", c,
                         f"商家资料：公司{c}，店铺{s}，联系人{p}，电话{ph}。附加：{json.dumps(x, ensure_ascii=False)}")
    return len(DEMO_MERCHANTS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="现场新数据xlsx（增量导入，不清空已有）")
    ap.add_argument("--clear", action="store_true", help="先清空同源数据再导入")
    args = ap.parse_args()

    n1 = import_scenarios(clear=args.clear)
    n2 = import_qa(clear=args.clear)
    n3 = import_demo_merchants()

    if args.file and os.path.exists(args.file):
        wb = openpyxl.load_workbook(args.file, data_only=True)
        ws = wb.active
        new_rows = []
        for r in ws.iter_rows(min_row=2, values_only=True):
            if r and cell(r[0]):
                new_rows.append((cell(r[0]), cell(r[1]) if len(r) > 1 else "",
                                 cell(r[2]) if len(r) > 2 else "",
                                 cell(r[3]) if len(r) > 3 else "",
                                 json.dumps({}, ensure_ascii=False)))
        n4 = import_merchants(new_rows)
        print(f"现场数据导入：商家 {n4} 条")

    total = db.query_one("SELECT COUNT(*) AS n FROM knowledge_chunks")["n"]
    scen = db.query_one("SELECT COUNT(*) AS n FROM scenarios")["n"]
    print(f"导入完成：场景 {scen} 条 | 话术 {n2['scripts']} | QA {n2['qa']} | 特殊 {n2['special']} | 演示商家 {n3} | 知识库分块 {total}")


# ---------------- 新公司信息导入（网页端，秦 11/11 新增功能，A 方案移植） ----------------
# 说明：本区所有函数只服务「07 新公司信息导入 / 公司信息管理」，
#       不改动上方任何既有导入流程。
# 历史记录备份复用现有 settings 表（KV），因此不需要改 db.py 的表结构。

import time as _time
from datetime import datetime as _datetime

COMPANY_FIELDS = ("company_name", "shop_name", "contact_name", "phone",
                  "audit_no", "reject_reason")
_HISTBAK_PREFIX = "histbak:"


def _s(row: dict, key: str) -> str:
    """取字段并 strip()。"""
    v = row.get(key, "")
    return str(v).strip() if v is not None else ""


def _extra_dict(merchant_row: dict) -> dict:
    """解析 extra_json，兼容旧演示数据的中文键（资审单号 / 驳回场景）。"""
    raw = merchant_row.get("extra_json") or "{}"
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    out = {}
    for k, v in data.items():
        field = {"资审单号": "audit_no", "驳回场景": "reject_reason"}.get(k, k)
        out[field] = v
    return out


def _company_values(row: dict) -> tuple:
    """组装 merchants 表 5 个写入值，扩展字段打包成 extra_json。"""
    extra = {}
    for k in ("audit_no", "reject_reason"):
        if _s(row, k):
            extra[k] = _s(row, k)
    return (_s(row, "company_name"), _s(row, "shop_name"),
            _s(row, "contact_name"), _s(row, "phone"),
            json.dumps(extra, ensure_ascii=False))


def _knowledge_text(values: tuple) -> str:
    """生成商家资料知识库文本。"""
    extra = json.loads(values[4]) if values[4] else {}
    return (f"商家资料：公司{values[0]}，店铺{values[1]}，联系人{values[2]}，"
            f"电话{values[3]}。"
            f"资审单号：{extra.get('audit_no', '')}；"
            f"驳回场景：{extra.get('reject_reason', '')}。").rstrip("；。") + "。"


def find_company_conflict(company_name: str) -> dict | None:
    """按公司全称查 status='active' 的已有公司，返回整行。"""
    if not (company_name or "").strip():
        return None
    return db.query_one(
        "SELECT * FROM merchants WHERE company_name=? AND status='active'",
        (company_name.strip(),))


def company_history_count(merchant_id: int) -> dict:
    """统计该公司 {calls, turns, tickets}，弹窗、表格、删除存档共用。"""
    calls = db.query_one(
        "SELECT COUNT(*) AS n FROM calls WHERE merchant_id=?", (merchant_id,))["n"]
    turns = db.query_one(
        "SELECT COUNT(*) AS n FROM call_turns WHERE call_id IN "
        "(SELECT call_id FROM calls WHERE merchant_id=?)", (merchant_id,))["n"]
    tickets = db.query_one(
        "SELECT COUNT(*) AS n FROM tickets WHERE merchant_id=?", (merchant_id,))["n"]
    return {"calls": calls, "turns": turns, "tickets": tickets}


def _remove_merchant_knowledge(company_name: str) -> int:
    """删该公司知识库条目，并同步删对应 knowledge_fts 索引行。返回删除条数。"""
    rows = db.query(
        "SELECT id FROM knowledge_chunks WHERE source='merchant' AND title=?",
        (company_name,))
    ids = [r["id"] for r in rows]
    for kid in ids:
        db.execute("DELETE FROM knowledge_fts WHERE rowid=?", (kid,))
    db.execute(
        "DELETE FROM knowledge_chunks WHERE source='merchant' AND title=?",
        (company_name,))
    return len(ids)


def add_company(row: dict) -> int:
    """双写新增公司：INSERT merchants + 知识库资料。返回新 id。"""
    values = _company_values(row)
    mid = db.execute(
        "INSERT INTO merchants(company_name,shop_name,contact_name,phone,"
        "extra_json,status,source) VALUES(?,?,?,?,?,'active','import')", values)
    db.add_knowledge("merchant", values[0], _knowledge_text(values))
    return mid


def backup_and_clear_history(merchant_row: dict) -> dict:
    """把 merchants 整行 + calls + call_turns + tickets 打包成 JSON 快照，
    先写存档，再按 call_turns → tickets → calls 顺序删除。"""
    mid = merchant_row["id"]
    calls = db.query("SELECT * FROM calls WHERE merchant_id=?", (mid,))
    call_ids = [c["call_id"] for c in calls]
    turns, tickets = [], []
    if call_ids:
        ph = ",".join("?" for _ in call_ids)
        turns = db.query(
            f"SELECT * FROM call_turns WHERE call_id IN ({ph}) ORDER BY id", tuple(call_ids))
        tickets = db.query(
            f"SELECT * FROM tickets WHERE call_id IN ({ph}) ORDER BY id", tuple(call_ids))
    extra_tickets = db.query(
        "SELECT * FROM tickets WHERE merchant_id=?", (mid,))
    seen = {t["id"] for t in tickets}
    tickets += [t for t in extra_tickets if t["id"] not in seen]
    snap = {"merchant": dict(merchant_row), "calls": [dict(c) for c in calls],
            "turns": [dict(t) for t in turns], "tickets": [dict(t) for t in tickets],
            "counts": {"calls": len(calls), "turns": len(turns),
                       "tickets": len(tickets)},
            "cleared_at": _datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    key = f"{_HISTBAK_PREFIX}{mid}:{int(_time.time() * 1000)}"
    db.set_setting(key, json.dumps(snap, ensure_ascii=False))
    # 先存档，后删数据
    db.execute("DELETE FROM call_turns WHERE call_id IN "
               "(SELECT call_id FROM calls WHERE merchant_id=?)", (mid,))
    db.execute("DELETE FROM tickets WHERE merchant_id=?", (mid,))
    db.execute("DELETE FROM calls WHERE merchant_id=?", (mid,))
    return {"key": key, "counts": snap["counts"]}


def replace_company(merchant_id: int, row: dict) -> dict:
    """替换公司：保留原 merchant_id，先存档清史，再覆盖资料与知识库。"""
    m = db.query_one("SELECT * FROM merchants WHERE id=?", (merchant_id,))
    if not m:
        raise RuntimeError("公司不存在或已被删除")
    cleared = backup_and_clear_history(m)
    values = _company_values(row)
    db.execute(
        "UPDATE merchants SET company_name=?,shop_name=?,contact_name=?,"
        "phone=?,extra_json=? WHERE id=?", values + (merchant_id,))
    _remove_merchant_knowledge(m["company_name"])
    db.add_knowledge("merchant", values[0], _knowledge_text(values))
    return {"key": cleared["key"], "counts": cleared["counts"]}


def list_history_backups() -> list:
    """扫描 settings 表里 histbak:% 的键，返回存档列表（含 kind 与知识库条数）。"""
    rows = db.query(
        "SELECT key, value FROM settings WHERE key LIKE ? ORDER BY key DESC",
        (_HISTBAK_PREFIX + "%",))
    items = []
    for r in rows:
        try:
            s = json.loads(r["value"])
        except (ValueError, TypeError):
            continue
        m = s.get("merchant") or {}
        items.append({
            "key": r["key"],
            "company_name": m.get("company_name", ""),
            "merchant_id": m.get("id"),
            "cleared_at": s.get("cleared_at") or s.get("deleted_at") or "",
            "counts": s.get("counts", {"calls": 0, "turns": 0, "tickets": 0}),
            "kind": "delete" if s.get("deleted") else "replace",
            "knowledge": len(s.get("knowledge") or []),
        })
    return items


def restore_history_backup(key: str) -> dict:
    """一键恢复：替换存档恢复历史记录；删除存档连公司记录本身一起重建。"""
    if not key.startswith(_HISTBAK_PREFIX):
        raise RuntimeError("存档 key 不合法")
    raw = db.get_setting(key)
    if not raw:
        raise RuntimeError("存档不存在或已被消费")
    s = json.loads(raw)
    m = s.get("merchant") or {}
    deleted = bool(s.get("deleted"))
    company_restored = False
    # 删除存档且原 id 已空 → 用快照所有列（含原 id）重建公司；被占用则拒绝
    if deleted and m:
        exists = db.query_one("SELECT id FROM merchants WHERE id=?", (m.get("id"),))
        if exists:
            raise RuntimeError("原公司 ID 已被其它公司占用，无法恢复")
        cols = list(m.keys())
        ph = ",".join("?" for _ in cols)
        db.execute(
            f"INSERT INTO merchants({','.join(cols)}) VALUES({ph})",
            tuple(m.get(c) for c in cols))
        company_restored = True
    # 重建知识库资料与 FTS 索引（同名公司共用资料时只补一次）
    if deleted and s.get("knowledge"):
        restored_titles = set()
        for k in s["knowledge"]:
            if k.get("title") in restored_titles:
                continue
            restored_titles.add(k.get("title"))
            db.add_knowledge(k.get("source", "merchant"), k.get("title", ""),
                             k.get("content", ""),
                             json.loads(k.get("meta_json") or "{}"))
    elif deleted and m.get("company_name"):
        if not db.query_one(
                "SELECT id FROM knowledge_chunks WHERE source='merchant' AND title=?",
                (m["company_name"],)):
            values = (m.get("company_name", ""), m.get("shop_name", ""),
                      m.get("contact_name", ""), m.get("phone", ""),
                      m.get("extra_json", "{}"))
            db.add_knowledge("merchant", values[0], _knowledge_text(values))

    def insert_rows(table: str, rows: list):
        if not rows:
            return
        cols = list(rows[0].keys())
        ph = ",".join("?" for _ in cols)
        db.executemany(
            f"INSERT INTO {table}({','.join(cols)}) VALUES({ph})",
            [tuple(r.get(c) for c in cols) for r in rows])

    insert_rows("calls", s.get("calls") or [])
    insert_rows("tickets", s.get("tickets") or [])
    insert_rows("call_turns", s.get("turns") or [])
    # 恢复成功后自动消费删除
    db.execute("DELETE FROM settings WHERE key=?", (key,))
    return {"company_name": m.get("company_name", ""),
            "restored": s.get("counts", {"calls": 0, "turns": 0, "tickets": 0}),
            "kind": "delete" if deleted else "replace",
            "company_restored": company_restored}


def commit_companies(rows: list, decisions: dict | None) -> dict:
    """两阶段提交：decisions=None 只查重绝不写库；带决策才执行。"""
    conflicts = []
    for i, row in enumerate(rows or []):
        name = _s(row, "company_name")
        if not name:
            continue
        existing = find_company_conflict(name)
        if existing:
            conflicts.append({
                "index": i, "row": {k: _s(row, k) for k in COMPANY_FIELDS},
                "existing": {
                    "id": existing["id"],
                    "company_name": existing["company_name"],
                    "shop_name": existing["shop_name"],
                    "contact_name": existing["contact_name"],
                    "phone": existing["phone"],
                    "audit_no": _extra_dict(existing).get("audit_no", ""),
                    "reject_reason": _extra_dict(existing).get("reject_reason", ""),
                    "created_at": existing.get("created_at", ""),
                    "source": existing.get("source", ""),
                },
                "history": company_history_count(existing["id"]),
            })
    if decisions is None and conflicts:
        return {"need_decision": True, "conflicts": conflicts}
    added = replaced = skipped = 0
    cleared, errors = [], []
    decisions = decisions or {}
    for i, row in enumerate(rows or []):
        name = _s(row, "company_name")
        if not name:
            continue
        decision = str(decisions.get(str(i), decisions.get(i, "")))
        try:
            existing = find_company_conflict(name)
            if existing:
                if decision == "replace":
                    c = replace_company(existing["id"], row)
                    cleared.append({"company_name": name, "key": c["key"],
                                    "counts": c["counts"]})
                    replaced += 1
                elif decision == "skip":
                    skipped += 1
                else:  # add：新增一个同名公司
                    add_company(row)
                    added += 1
            else:
                add_company(row)
                added += 1
        except Exception as e:  # 单行异常不影响其它行
            errors.append({"index": i, "company_name": name, "error": str(e)})
    return {"added": added, "replaced": replaced, "skipped": skipped,
            "cleared": cleared, "errors": errors}


# ---------------- 07 公司信息删除（秦 11/11 新增功能） ----------------
# 说明：删除一家公司时，连同通话记录、对话轮次、工单、知识库资料一起清除；
# 清除前把全部内容打包进 settings 表，复用 07 页「已清除的历史记录」一键恢复。


def list_companies_for_table(keyword: str = "") -> list:
    """公司管理表格数据源：全字段 + 历史条数 + 知识库条数 + 搜索。"""
    kw = (keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        rows = db.query(
            "SELECT * FROM merchants WHERE company_name LIKE ? OR shop_name LIKE ? "
            "OR contact_name LIKE ? OR phone LIKE ? ORDER BY id", (like, like, like, like))
    else:
        rows = db.query("SELECT * FROM merchants ORDER BY id")
    # 一次性统计知识库条数，避免逐行查库
    kmap = {}
    for kr in db.query(
            "SELECT title, COUNT(*) AS n FROM knowledge_chunks "
            "WHERE source='merchant' GROUP BY title"):
        kmap[kr["title"]] = kr["n"]
    out = []
    for m in rows:
        extra = _extra_dict(m)
        out.append({
            "id": m["id"], "company_name": m["company_name"],
            "shop_name": m["shop_name"], "contact_name": m["contact_name"],
            "phone": m["phone"], "audit_no": extra.get("audit_no", ""),
            "reject_reason": extra.get("reject_reason", ""),
            "status": m.get("status", ""), "source": m.get("source", ""),
            "created_at": m.get("created_at", ""),
            "history": company_history_count(m["id"]),
            "knowledge": kmap.get(m["company_name"], 0),
        })
    return out


def delete_company(merchant_id, confirm_name: str = "") -> dict:
    """删除公司全部信息。三道防线：id 合法 → 公司存在 → 全称比对一致。
    删前打包存档（kind=delete），可一键恢复公司本身与全部历史。"""
    try:
        mid = int(merchant_id)
    except (TypeError, ValueError):
        raise RuntimeError("公司 ID 不合法")
    m = db.query_one("SELECT * FROM merchants WHERE id=?", (mid,))
    if not m:
        raise RuntimeError("公司不存在或已被删除")
    # 第 3 道防线：公司全称校验不通过不写任何数据
    typed = (confirm_name or "").strip()
    if not typed or typed != m["company_name"].strip():
        raise RuntimeError("公司全称校验不通过，删除已取消")
    calls = db.query("SELECT * FROM calls WHERE merchant_id=?", (mid,))
    call_ids = [c["call_id"] for c in calls]
    turns, tickets = [], []
    if call_ids:
        ph = ",".join("?" for _ in call_ids)
        turns = db.query(
            f"SELECT * FROM call_turns WHERE call_id IN ({ph}) ORDER BY id", tuple(call_ids))
        tickets = db.query(
            f"SELECT * FROM tickets WHERE call_id IN ({ph}) ORDER BY id", tuple(call_ids))
    extra_tickets = db.query("SELECT * FROM tickets WHERE merchant_id=?", (mid,))
    seen = {t["id"] for t in tickets}
    tickets += [t for t in extra_tickets if t["id"] not in seen]
    knowledge = [dict(k) for k in db.query(
        "SELECT * FROM knowledge_chunks WHERE source='merchant' AND title=?",
        (m["company_name"],))]
    counts = {"calls": len(calls), "turns": len(turns), "tickets": len(tickets)}
    snap = {"merchant": dict(m),
            "calls": [dict(c) for c in calls],
            "turns": [dict(t) for t in turns],
            "tickets": [dict(t) for t in tickets],
            "knowledge": knowledge, "deleted": True,
            "deleted_at": _datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "counts": counts}
    key = f"{_HISTBAK_PREFIX}{mid}:{int(_time.time() * 1000)}"
    db.set_setting(key, json.dumps(snap, ensure_ascii=False))  # 先写存档，后删数据
    db.execute("DELETE FROM call_turns WHERE call_id IN "
               "(SELECT call_id FROM calls WHERE merchant_id=?)", (mid,))
    db.execute("DELETE FROM tickets WHERE merchant_id=?", (mid,))
    db.execute("DELETE FROM calls WHERE merchant_id=?", (mid,))
    _remove_merchant_knowledge(m["company_name"])
    db.execute("DELETE FROM merchants WHERE id=?", (mid,))
    return {"company_name": m["company_name"], "merchant_id": mid,
            "key": key, "counts": counts, "knowledge": len(knowledge)}


if __name__ == "__main__":
    main()
