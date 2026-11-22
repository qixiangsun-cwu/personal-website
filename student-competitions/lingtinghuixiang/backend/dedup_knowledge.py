# -*- coding: utf-8 -*-
"""知识库去重 + 唯一索引（校赛版数据一致性修复，11/15）。

背景：整合过程中 add_knowledge 为裸 INSERT、无唯一约束，历史导入累计
27 组完全重复副本，重复条目会拉偏 bigram+IDF 检索排序（噪声挤进 top4）。

本脚本做三件事，全部幂等，可重复执行：
  1) 按 (source,title,content) 精确去重 knowledge_chunks，同步清 knowledge_fts；
  2) 建 UNIQUE 索引 uq_knowledge，阻断未来重复写入；
  3) 报告清理前后条数。
用法：python dedup_knowledge.py [--dry-run]
"""
import os, sys, sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("ZHIYU_DB") or os.path.join(os.path.dirname(HERE), "data", "zhiyu.db")

def main(dry_run: bool = False) -> None:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    n0 = c.execute("select count(*) from knowledge_chunks").fetchone()[0]
    dup = c.execute(
        "select count(*) from (select source,title,content,count(*) k "
        "from knowledge_chunks group by source,title,content having k>1)").fetchone()[0]
    print(f"库: {DB}\n清理前: {n0} 条, 完全重复组 {dup}")

    # 1) 找出每组重复里要删的多余 rowid（保留最小 rowid）
    doomed = [r["rid"] for r in c.execute(
        "select id as rid from knowledge_chunks where id not in "
        "(select min(id) from knowledge_chunks group by source,title,content)")]

    # 同 content 不同 title 的跨标题重复不再合并（保留，属内容组织而非事故）

    if doomed:
        print(f"待删多余副本: {len(doomed)} 条 (ids: {doomed[:20]}{'...' if len(doomed)>20 else ''})")
        if not dry_run:
            marks = ",".join("?" * len(doomed))
            # FTS 先删（外键无级联）
            c.execute(f"delete from knowledge_fts where rowid in ({marks})", doomed)
            c.execute(f"delete from knowledge_chunks where id in ({marks})", doomed)
    else:
        print("无重复副本，跳过删除")

    # 2) 唯一索引（幂等）
    if not dry_run:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge "
                  "ON knowledge_chunks(source, title, content)")
        c.commit()
        n1 = c.execute("select count(*) from knowledge_chunks").fetchone()[0]
        fts = c.execute("select count(*) from knowledge_fts").fetchone()[0]
        print(f"清理后: {n1} 条 (删除 {n0-n1}), knowledge_fts {fts} 行, 唯一索引 uq_knowledge 已就位")
    else:
        print("[dry-run] 未做任何修改")
    c.close()

if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
