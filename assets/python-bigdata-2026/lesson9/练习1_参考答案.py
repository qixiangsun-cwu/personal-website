# -*- coding: utf-8 -*-
"""练习 1 参考答案（教材例题改编，21 小问）。
用法：把本文件和 订单.xlsx、订单_new.xlsx 放在同一个文件夹，然后 `python 练习1_参考答案.py`。
环境：Python 3.11+ / pandas 2.2+（本机实测 Python 3.14.7 + pandas 3.0.5）。
说明：答案数字与课程页面一致；每步都重新读取原始数据，模拟题目要求的先后顺序。
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无图形界面的环境也能保存图片
import matplotlib.pyplot as plt
import pandas as pd

plt.rcParams["font.sans-serif"] = ["SimHei"]
plt.rcParams["axes.unicode_minus"] = False

HERE = Path(__file__).resolve().parent
RAW = HERE / "订单.xlsx"
NEW = HERE / "订单_new.xlsx"


def title(no, text):
    print("\n" + "=" * 64)
    print("第 %s 题：%s" % (no, text))


# 一、导入导出，查看数据框
title(1, "导入 Pandas，读取数据框，创建 DataFrame")
df = pd.read_excel(RAW)
print(df.head())

title(2, "查看数据框的基本信息")
df.info()

title(3, "查看描述信息与基本形状")
print(df.describe())
print("shape =", df.shape)

title(4, "查看前 3 条记录和后 5 条记录")
print("前 3 条：\n", df.head(3))
print("后 5 条：\n", df.tail(5))

# 二、数据预处理
title(5, "查找缺失值和包含空值的记录")
print("每列是否有缺失：\n", df.isnull().any())
print("每列缺失个数：\n", df.isnull().sum())
print("包含空值的记录：\n", df[df.isnull().any(axis=1)])

title(6, "删除空值记录（原地删除）")
n0 = len(df)
df.dropna(inplace=True)
print("删除前 %d 行，删除后 %d 行" % (n0, len(df)))

title(7, "重新读取后，查找并删除金额为 0 的记录")
df = pd.read_excel(RAW)
zero = df[df["金额"] == 0]
print("金额为 0 的记录：\n", zero[["订单编号", "商品名称", "金额"]])
df.drop(index=zero.index, inplace=True)
print("剩余行数：", len(df))

title(8, "单价 > 150 且数量 > 25 视为异常值，查找并删除")
bad = df[(df["单价"] > 150) & (df["数量"] > 25)]
print("异常记录：\n", bad[["订单编号", "商品名称", "单价", "数量", "金额"]])
df.drop(index=bad.index, inplace=True)
print("剩余行数：", len(df))

title(9, "只导入订单日期、用户ID、商品名称、数量，查看重复记录")
part = pd.read_excel(RAW, usecols=[1, 2, 3, 5])
print("形状：", part.shape, "列：", list(part.columns))
print("重复行数：", part.duplicated().sum())
print(part[part.duplicated(keep=False)].sort_values(list(part.columns)))

title(10, "查询订单金额超过 5000（含）的商品名称和金额")
print(df[df["金额"] >= 5000][["商品名称", "金额"]])
print("说明：第 8 步已删除唯一一笔 5355 元订单，所以这里是空表。")

# 三、数据查询
title(11, "查询“运动服”的订单情况，显示最后 5 条记录")
print(df[df["商品名称"] == "运动服"].tail(5))

title(12, "查询订购数量超过 25（含）的“卫衣”订单信息")
hit = df[(df["商品名称"] == "卫衣") & (df["数量"] >= 25)]
print(hit)
print("共", len(hit), "条")

title(13, "查询订单金额至少为 2000 的记录，并按金额降序排序")
print(df[df["金额"] >= 2000].sort_values("金额", ascending=False))

# 四、数据汇总
title(14, "统计订购总金额")
print("总金额 =", round(df["金额"].sum(), 2))

title(15, "统计休闲鞋订购总数量")
print("休闲鞋总数量 =", df[df["商品名称"] == "休闲鞋"]["数量"].sum())

title(16, "统计每种商品的订购总金额，并按金额降序")
amount = df.groupby("商品名称").agg({"金额": "sum"}).sort_values("金额", ascending=False)
print(amount)

title(17, "按每日订购总金额分档，统计各等级数量")
daily = df.groupby("订单日期").agg({"金额": "sum"})
daily["等级"] = pd.cut(daily["金额"], bins=[3000, 8000, 15000, 23000, 30000, 35000],
                       labels=["L1", "L2", "L3", "L4", "L5"])
print(daily)
print(daily["等级"].value_counts().sort_index())
print("未落档（NaN）：", daily["等级"].isna().sum())

title(18, "制作数据透视表 df1：每周各商品的订购总金额")
df_new = pd.read_excel(NEW)
df1 = df_new.pivot_table(values="金额", index="周次", columns="商品名称",
                         aggfunc="sum", margins=True)
print(df1)

# 五、透视表结果画图与导出
title(19, "用 df1 画第 5 周各商品的订购总金额柱状图")
week5 = df1.loc[5].drop("All")
print(week5.sort_values(ascending=False))
ax = week5.sort_values(ascending=False).plot.bar(figsize=(8, 4.2), color="#306998")
ax.set_title("第 5 周各商品订购总金额")
ax.set_ylabel("金额")
ax.set_xlabel("")
for i, v in enumerate(week5.sort_values(ascending=False).values):
    ax.text(i, v, "%.2f" % v, ha="center", va="bottom", fontsize=9)
plt.tight_layout()
plt.savefig(HERE / "第5周各商品总金额.png", dpi=150)
print("已保存图片：第5周各商品总金额.png")

title(20, "画卫衣各周的订购总金额柱状图")
weiyi = df1["卫衣"].drop("All")
print(weiyi)
ax = weiyi.plot.bar(figsize=(8, 4.2), color="#b8860b")
ax.set_title("卫衣各周订购总金额")
ax.set_ylabel("金额")
ax.set_xlabel("周次")
for i, v in enumerate(weiyi.values):
    ax.text(i, v, "%.2f" % v, ha="center", va="bottom", fontsize=9)
plt.tight_layout()
plt.savefig(HERE / "卫衣各周总金额.png", dpi=150)
print("已保存图片：卫衣各周总金额.png")

title(21, "将数据框导出到 Excel 文件“订单结果.xlsx”")
df.to_excel(HERE / "订单结果.xlsx", index=False)
print("已导出：订单结果.xlsx，行数", len(df))
print("\n练习 1 全部完成。")
