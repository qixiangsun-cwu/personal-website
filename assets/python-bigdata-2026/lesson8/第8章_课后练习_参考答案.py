# -*- coding: utf-8 -*-
"""第 8 章《Pandas 数据处理与分析》课后练习 · 参考答案

配套页面：https://www.qixiangsun.com.cn/python-bigdata-2026-lesson8
运行环境：Python 3.14 + pandas 3.0.5（pandas 2.x 也能运行，个别输出格式略有差异）

使用方法：
1. 把本文件与数据文件（订单.xlsx、订单_new.xlsx 等）放在同一个文件夹；
2. 在命令行进入该文件夹，执行  python 第8章_课后练习_参考答案.py
3. 每个部分独立可运行；第 2、3 部分依赖同目录下的数据文件，找不到文件时会给出提示。
"""
import os
import sys

import numpy as np
import pandas as pd

# 让中文在 Windows 控制台正常显示（Python 3.7+ 可用 reconfigure）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def part1_sales():
    """作业 1：模拟商场 2025 年 12 个月销售表，完成 9 个查询 / 统计任务。"""
    print("\n" + "=" * 72)
    print("作业 1：2025 年销售表（服装 / 化妆品 / 日用品）")
    print("=" * 72)

    # ① 用随机数生成销量（每种商品都不超过 50），固定随机种子保证结果可复现
    np.random.seed(42)
    months = pd.date_range(start="2025-01-01", periods=12, freq="MS")  # 12 个月初
    sales_data = {
        "服装": np.random.randint(0, 51, size=12),
        "化妆品": np.random.randint(0, 51, size=12),
        "日用品": np.random.randint(0, 51, size=12),
    }
    # ② 用时间序列作为数据框的行索引
    sales = pd.DataFrame(sales_data, index=months)
    print("原始销售数据：")
    print(sales)

    # ③ 查询 6 月份“化妆品”的销售量
    print("\n③ 6 月份化妆品销售量：", sales.loc["2025-06-01", "化妆品"])

    # ④ 查询 5 月份和 10 月份的销售记录
    print("\n④ 5 月和 10 月的销售记录：")
    print(sales.loc[["2025-05-01", "2025-10-01"]])

    # ⑤ 查询“服装”销量为 20~35 的销售记录
    print("\n⑤ 服装销量 20~35 的记录：")
    print(sales[sales["服装"].between(20, 35)])

    # ⑥ 查询“服装”和“化妆品”销量都超过 30（含）的销售记录
    print("\n⑥ 服装和化妆品销量都 ≥30 的记录：")
    print(sales[(sales["服装"] >= 30) & (sales["化妆品"] >= 30)])

    # ⑦ 按“日用品”销量降序排序（返回新的数据框对象）
    print("\n⑦ 按日用品销量降序排序：")
    print(sales.sort_values(by="日用品", ascending=False))

    # ⑧ 统计 2025 年各商品的销售总量
    print("\n⑧ 各商品年度销售总量：")
    print(sales.sum())

    # ⑨ 统计每个月的销售总量
    print("\n⑨ 每个月销售总量：")
    print(sales.sum(axis=1))

    # ⑩ 自选：日用品销量最高的月份
    print("\n⑩ 日用品销量最高的月份：", sales["日用品"].idxmax(), "，销量：", sales["日用品"].max())


def _load(name):
    """按相对路径读取数据文件，不存在时返回 None 并提示。"""
    if not os.path.exists(name):
        print("  [跳过] 找不到数据文件：%s（请把本文件与数据文件放在同一目录）" % name)
        return None
    return name


def part2_order_stats():
    """作业 2：用订单_new.xlsx 完成统计分析（课件课后练习 2）。"""
    print("\n" + "=" * 72)
    print("作业 2：订单数据统计分析")
    print("=" * 72)
    name = _load("订单_new.xlsx")
    if not name:
        return
    df = pd.read_excel(name)
    print("数据规模：", df.shape)
    print("字段：", list(df.columns))

    # ① 每天的订单记录数量
    print("\n① 每天的订单记录数量：")
    print(df["订单日期"].value_counts().sort_index())

    # ② 每周的订购总数量和总金额，保存到 CSV
    weekly = df.groupby("周次").agg({"数量": "sum", "金额": "sum"})
    print("\n② 每周订购总数量 / 总金额：")
    print(weekly)
    weekly.to_csv("每周订购统计.csv", encoding="utf-8-sig")
    print("   已保存到 每周订购统计.csv")

    # ③ 每周各商品的订单记录数量（数据透视表）
    print("\n③ 每周各商品的订单记录数量：")
    print(df.pivot_table(values="金额", index="周次", columns="商品名称",
                         aggfunc="count", margins=True))

    # ④ 第 5 周的订购总数量
    print("\n④ 第 5 周订购总数量：", df[df["周次"] == 5]["数量"].sum())

    # ⑤ 自选：每种商品的订购总金额，按金额降序
    print("\n⑤ 每种商品订购总金额（降序）：")
    print(df.groupby("商品名称", as_index=False).agg({"金额": "sum"})
            .sort_values("金额", ascending=False))


def part3_pipeline():
    """作业 3：订单数据全流程（导入 → 清洗 → 查询 → 分组 → 透视 → 导出）。"""
    print("\n" + "=" * 72)
    print("作业 3：订单数据全流程实战")
    print("=" * 72)
    name = _load("订单.xlsx")
    if not name:
        return

    # ① 导入并查看
    df = pd.read_excel(name)
    print("① 导入：", df.shape)
    df.info()

    # ② 清洗：删除缺失值 → 删除重复值 → 删除异常值（金额为 0、单价>150 且数量>25）
    before = df.shape[0]
    df = df.dropna()
    print("\n② 删缺失值：", before, "→", df.shape[0])
    before = df.shape[0]
    df = df.drop_duplicates()
    print("   去重复值：", before, "→", df.shape[0])
    before = df.shape[0]
    # 异常值 1：金额为 0
    df = df[df["金额"] != 0]
    # 异常值 2：单价大于 150 且数量大于 25
    df = df[~((df["单价"] > 150) & (df["数量"] > 25))]
    print("   删异常值：", before, "→", df.shape[0])

    # ③ 查询：金额超过 3000 的订单
    print("\n③ 金额超过 3000 的订单（前 5 条）：")
    print(df[df["金额"] > 3000][["订单编号", "商品名称", "金额"]].head())

    # ④ 分组：每种商品的订购总金额、总数量
    print("\n④ 每种商品统计：")
    print(df.groupby("商品名称", as_index=False).agg({"金额": "sum", "数量": "sum"})
            .sort_values("金额", ascending=False))

    # ⑤ 透视：按周次 × 商品名称统计总金额
    df["周次"] = df["订单日期"].dt.isocalendar().week
    print("\n⑤ 周次 × 商品 的销售额透视表：")
    print(df.pivot_table(values="金额", index="周次", columns="商品名称",
                         aggfunc="sum", fill_value=0, margins=True,
                         margins_name="总计"))

    # ⑥ 导出（不带行号）
    out = "订单_分析结果.xlsx"
    df.to_excel(out, index=False)
    print("\n⑥ 已导出：", out, "（", df.shape[0], "行）")


if __name__ == "__main__":
    part1_sales()
    part2_order_stats()
    part3_pipeline()
    print("\n全部完成。")
