# -*- coding: utf-8 -*-
"""第 7 章《NumPy 数值计算》示例代码 · 模块 5：数组的运算

配套页面：https://www.qixiangsun.com.cn/python-bigdata-2026-lesson7
运行环境：Python 3.10+ / numpy 2.x（本课页面实测 Python 3.14.7 + numpy 2.5.2）
说明：本模块包括 转置 T · 逐元素运算 · 点积 dot · 广播机制。
      本文件按页面顺序排列，每段都独立可运行，段与段之间用 ===== 分隔；
      页面上标注“故意报错”的示例未收录（运行会中断），请到页面查看。
用法：python 示例代码_模块5_数组的运算.py
"""
import numpy as np

# ===== [k5-transpose] 一维转置不变、二维 (2,3) 变 (3,2)：形状和内容一起看 =====
import numpy as np

a1 = np.array([1, 2, 3])
print('一维 a1：', a1, ' 形状：', a1.shape)
print('一维 a1.T：', a1.T, ' 形状：', a1.T.shape)

a2 = np.array([[1, 2, 3], [5, 6, 7]])
print('a2 的形状：', a2.shape)
print(a2)
print('a2.T 的形状：', a2.T.shape)
print(a2.T)
print('转置两次又回来了：', bool((a2.T.T == a2).all()))


# ===== [k5-elemwise] 加减乘除与标量：所有运算符都是逐元素的 =====
import numpy as np

a = np.array([1, 2, 3])
b = np.array([10, 20, 30])
print('a + b =', a + b)
print('a - b =', a - b)
print('a * b =', a * b)        # 逐元素相乘，不是矩阵乘法
print('b / a =', b / a)
print('a ** 2 =', a ** 2)
print('a * 2（跟标量算）=', a * 2)
print('b // 7 =', b // 7)
print('b % 7 =', b % 7)


# ===== [k5-dot] 同一对数组：* 逐元素 vs np.dot 矩阵乘；外加一维内积和课件那道 (2,3)·(3,2) =====
import numpy as np

x = np.array([[1, 2], [3, 4]])
y = np.array([[5, 6], [7, 8]])
print('同一对数组，两种乘法：')
print('x * y（逐元素）：')
print(x * y)
print('np.dot(x, y)（矩阵乘法）：')
print(np.dot(x, y))
print('x @ y 与 np.dot 等价：', bool((x @ y == np.dot(x, y)).all()))

a = np.array([1, 2, 1])
b = np.array([3, 4, 5])
print('一维 dot = 内积：1*3 + 2*4 + 1*5 =', int(np.dot(a, b)))

m = np.array([[1, 0, 1], [0, 1, 1]])      # (2,3)
n = np.array([[4, 1], [2, 2], [2, 4]])    # (3,2)
print('(2,3) 乘 (3,2) 得 (2,2)：')
print(np.dot(m, n))


# ===== [k5-broadcast] (3,3) 加 (3,) 和加 (3,1)：短的一方怎么铺开，结果形状是多少 =====
import numpy as np

m = np.arange(9).reshape(3, 3)
print('m 的形状：', m.shape)
print(m)
row = np.array([100, 200, 300])
print('row 的形状：', row.shape)
print('m + row（row 被铺到每一行）：')
print(m + row)

col = np.array([[10], [20], [30]])
print('col 的形状：', col.shape)
print('m + col（col 被铺到每一列）：')
print(m + col)
print('(3,1) 乘 (1,4) 广播成：', (np.arange(3).reshape(3, 1) * np.arange(1, 5)).shape)


# 注：页面上这些示例是“故意报错”的，未收录到本文件：k5-bcast-fail
