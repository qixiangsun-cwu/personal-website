# -*- coding: utf-8 -*-
"""第 7 章《NumPy 数值计算》示例代码 · 模块 2：数组的创建

配套页面：https://www.qixiangsun.com.cn/python-bigdata-2026-lesson7
运行环境：Python 3.10+ / numpy 2.x（本课页面实测 Python 3.14.7 + numpy 2.5.2）
说明：本模块包括 np.array 建数组 · arange / linspace · zeros / ones / full · reshape。
      本文件按页面顺序排列，每段都独立可运行，段与段之间用 ===== 分隔；
      页面上标注“故意报错”的示例未收录（运行会中断），请到页面查看。
用法：python 示例代码_模块2_数组的创建.py
"""
import numpy as np

# ===== [k2-array] 从列表、元组、嵌套列表建数组，并控制 dtype =====
import numpy as np

print('列表建数组：', np.array([1, 2, 3]))
print('元组也行：', np.array((4, 5, 6)))
print('指定整数类型：', np.array([1.7, 2.2], dtype=int))
print('指定浮点类型：', np.array([1, 2], dtype=float))

print('嵌套列表 → 二维数组：')
print(np.array([[1, 2, 3], [4, 5, 6]]))

print('自动推断：', np.array([1, 2, 3]).dtype)
print('二维的形状：', np.array([[1, 2, 3], [4, 5, 6]]).shape)


# ===== [k2-arange-linspace] arange 不含 stop，linspace 含两端：跑一遍看长度 =====
import numpy as np

print('arange(10, 21, 2)：', np.arange(10, 21, 2))
print('arange(0, 10, 3)：', np.arange(0, 10, 3))
print('arange(5)：', np.arange(5))
print('arange(0, 1, 0.25)：', np.arange(0, 1, 0.25))
print('linspace(0, 1, 5)：', np.linspace(0, 1, 5))
print('linspace(0, 10, 3)：', np.linspace(0, 10, 3))
print('看长度：', len(np.arange(0, 1, 0.25)), 'vs', len(np.linspace(0, 1, 5)))


# ===== [k2-zeros] 占位四件套：zeros / ones / full / eye =====
import numpy as np

print('zeros(3)：', np.zeros(3))
print('zeros((2, 3))：')
print(np.zeros((2, 3)))
print('ones(3, dtype=int)：', np.ones(3, dtype=int))
print('full((2, 2), 7)：')
print(np.full((2, 2), 7))
print('eye(3)：')
print(np.eye(3))
print('zeros 的默认类型：', np.zeros(3).dtype)


# ===== [k2-reshape] reshape 改形状：2×3、3×2、-1 自动推断 =====
import numpy as np

a = np.arange(6)
print('原数组：', a)

print('reshape(2, 3)：')
print(a.reshape(2, 3))

print('reshape(3, -1)：')
print(a.reshape(3, -1))

print('reshape(-1) 拉平：', a.reshape(-1))
print('原数组没变：', a)


# 注：页面上这些示例是“故意报错”的，未收录到本文件：k2-array-err、k2-reshape-err
