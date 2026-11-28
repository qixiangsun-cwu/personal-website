# -*- coding: utf-8 -*-
"""练习 2 参考答案（pandas 数据处理，10 小问）。
用法：把本文件和 订单_lx2.xlsx 放在同一个文件夹，然后 `python 练习2_参考答案.py`。
环境：Python 3.11+ / pandas 2.2+（本机实测 Python 3.14.7 + pandas 3.0.5）。
"""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).resolve().parent
FILE = HERE / "订单_lx2.xlsx"

plt.rcParams["font.sans-serif"] = ["SimHei"]
plt.rcParams["axes.unicode_minus"] = False


def title(no, text):
    print("\n" + "=" * 64)
    print("第 %s 题：%s" % (no, text))


title(1, "导入 Pandas，读取数据文件，创建 DataFrame")
df = pd.read_excel(FILE)
print(df.head())

title(2, "查看数据框的形状和前 5 条记录")
print("shape =", df.shape)
print(df.head(5))

title(3, "查询数量超过 10 的大衣，按数量降序")
hit = df[(df["商品名称"] == "大衣") & (df["数量"] > 10)]
print(hit[["订购日期", "商品名称", "数量"]].sort_values("数量", ascending=False))

title(4, "统计围巾的最高销售数量")
print("围巾最高数量 =", df[df["商品名称"] == "围巾"]["数量"].max())

title(5, "统计每种商品的订购总数量，并按总数量降序")
total = df.groupby("商品名称").agg({"数量": "sum"}).sort_values("数量", ascending=False)
print(total)

title(6, "查询围巾的订购数量并保存在 df2")
df2 = df[df["商品名称"] == "围巾"]
print(df2)

title(7, "用 df2 分区统计订购数量等级（差/中/好），统计各等级数量")
df2 = df2.copy()
df2["等级"] = pd.cut(df2["数量"], bins=[0, 10, 20, 30], labels=["差", "中", "好"])
print(df2[["订购日期", "数量", "等级"]])
print(df2["等级"].value_counts().sort_index())
print("未落档（NaN）：", df2["等级"].isna().sum())

title(8, "用透视表查看每天每种商品的订购总数量")
df1 = df.pivot_table(values="数量", index="订购日期", columns="商品名称",
                     aggfunc="sum", margins=True)
print(df1)

title(9, "用柱状图展示围巾每日的订购数量")
scarf = df1["围巾"].drop("All")
print(scarf)
ax = scarf.plot.bar(figsize=(8, 4.2), color="#2e7d32")
ax.set_title("围巾每日订购数量")
ax.set_ylabel("数量")
ax.set_xlabel("订购日期")
for i, v in enumerate(scarf.values):
    ax.text(i, v, "%d" % v, ha="center", va="bottom", fontsize=10)
plt.tight_layout()
plt.savefig(HERE / "围巾每日订购数量.png", dpi=150)
print("已保存图片：围巾每日订购数量.png")

title(10, "将数据框导出到 Excel 文件“订单结果.xlsx”")
df.to_excel(HERE / "订单结果.xlsx", index=False)
print("已导出：订单结果.xlsx，行数", len(df))
print("\n练习 2 全部完成。")
