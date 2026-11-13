# -*- coding: utf-8 -*-
"""第 7 章《NumPy 数值计算》示例代码 · 模块 4：二维访问与布尔索引

配套页面：https://www.qixiangsun.com.cn/python-bigdata-2026-lesson7
运行环境：Python 3.10+ / numpy 2.x（本课页面实测 Python 3.14.7 + numpy 2.5.2）
说明：本模块包括 二维索引 · 二维切片 · 视图 · 布尔索引。
      本文件按页面顺序排列，每段都独立可运行，段与段之间用 ===== 分隔；
      页面上标注“故意报错”的示例未收录（运行会中断），请到页面查看。
用法：python 示例代码_模块4_二维访问与布尔索引.py
"""
import numpy as np

# ===== [k4-index2d] a[0] 取整行、a[1,1] 取单个元素：一维和二维的差别就在下标的个数 =====
import numpy as np

a = np.array([[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]])
print('整个数组 a：')
print(a)
print('a[0] 取整行：', a[0])
print('a[0] 的形状：', a[0].shape)
print('a[1,1] 取单个元素：', int(a[1, 1]))
print('a[2,0] 取单个元素：', int(a[2, 0]))
print('a[-1] 取最后一行：', a[-1])
print('a[1] 取第 1 行：', a[1])


# ===== [k4-slice2d] 行切片、整列、指定列、行+列组合切片，一次看全 =====
import numpy as np

a = np.array([[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]])
print('a[0:2] 前两行：')
print(a[0:2])
print('a[:, 0] 第 0 列：', a[:, 0])
print('a[:, [0, 2]] 只要第 0、2 列：')
print(a[:, [0, 2]])
print('a[0:2, 1:] 前两行 + 后两列：')
print(a[0:2, 1:])
print('a[[0, 2]] 取第 0、2 两行：')
print(a[[0, 2]])
print('a[0, 2] 取第 0 行第 2 列：', int(a[0, 2]))
print('a[:, 0] 的形状：', a[:, 0].shape, ' a[:, 0:1] 的形状：', a[:, 0:1].shape)


# ===== [k4-view] 改切片会影响原数组：二维切片同样是视图 =====
import numpy as np

a = np.array([[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]])
col = a[:, 0]                 # 切片取出来的是视图，不是副本
print('a[:, 0] 取出来：', col)
col[0] = 100                  # 改这个视图
print('改完 col[0] 之后，a 变成：')
print(a)
print('原数组也被改了 —— 这就是视图，不是复制')


# ===== [k4-boolmask] 单条件、多条件、统计满足条件的个数：布尔索引的三种常用写法 =====
import numpy as np

a = np.arange(10, 21, 2)
print('a =', a)
print('a > 15 得到布尔数组：', a > 15)
print('a[a > 15] 挑出满足条件的：', a[a > 15])
print('a[a % 3 == 0]：', a[a % 3 == 0])
# 多条件：每个条件都要加括号，用 & | 连接
print('(a > 10) & (a < 20)：', a[(a > 10) & (a < 20)])
print('(a == 10) | (a == 20)：', a[(a == 10) | (a == 20)])
print('掩码里 True 的个数：', int((a > 15).sum()))

