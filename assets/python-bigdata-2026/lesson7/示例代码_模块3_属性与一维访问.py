# -*- coding: utf-8 -*-
"""第 7 章《NumPy 数值计算》示例代码 · 模块 3：属性与一维访问

配套页面：https://www.qixiangsun.com.cn/python-bigdata-2026-lesson7
运行环境：Python 3.10+ / numpy 2.x（本课页面实测 Python 3.14.7 + numpy 2.5.2）
说明：本模块包括 shape / ndim / size / dtype · 一维索引 · 切片 · 视图与副本 · 花式索引。
      本文件按页面顺序排列，每段都独立可运行，段与段之间用 ===== 分隔；
      页面上标注“故意报错”的示例未收录（运行会中断），请到页面查看。
用法：python 示例代码_模块3_属性与一维访问.py
"""
import numpy as np

# ===== [k3-attrs] 一维与二维数组的属性对照：shape、ndim、size、dtype =====
import numpy as np

a1 = np.arange(10)
a2 = np.array([[1, 2, 3], [4, 5, 6]])

print('a1.shape：', a1.shape)
print('a1.ndim：', a1.ndim)
print('a1.size：', a1.size)
print('a2.shape：', a2.shape)
print('a2.ndim：', a2.ndim)
print('a2.size：', a2.size)
print('a2.dtype：', a2.dtype)
print('两维相乘：', a2.shape[0] * a2.shape[1])


# ===== [k3-index] 正下标与负下标：a[0]、a[3]、a[-1]、a[-2] =====
import numpy as np

a = np.arange(10, 21, 2)
print('a：', a)
print('a[0]：', a[0])
print('a[3]：', a[3])
print('a[-1]：', a[-1])
print('a[-2]：', a[-2])
print('两端的值相加：', a[0] + a[-1])


# ===== [k3-slice] 切片的四种常用写法：取前 3 个、隔一个取一个、倒序 =====
import numpy as np

a = np.arange(10, 21, 2)
print('a：', a)
print('a[0:3]：', a[0:3])
print('a[:3]：', a[:3])
print('a[2:]：', a[2:])
print('a[::2]：', a[::2])
print('a[::-1]：', a[::-1])
print('a[1:5:2]：', a[1:5:2])


# ===== [k3-view] 证明切片是视图：改切片 → 原数组跟着变；.copy() 才是独立副本 =====
import numpy as np

a = np.arange(10, 21, 2)
s = a[0:3]
print('改之前 a：', a)
s[0] = 99
print('改 s[0] 之后 a：', a)
print('共享内存吗：', np.shares_memory(a, s))

c = a[0:3].copy()
c[0] = -1
print('改 copy 之后 a：', a)
print('copy 的内容：', c)


# ===== [k3-fancy] 列表下标：挑选、乱序、重复，而且拿到的是副本 =====
import numpy as np

a = np.arange(10, 21, 2)
print('a：', a)
print('a[[1, 2, 5]]：', a[[1, 2, 5]])
print('乱序 a[[5, 0, 1]]：', a[[5, 0, 1]])
print('重复 a[[0, 0, 0]]：', a[[0, 0, 0]])

f = a[[0, 1]]
f[0] = 77
print('改副本之后 a：', a)
print('副本 f：', f)


# 注：页面上这些示例是“故意报错”的，未收录到本文件：k3-index-err
