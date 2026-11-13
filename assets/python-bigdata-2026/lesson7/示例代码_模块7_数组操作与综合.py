# -*- coding: utf-8 -*-
"""第 7 章《NumPy 数值计算》示例代码 · 模块 7：数组操作与综合

配套页面：https://www.qixiangsun.com.cn/python-bigdata-2026-lesson7
运行环境：Python 3.10+ / numpy 2.x（本课页面实测 Python 3.14.7 + numpy 2.5.2）
说明：本模块包括 排序 sort · reshape / ravel · concat · 成绩表综合案例 · 加权总分 · 前三名 · 教材股票组合案例（np.where / matmul / argmin + divmod） · 张量。
      本文件按页面顺序排列，每段都独立可运行，段与段之间用 ===== 分隔；
      页面上标注“故意报错”的示例未收录（运行会中断），请到页面查看。
用法：python 示例代码_模块7_数组操作与综合.py
"""
import numpy as np

# ===== [k7-sort-func] 课件 slide16 复现：np.sort(x) 不动原数组，x.sort() 原地改 =====
import numpy as np

np.random.seed(10)                             # 固定随机种子，结果可复现
x = np.round(np.random.rand(6) * 50, 2)        # 6 个 0~50 的随机小数
print('原始 x      =', x.tolist())

y = np.sort(x)                                 # 函数：返回新数组
print('np.sort(x)  =', y.tolist())
print('x 没被改动   =', x.tolist())

ret = x.sort()                                 # 方法：原地排序
print('x.sort() 的返回值 =', ret)               # None
print('x 被改成     =', x.tolist())


# ===== [k7-sort-axis] 课件 slide17 复现：二维排序，默认 axis=-1，指定 axis=0 结果完全不同 =====
import numpy as np

x = np.array([[0, 4, 3],
              [2, 2, 1]])
print('原数组 x：')
print(x)

print('np.sort(x)：默认 axis=-1，等于 axis=1，每一行内部排')
print(np.sort(x))

print('np.sort(x, axis=0)：同一列上下排')
print(np.sort(x, axis=0))

x.sort(axis=0)                                  # 方法版原地排
print('x.sort(axis=0) 之后，x 自己被改了：')
print(x)


# ===== [k7-reshape] reshape：只换形状不换数据，-1 让 NumPy 自己算那一维 =====
import numpy as np

a = np.arange(12)                               # 0 ~ 11，共 12 个数
print('a =', a.tolist())
print('a.shape =', a.shape)

b = a.reshape(3, 4)                             # 3 行 4 列
print('reshape(3, 4) 之后：')
print(b)
print('b.shape =', b.shape)

c = b.reshape(-1)                               # -1：这一维让 NumPy 自己算
print('reshape(-1) 拉回一维：', c.tolist())

d = b.reshape(2, -1)                            # 2 行，列数自己算 → 6
print('reshape(2, -1) 的形状：', d.shape)


# ===== [k7-ravel] ravel 是视图、flatten 是副本：改一下就知道差别 =====
import numpy as np

a = np.array([[1, 2, 3],
              [4, 5, 6]])
r = a.ravel()                                   # 展平，返回视图
f = a.flatten()                                 # 展平，返回副本
print('ravel()   =', r.tolist())
print('flatten() =', f.tolist())

r[0] = 99                                       # 改视图 → 原数组跟着变
print('改 r[0] 之后 a：')
print(a)

f[1] = 88                                       # 改副本 → 原数组不受影响
print('改 f[1] 之后 f：', f.tolist())
print('同一时刻 a 还是：')
print(a)


# ===== [k7-concat] vstack 竖着摞（加行）、hstack 横着接（加列） =====
import numpy as np

a = np.array([[1, 2],
              [3, 4]])
b = np.array([[5, 6]])

print('a 的形状：', a.shape, ' b 的形状：', b.shape)
print('np.vstack((a, b))：竖着摞，行数变 3')
print(np.vstack((a, b)))

c = np.array([[7],
              [8]])
print('c 的形状：', c.shape)
print('np.hstack((a, c))：横着接，列数变 3')
print(np.hstack((a, c)))


# ===== [k7-concat-axis] concatenate 指定 axis 拼，append 末尾追加（每次都返回新数组） =====
import numpy as np

x = np.array([1, 2, 3])
y = np.array([4, 5, 6])
print('一维 concatenate：', np.concatenate((x, y)).tolist())

m = np.array([[1, 2], [3, 4]])
n = np.array([[5, 6], [7, 8]])
print('axis=0：按行拼，行数从 2 变 4')
print(np.concatenate((m, n), axis=0))
print('axis=1：按列拼，列数从 2 变 4')
print(np.concatenate((m, n), axis=1))

print('append 返回新数组：', np.append(x, 7).tolist())
print('原数组 x 没变：', x.tolist())


# ===== [k7-scores-build] （1）建数组：10 行 × 5 列，行是学生、列是课程 =====
import numpy as np

scores = np.array([
    [98, 82, 61, 95, 83],   # 学生 100
    [80, 54, 56, 87, 56],   # 学生 101
    [72, 95, 55, 52, 81],   # 学生 102
    [57, 97, 54, 64, 85],   # 学生 103
    [78, 77, 76, 76, 56],   # 学生 104
    [70, 93, 81, 99, 71],   # 学生 105
    [92, 91, 50, 56, 69],   # 学生 106
    [52, 65, 80, 91, 85],   # 学生 107
    [76, 52, 62, 82, 53],   # 学生 108
    [52, 60, 98, 96, 50],   # 学生 109
])
print('scores.shape =', scores.shape, ' ndim =', scores.ndim, ' size =', scores.size)
print('学生数 =', scores.shape[0], ' 课程数 =', scores.shape[1])
print(scores)


# ===== [k7-scores-axis0] （2）五门课的平均 / 最高 / 最低：axis=0，10 行压成 1 行 =====
import numpy as np

scores = np.array([[98, 82, 61, 95, 83], [80, 54, 56, 87, 56],
                   [72, 95, 55, 52, 81], [57, 97, 54, 64, 85],
                   [78, 77, 76, 76, 56], [70, 93, 81, 99, 71],
                   [92, 91, 50, 56, 69], [52, 65, 80, 91, 85],
                   [76, 52, 62, 82, 53], [52, 60, 98, 96, 50]])
courses = ['语文', '数学', '英语', '生物', '历史']

avg = np.mean(scores, axis=0)        # 压掉学生这一维 → 5 个数
hi = np.max(scores, axis=0)
lo = np.min(scores, axis=0)
for i in range(5):
    print(f'{courses[i]}  平均 {float(avg[i]):.2f}  最高 {int(hi[i])}  最低 {int(lo[i])}')


# ===== [k7-scores-axis1] （3）每个学生的最高 / 最低：axis=1，5 列压成 1 个；再查最高分是哪门课 =====
import numpy as np

scores = np.array([[98, 82, 61, 95, 83], [80, 54, 56, 87, 56],
                   [72, 95, 55, 52, 81], [57, 97, 54, 64, 85],
                   [78, 77, 76, 76, 56], [70, 93, 81, 99, 71],
                   [92, 91, 50, 56, 69], [52, 65, 80, 91, 85],
                   [76, 52, 62, 82, 53], [52, 60, 98, 96, 50]])
ids = np.arange(100, 110)             # 学号：100 ~ 109
courses = ['语文', '数学', '英语', '生物', '历史']

top = np.max(scores, axis=1)          # 压掉课程这一维 → 10 个数
low = np.min(scores, axis=1)
best = np.argmax(scores, axis=1)      # 最高分落在第几列
for i in range(10):
    print(f'学生{int(ids[i])}  最高 {int(top[i])}（{courses[int(best[i])]}）  最低 {int(low[i])}')


# ===== [k7-weighted] （4）加权总分：np.dot(scores, weight.T)，一行算出一个总分 =====
import numpy as np

scores = np.array([[98, 82, 61, 95, 83], [80, 54, 56, 87, 56],
                   [72, 95, 55, 52, 81], [57, 97, 54, 64, 85],
                   [78, 77, 76, 76, 56], [70, 93, 81, 99, 71],
                   [92, 91, 50, 56, 69], [52, 65, 80, 91, 85],
                   [76, 52, 62, 82, 53], [52, 60, 98, 96, 50]])
weight = np.array([0.25, 0.25, 0.20, 0.15, 0.15])
ids = np.arange(100, 110)

total = np.dot(scores, weight.T)         # (10, 5) · (5,) → (10,)
print('每位学生的加权总分：')
for i in range(10):
    print(f'学生{int(ids[i])}  {round(float(total[i]), 2)}')


# ===== [k7-top3] 总分前三名：argsort 排下标 → 取 [-3:] → 再 [::-1] 翻成降序 =====
import numpy as np

scores = np.array([[98, 82, 61, 95, 83], [80, 54, 56, 87, 56],
                   [72, 95, 55, 52, 81], [57, 97, 54, 64, 85],
                   [78, 77, 76, 76, 56], [70, 93, 81, 99, 71],
                   [92, 91, 50, 56, 69], [52, 65, 80, 91, 85],
                   [76, 52, 62, 82, 53], [52, 60, 98, 96, 50]])
weight = np.array([0.25, 0.25, 0.20, 0.15, 0.15])
ids = np.arange(100, 110)
total = np.dot(scores, weight.T)

order = np.argsort(total)                # 升序排的是"下标"
print('argsort 的结果（升序下标）：', order.tolist())

top3 = order[-3:][::-1]                  # 取最后 3 个，再倒过来 = 前三名
print('总分前三名：')
for rank, i in enumerate(top3, 1):
    print(f'第{rank}名  学号 {int(ids[i])}  总分 {round(float(total[i]), 2)}')


# ===== [k7-argsort-why] 直接 np.sort(total)[-3:] 只拿到分数、拿不到人：argsort 才有位置信息 =====
import numpy as np

scores = np.array([[98, 82, 61, 95, 83], [80, 54, 56, 87, 56],
                   [72, 95, 55, 52, 81], [57, 97, 54, 64, 85],
                   [78, 77, 76, 76, 56], [70, 93, 81, 99, 71],
                   [92, 91, 50, 56, 69], [52, 65, 80, 91, 85],
                   [76, 52, 62, 82, 53], [52, 60, 98, 96, 50]])
weight = np.array([0.25, 0.25, 0.20, 0.15, 0.15])
ids = np.arange(100, 110)
total = np.dot(scores, weight.T)

print('直接排序取后三个：', np.round(np.sort(total)[-3:], 2).tolist())
print('→ 分数是有了，但不知道是谁的')

order = np.argsort(total)
print('argsort 的下标：', order.tolist())
print('用下标去学号里取人：', ids[order[-3:][::-1]].tolist())
print('再用同一批下标取分：', np.round(total[order[-3:][::-1]], 2).tolist())


# ===== [k7-stock-build] 固定种子生成 4×5 的涨跌幅矩阵，顺带查一个单值（B 股票 2/3） =====
import numpy as np

np.random.seed(25)                              # 固定种子：每次运行结果一样
stocks = np.random.rand(4, 5) * 3 - 1.5         # 4 只股票 × 5 个交易日，范围 [-1.5, 1.5)
stocks = np.round(stocks, 2)                    # 先保留两位小数
stocks = stocks / 100                           # 再除以 100，换成“小数形式”的涨跌幅

print('形状 / 维度 / 元素个数：', stocks.shape, stocks.ndim, stocks.size)
print(stocks)
print('B 股票 2021-02-03 的涨跌幅：', stocks[1, 2])


# ===== [k7-stock-query] 布尔索引找“跌幅超过 1%”、np.where 给位置、切片取 C/D 股票、两种 axis 排序 =====
import numpy as np

np.random.seed(25)
stocks = np.round(np.random.rand(4, 5) * 3 - 1.5, 2) / 100

mask = stocks < -0.01                           # 跌幅超过 1% 的位置
print('跌幅小于 -0.01 的涨跌幅：', stocks[mask])
rows, cols = np.where(mask)                     # 布尔掩码的“位置版”：分别给出行、列下标
print('对应行下标：', rows, ' 列下标：', cols)
print('C、D 股票在 2 月 2 日—2 月 5 日的涨跌幅：')
print(stocks[2:, 1:])
print('按交易日排序（axis=0，每天 4 只股票从低到高）：')
print(np.sort(stocks, axis=0))
print('按每只股票排序（axis=1，每只股票 5 天从低到高）：')
print(np.sort(stocks, axis=1))


# ===== [k7-stock-portfolio] 加权收益率 = 权重 · 涨跌幅矩阵；再乘本金算总收益，最后用 divmod 还原最小涨幅的位置 =====
import numpy as np

np.random.seed(25)
stocks = np.round(np.random.rand(4, 5) * 3 - 1.5, 2) / 100
weight = np.array([0.15, 0.20, 0.25, 0.40])     # A~D 四只股票的配置比例

daily = np.matmul(weight, stocks)               # 每天的组合加权收益率：4 只股票按权重加权
print('每日组合收益率：', daily)
print('按 100 万本金折算，每日收益（元）：', daily * 1000000)
print('5 个交易日总收益（元）：', np.sum(daily * 1000000))

pos = np.argmin(stocks)                         # 最小涨幅在“压平”后的数组里是第几个
print('最小涨幅：', np.min(stocks), ' 压平下标：', pos)
print('还原成行列（行, 列）：', divmod(int(pos), stocks[0].size))


# ===== [k7-ai-tensor] ndarray 就是 CPU 上的张量：同一套 shape / 广播 / 点积 / 归约 =====
import numpy as np

# 下面每一行的写法，在 PyTorch 里只差一个 torch. 前缀，语义完全一样
x = np.array([[1.0, 2.0, 3.0],
              [4.0, 5.0, 6.0]])
print('shape =', x.shape, ' dtype =', x.dtype)

print('逐元素 x + 1（广播）：')
print(x + 1)

print('按列求均值 axis=0：', np.round(x.mean(axis=0), 2).tolist())

w = np.array([0.5, 0.5, 0.5])
print('矩阵乘向量 dot：', np.round(np.dot(x, w), 2).tolist())

z = np.array([2.0, 1.0, 0.1])
e = np.exp(z - np.max(z))                   # 减去最大值，防止溢出
print('手写 softmax：', np.round(e / np.sum(e), 4).tolist())

