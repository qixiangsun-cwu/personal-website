# -*- coding: utf-8 -*-
"""第 7 章《NumPy 数值计算》示例代码 · 模块 1：NumPy 与 ndarray

配套页面：https://www.qixiangsun.com.cn/python-bigdata-2026-lesson7
运行环境：Python 3.10+ / numpy 2.x（本课页面实测 Python 3.14.7 + numpy 2.5.2）
说明：本模块包括 列表 vs 数组 · ndarray 的三条规矩 · 第一个 NumPy 数组 · 打印与类型陷阱。
      本文件按页面顺序排列，每段都独立可运行，段与段之间用 ===== 分隔；
      页面上标注“故意报错”的示例未收录（运行会中断），请到页面查看。
用法：python 示例代码_模块1_NumPy与ndarray.py
"""
import numpy as np

# ===== [k1-why] 列表做不了数学，数组可以：乘 2、加 2、平方、求和 =====
import numpy as np

nums = [1, 2, 3]
arr = np.array([1, 2, 3])

print('列表 * 2：', nums * 2)
try:
    print(nums + 2)
except TypeError as e:
    print('列表 + 2 报错：', e)

print('数组 * 2：', arr * 2)
print('数组 + 2：', arr + 2)
print('数组 ** 2：', arr ** 2)
print('数组求和：', arr.sum())


# ===== [k1-rules] 类型统一与大小固定：混着写会被提升，想加元素只能新建 =====
import numpy as np

a = np.array([1, 2, 3])
b = np.array([1, 2, 3.5])
c = np.array([1, 2, '3'])

print('a 的 dtype：', a.dtype)
print('b 的 dtype：', b.dtype)
print('c 的 dtype：', c.dtype)
print('b 的内容：', b)
print('c 的内容：', c)
print('数组有 append 吗：', hasattr(a, 'append'))
print('np.append 其实返回新数组：', np.append(a, 4))
print('原来的 a 还是：', a)


# ===== [k1-first] 第一个数组：print、type、repr、tolist 各看到什么 =====
import numpy as np

arr = np.array([1, 2, 3])
print('直接 print：', arr)
print('类型：', type(arr))
print('repr 回显：', repr(arr))
print('转回列表：', arr.tolist())

print('二维数组：')
print(np.array([[1, 2, 3], [4, 5, 6]]))


# ===== [k1-print-traps] 两个输出坑：np.float64 污染列表、浮点尾巴 =====
import numpy as np

arr = np.array([0.1, 0.2, 0.3])
print('直接求和：', arr.sum())
print('保留两位：', np.round(arr.sum(), 2))

v = np.float64(1.5)
print('放进列表（丑）：', [v])
print('先转 float（对）：', [float(v)])
print('切一片再 tolist：', arr[:2].tolist())
print('数组本身：', arr)

