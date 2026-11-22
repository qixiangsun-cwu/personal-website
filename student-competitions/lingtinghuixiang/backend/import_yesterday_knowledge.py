# -*- coding: utf-8 -*-
"""昨天版优质话术增量 · 幂等导入脚本（2026-11-15）

用法（在今天的包的 backend 目录下运行）:
    python import_yesterday_knowledge.py            # 正式导入
    python import_yesterday_knowledge.py --dry-run  # 只预览，不写库

特性:
1. 幂等：按 (source, title) 判重，重复运行不会产生副本；
   已存在但内容有更新时做 UPDATE（并同步 FTS 索引）。
2. 只写 knowledge_chunks / knowledge_fts，不碰 qa_pairs、scenarios、
   call_scripts、special_cases、merchant 等任何业务表。
3. 每条 meta 带 managed_by 标记，便于以后识别/清理这批数据。
4. 不含任何 server/agent 代码改动，不影响 P50 相关链路。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.normpath(os.path.join(HERE, os.pardir, "data", "yesterday_knowledge.json"))
MANAGED_BY = "import_yesterday_knowledge.py"


def upsert_one(item) -> str:
    source = item["source"]
    title = item["title"]
    content = item["content"].strip()
    meta = dict(item.get("meta") or {})
    meta["managed_by"] = MANAGED_BY
    meta_json = json.dumps(meta, ensure_ascii=False)

    row = db.query_one(
        "SELECT id, content FROM knowledge_chunks WHERE source=? AND title=?",
        (source, title),
    )
    if row:
        if (row["content"] or "").strip() == content:
            return "skip"
        db.execute(
            "UPDATE knowledge_chunks SET content=?, meta_json=? WHERE id=?",
            (content, meta_json, row["id"]),
        )
        db.execute(
            "UPDATE knowledge_fts SET content=? WHERE rowid=?",
            (content, row["id"]),
        )
        return "update"

    db.add_knowledge(source, title, content, meta)
    return "add"


def main() -> int:
    if not os.path.exists(DATA_PATH):
        print(f"[ERR] 找不到数据文件: {DATA_PATH}")
        print("      请保持包内目录结构: backend/ 与 data/ 同级解压。")
        return 1
    items = json.load(open(DATA_PATH, encoding="utf-8"))
    dry = "--dry-run" in sys.argv
    stat = {"add": 0, "update": 0, "skip": 0}
    for it in items:
        if dry:
            act = "add"
            row = db.query_one(
                "SELECT id, content FROM knowledge_chunks WHERE source=? AND title=?",
                (it["source"], it["title"]),
            )
            if row:
                act = "skip" if (row["content"] or "").strip() == it["content"].strip() else "update"
        else:
            act = upsert_one(it)
        stat[act] += 1
        print(f"  [{act:6s}] [{it['source']}] {it['title']}")

    n = db.query_one("SELECT count(*) n FROM knowledge_chunks")
    print(f"\n完成: 新增 {stat['add']} / 更新 {stat['update']} / 跳过 {stat['skip']}"
          f"{'（dry-run 未写库）' if dry else ''}")
    print(f"当前 knowledge_chunks 总条数: {n['n']}")
    if not dry:
        print("提示: 若之后运行 python import_data.py --clear，会清掉 source='qa' 的条目，"
              "请再运行一次本脚本即可恢复（幂等）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
