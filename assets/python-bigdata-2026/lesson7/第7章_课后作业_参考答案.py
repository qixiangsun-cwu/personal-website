# -*- coding: utf-8 -*-
"""第 7 章《NumPy 数值计算》课后作业 · 参考答案

配套页面：https://www.qixiangsun.com.cn/python-bigdata-2026-lesson7
运行环境：Python 3.10+ / numpy 2.x（本课页面实测 Python 3.14.7 + numpy 2.5.2）

内容：
  一、页面「三、课后作业（四题）」的参考实现
      作业 1 数组基本功 / 作业 2 索引练习 / 作业 3 成绩统计 / 作业 4 综合（加权总分）
  二、教材第 7 章「股票分析」案例的 8 个小问

说明：
  1. 本文件只依赖 numpy，不读取任何外部文件；数据全部在代码里内联生成，
     随机数都固定了种子，每次运行结果完全一致。
  2. 每道题封装成一个函数，函数内部自备数据，可以单独复制、单独运行。
  3. 作业要求「用数组算，不许写 for 循环逐元素处理」，本文件的统计全部用
     向量化运算 / 聚合函数完成（打印结果时才用 for 循环遍历输出）。

使用方法：
  python 第7章_课后作业_参考答案.py
"""
import sys

import numpy as np

# 让中文在 Windows 控制台正常显示（Python 3.7+ 支持 reconfigure）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BAR = "=" * 70


def section(title):
    """打印一节的标题。"""
    print("\n" + BAR)
    print(title)
    print(BAR)


def show_matrix(name, mat, fmt="{:+.4f}"):
    """按矩阵形式打印浮点数组，避免 numpy 默认输出里的 float64 噪音。"""
    print(name + " =")
    for row in np.atleast_2d(mat):
        print("  [" + " ".join(fmt.format(float(v)) for v in row) + "]")


def show_list(name, arr, nd=2):
    """打印成干净的 Python 列表（保留 nd 位小数）。"""
    vals = np.round(np.asarray(arr, dtype=float), nd).tolist()
    print(name, "=", vals)


def show_int_list(name, arr):
    """打印成干净的 Python 整数列表。"""
    print(name, "=", np.asarray(arr).astype(int).tolist())


# ======================================================================
# 一、页面「三、课后作业（四题）」
# ======================================================================

def homework_1_basics():
    """作业 1：arange / linspace 各生成 0~1 之间的 11 个点；4×5 随机整数矩阵统计。"""
    section("作业 1：数组基本功（arange / linspace / 4×5 随机矩阵）")

    # ① 0~1 之间 11 个点：arange 用“步长”控制，linspace 用“个数”控制
    a = np.arange(0, 1.1, 0.1)
    b = np.linspace(0, 1, 11)
    print("① 0~1 之间的 11 个点")
    print("  np.arange(0, 1.1, 0.1) ->", np.round(a, 10).tolist())
    print("  np.linspace(0, 1, 11)  ->", np.round(b, 10).tolist())
    print("  arange 个数 =", a.size, "，linspace 个数 =", b.size)
    print("  区别：arange 按“步长”算，默认不含终点，还容易因浮点误差多/少一个；")
    print("        linspace 按“个数”算，默认含两端点，元素个数一定正确。")
    print("  把终点写成 1（不含）就能看出差别：")
    print("    np.arange(0, 1, 0.1)  ->", np.arange(0, 1, 0.1).round(10).tolist(), "（10 个，不含 1）")
    print("    np.linspace(0, 1, 11) ->", np.linspace(0, 1, 11).round(10).tolist(), "（11 个，含 1）")

    # ② 4×5 的随机整数矩阵（1~100），固定种子保证结果可复现
    np.random.seed(0)
    m = np.random.randint(1, 101, size=(4, 5))
    print("\n② 4×5 随机整数矩阵（1~100，随机种子 0）：")
    print(m)
    print("  shape =", m.shape, " ndim =", m.ndim, " size =", m.size)
    print("  全表最大值 =", int(m.max()),
          " 最小值 =", int(m.min()),
          " 平均值 =", round(float(m.mean()), 2))


def homework_2_indexing():
    """作业 2：np.arange(1, 51).reshape(5, 10) 的五种取法。"""
    section("作业 2：索引练习（np.arange(1, 51).reshape(5, 10)）")

    a = np.arange(1, 51).reshape(5, 10)
    print("原始矩阵（5 行 × 10 列）：")
    print(a)

    print("\n① 第 2 行：a[1] =", a[1].tolist())
    print("② 第 3 列：a[:, 2] =", a[:, 2].tolist())
    print("③ 右下角 2×3 子块：a[-2:, -3:] =")
    print(a[-2:, -3:])
    print("④ 所有大于 40 的元素：a[a > 40] =", a[a > 40].tolist())
    print("⑤ 所有是 3 的倍数的元素：a[a % 3 == 0] =", a[a % 3 == 0].tolist())


def homework_3_scores():
    """作业 3：8 名学生 × 4 门课的整数成绩矩阵统计。"""
    section("作业 3：成绩统计（8 名学生 × 4 门课，60~100，随机种子 10）")

    np.random.seed(10)
    scores = np.random.randint(60, 101, size=(8, 4))
    ids = np.arange(1, 9)                        # 学号 1~8
    courses = ["语文", "数学", "英语", "编程"]

    print("成绩矩阵（行 = 学生、列 = 课程）：")
    print(scores)

    # ① 每门课平均分：按列求平均，axis=0 表示“沿着行方向压扁”
    means = scores.mean(axis=0)
    print("\n① 每门课平均分（axis=0）：")
    for name, v in zip(courses, means):
        print("  %-4s %.2f" % (name, float(v)))
    show_list("  均值数组", means)

    # ② 每个学生总分：按行求和，axis=1 表示“沿着列方向压扁”
    totals = scores.sum(axis=1)
    print("\n② 每个学生总分（axis=1）：")
    for sid, v in zip(ids, totals):
        print("  学号 %d  总分 %d" % (int(sid), int(v)))
    show_int_list("  总分数组", totals)

    # ③ 每门课最高分及其学生下标（argmax 返回的是“行下标”）
    best = scores.max(axis=0)
    best_idx = scores.argmax(axis=0)
    print("\n③ 每门课最高分及对应学生：")
    for name, v, i in zip(courses, best, best_idx):
        print("  %-4s 最高分 %d  学生下标 %d  学号 %d" % (name, int(v), int(i), int(ids[i])))

    # ④ 总分排名前三的学号（argsort 排的是“下标”，不是分数）
    order = np.argsort(totals)[::-1]             # 升序下标 → 倒序 = 总分从高到低
    print("\n④ 总分前三名：")
    for rank, i in enumerate(order[:3], 1):
        print("  第%d名  学号 %d  总分 %d" % (rank, int(ids[i]), int(totals[i])))
    show_int_list("  前三名学号", ids[order[:3]])


def homework_4_weighted():
    """作业 4：课件成绩表的加权总分 + 两个思考问。"""
    section("作业 4：综合题（课件 10 名学生 5 门课成绩表的加权总分）")

    # 课件【课后题】的原始成绩表（学号 100~109，5 门课）
    scores = np.array([
        [98, 82, 61, 95, 83],
        [80, 54, 56, 87, 56],
        [72, 95, 55, 52, 81],
        [57, 97, 54, 64, 85],
        [78, 77, 76, 76, 56],
        [70, 93, 81, 99, 71],
        [92, 91, 50, 56, 69],
        [52, 65, 80, 91, 85],
        [76, 52, 62, 82, 53],
        [52, 60, 98, 96, 50],
    ])
    ids = np.arange(100, 110)
    courses = ["语文", "数学", "英语", "生物", "历史"]

    # 权重：语文 / 数学 各 0.25，英语 0.20，生物 / 历史 各 0.15（合计 1）
    weight = np.array([0.25, 0.25, 0.20, 0.15, 0.15])
    total = np.dot(scores, weight.T)             # (10, 5) · (5,) -> (10,)

    print("课程顺序：", courses)
    print("权重：", weight.tolist())
    print("\n每位学生的加权总分：")
    for sid, v in zip(ids, total):
        print("  学号 %d  加权总分 %.2f" % (int(sid), float(v)))
    show_list("加权总分数组", total)

    # 用 argsort 排下标，得到降序名次
    order = np.argsort(total)[::-1]
    print("\n加权总分排名（从高到低）：")
    for rank, i in enumerate(order, 1):
        print("  第%2d名  学号 %d  %.2f" % (rank, int(ids[i]), float(total[i])))

    # 思考问 ①：加权总分 vs 简单平均分
    avg = scores.mean(axis=1)
    diff = total - avg
    print("\n【思考问 ①】加权总分比简单平均分更适合衡量什么？")
    print("  简单平均分把 5 门课一视同仁，加权总分按各门课的权重折算。")
    print("  逐人对比（加权总分 - 简单平均）：")
    for sid, w, m, d in zip(ids, total, avg, diff):
        print("  学号 %d  加权 %.2f  简单平均 %.2f  差 %+.2f" % (int(sid), float(w), float(m), float(d)))
    # 用代码找出“权重受益最大 / 最吃亏”的学生，不靠眼看
    up = np.argsort(diff)[::-1][:2]
    down = np.argsort(diff)[:2]
    print("  加权分比简单平均高最多：学号 %s；低最多：学号 %s"
          % (ids[up].tolist(), ids[down].tolist()))
    print("  原因：语文、数学权重最高（各 0.25）。语文 / 数学强的人加权分被“抬”上去，")
    print("        生物、历史相对好的人则相反。所以加权总分更适合衡量“按课程权重折算的")
    print("        综合水平”（评奖学金、算 GPA 就是这种算法），简单平均分更适合看“五科均衡程度”。")

    # 思考问 ②：语文权重 0.25→0.40、数学 0.25→0.10，谁的名次变化最大（用代码算）
    weight2 = np.array([0.40, 0.10, 0.20, 0.15, 0.15])
    total2 = np.dot(scores, weight2)

    def rank_of(x):
        """名次：分数高者靠前，第 1 名为最高分（同分按学号先后）。"""
        return np.argsort(np.argsort(-x)) + 1

    rank1 = rank_of(total)
    rank2 = rank_of(total2)
    delta = rank2 - rank1                         # 正数 = 名次下降（变大），负数 = 名次上升

    print("\n【思考问 ②】把语文权重 0.25→0.40、数学 0.25→0.10，谁的名次变化最大？")
    print("  新权重：", weight2.tolist())
    print("  学号   原加权总分  新加权总分  原名次  新名次  变化")
    for sid, t1, t2, r1, r2, d in zip(ids, total, total2, rank1, rank2, delta):
        arrow = "↑ 上升" if d < 0 else ("↓ 下降" if d > 0 else "— 不变")
        print("  %d   %8.2f   %8.2f   %4d   %4d   %+d（%s）"
              % (int(sid), float(t1), float(t2), int(r1), int(r2), int(d), arrow))

    k = int(np.argmax(np.abs(delta)))             # 绝对值最大的名次变化
    print("\n  名次变化最大的是【学号 %d】：第 %d 名 → 第 %d 名（%+d）"
          % (int(ids[k]), int(rank1[k]), int(rank2[k]), int(delta[k])))
    print("  原因：语文权重提高、数学权重降低，语文好而数学弱的人受益最大，")
    print("        语文弱而数学强的人受损最大。代码用 argsort 排两次名次再相减，")
    print("        不用人工眼看，换数据也能自动找出来。")


# ======================================================================
# 二、教材第 7 章「股票分析」案例（8 个小问）
# ======================================================================

def stock_case():
    """教材第 7 章「股票分析」：4 只股票 × 5 个交易日的涨跌幅分析。"""
    section("教材第 7 章「股票分析」案例（8 个小问）")

    # （1）生成 4 只股票 × 5 个交易日的涨跌幅数据（随机种子 25）
    np.random.seed(25)
    stocks = np.random.rand(4, 5) * 3 - 1.5     # 取值 [-1.5, 1.5)
    stocks = np.round(stocks, 2)
    stocks = stocks / 100
    print("（1）4 只股票 × 5 个交易日的涨跌幅（随机种子 25）：")
    show_matrix("stocks", stocks)
    print("    行 = 股票 A / B / C / D，列 = 交易日 2021-02-01 ~ 2021-02-05")
    print("    注意：值是小数形式的涨跌幅，0.0111 = +1.11%，-0.0115 = -1.15%")

    # （2）投资组合的配置比例
    weight_list = [0.15, 0.20, 0.25, 0.40]
    weight = np.array(weight_list)
    print("\n（2）投资组合权重：", weight.tolist(), "（A / B / C / D，合计", round(float(weight.sum()), 2), "）")

    # （3）① 查 B 股票 2021-02-03 的涨跌幅
    print("\n（3）① B 股票 2021-02-03 的涨跌幅：stocks[1, 2] = %+.4f"
          % float(stocks[1, 2]))

    # （3）② 跌幅小于 -0.01 的股票
    mask = stocks < -0.01
    print("    ② 跌幅小于 -0.01（即跌超 1%）的涨跌幅：stocks[stocks < -0.01] =",
          [round(float(v), 4) for v in stocks[mask]])
    rows, cols = np.where(mask)
    print("       np.where(stocks < -0.01) =", (rows.tolist(), cols.tolist()))
    names = ["A", "B", "C", "D"]
    dates = ["2021-02-01", "2021-02-02", "2021-02-03", "2021-02-04", "2021-02-05"]
    for rc in zip(rows, cols):
        pair = (int(rc[0]), int(rc[1]))
        print("       rc = %s, data = %+.4f   → 股票 %s，%s"
              % (str(pair), float(stocks[pair]), names[pair[0]], dates[pair[1]]))

    # （3）③ C 股票和 D 股票 2021-02-02 起的涨跌幅（教材写法 stocks[2:, 1:4]）
    print("    ③ C / D 股票 2021-02-02 起的涨跌幅：stocks[2:, 1:4] =")
    print("       （教材这个切片取的是 02-02 ~ 02-04 三天）")
    print(np.round(stocks[2:, 1:4], 4))
    print("       补充：要把 02-05 也包含进来，切片写 stocks[2:, 1:5]（4 天）：")
    print(np.round(stocks[2:, 1:5], 4))

    # （4）按每个交易日排序：对每一列（同一交易日的 4 只股票）排序
    print("\n（4）按每个交易日的涨跌幅排序：np.sort(stocks, axis=0) =")
    print(np.round(np.sort(stocks, axis=0), 4))

    # （5）按每只股票排序：对每一行（同一只股票的 5 天）排序
    print("（5）按每只股票的涨跌幅排序：np.sort(stocks, axis=1) =")
    print(np.round(np.sort(stocks, axis=1), 4))

    # （6）每个交易日的投资组合加权收益率
    r1 = np.matmul(weight, stocks)
    print("\n（6）每个交易日的加权收益率：r1 = np.matmul(weight, stocks) =")
    print("     ", np.round(r1, 4).tolist())

    # （7）每个交易日的实际收益和 5 个交易日的总收益
    r2 = r1 * 1000000
    r3 = np.sum(r2)
    print("\n（7）本金 100 万元时每个交易日的实际收益：r2 = r1 * 1000000 =")
    print("     ", [round(float(v), 2) for v in r2])
    print("     5 个交易日的总收益：r3 = np.sum(r2) = %.2f 元" % float(r3))

    # （8）5 个交易日中的最小涨幅及对应股票
    mn = np.min(stocks)
    r4 = np.argmin(stocks)
    q, rem = divmod(int(r4), stocks[0].size)
    print("\n（8）5 个交易日中的最小涨幅：np.min(stocks) = %+.4f" % float(mn))
    print("     展平下标：np.argmin(stocks) = %d" % int(r4))
    print("     divmod(%d, %d) = %s → 第 %d 行第 %d 列 → 股票 %s，%s"
          % (int(r4), stocks[0].size, str((q, rem)), q, rem, names[q], dates[rem]))
    print("     （np.min 取到的是“最小涨幅”，也就是跌幅最大的那一天：股票 %s 在 %s 的"
          "涨跌幅为 %.2f%%）" % (names[q], dates[rem], float(mn) * 100))


def main():
    homework_1_basics()
    homework_2_indexing()
    homework_3_scores()
    homework_4_weighted()
    stock_case()
    print("\n全部完成。")


if __name__ == "__main__":
    main()
