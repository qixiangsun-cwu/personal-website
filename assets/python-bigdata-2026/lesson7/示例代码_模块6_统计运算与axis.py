# -*- coding: utf-8 -*-
"""第 7 章《NumPy 数值计算》示例代码 · 模块 6：统计运算与 axis

配套页面：https://www.qixiangsun.com.cn/python-bigdata-2026-lesson7
运行环境：Python 3.10+ / numpy 2.x（本课页面实测 Python 3.14.7 + numpy 2.5.2）
说明：本模块包括 常用统计函数 · 累计运算 · axis 参数 · 成绩表按行 / 按列统计 · 打印控制。
      本文件按页面顺序排列，每段都独立可运行，段与段之间用 ===== 分隔；
      页面上标注“故意报错”的示例未收录（运行会中断），请到页面查看。
用法：python 示例代码_模块6_统计运算与axis.py
"""
import numpy as np

# ===== [k6-statfunc] 课件那 8 个数：最小、最大、求和、平均、中位、标准差、方差、位置 =====
import numpy as np

x = np.array([19, 14, 25, 10, 27, 26, 27, 18])
print('x =', x)
print('np.min 最小值：', int(np.min(x)), ' np.argmin 最小值位置：', int(np.argmin(x)))
print('np.max 最大值：', int(np.max(x)), ' np.argmax 最大值位置：', int(np.argmax(x)))
print('np.sum 求和：', int(np.sum(x)))
print('np.mean 平均：', float(np.mean(x)))
print('np.median 中位数：', float(np.median(x)))
print('np.std 标准差：', round(float(np.std(x)), 2))
print('np.var 方差：', round(float(np.var(x)), 2))
print('np.percentile 75% 分位：', float(np.percentile(x, 75)))


# ===== [k6-cum] cumsum 累加、cumprod 累乘、prod 连乘、percentile 三个分位 =====
import numpy as np

x = np.array([19, 14, 25, 10, 27, 26, 27, 18])
print('原数组 x：', x)
print('np.cumsum 累加：', np.cumsum(x))
print('np.cumprod 累乘（后面数太大，只看到第 4 个）：', np.cumprod(x)[:4])
print('np.prod 连乘前 4 个：', int(np.prod(x[:4])))
print('np.percentile(x, 25)：', float(np.percentile(x, 25)))
print('np.percentile(x, 50) 就是中位数：', float(np.percentile(x, 50)))
print('np.percentile(x, 80)：', float(np.percentile(x, 80)))


# ===== [k6-axis] 同一个 3×4 数组：不写 axis、axis=0、axis=1 三种结果的长度不一样 =====
import numpy as np

a = np.arange(1, 13).reshape(3, 4)     # 3 行 4 列
print('a 的形状：', a.shape)
print(a)
print('不写 axis（整个数组一起算）：', int(np.sum(a)))
print('axis=0：把 3 行压掉，每列出一个数')
print(np.sum(a, axis=0), '→ 形状', np.sum(a, axis=0).shape)
print('axis=1：把 4 列压掉，每行出一个数')
print(np.sum(a, axis=1), '→ 形状', np.sum(a, axis=1).shape)
print('每列平均 axis=0：', np.round(np.mean(a, axis=0), 2))
print('每行最大 axis=1：', np.max(a, axis=1))
print('每列最小 axis=0：', np.min(a, axis=0))


# ===== [k6-table] 3×4 成绩表一次算清：每门课平均、每名学生最高与总分、全表最高分在哪 =====
import numpy as np

# 3 名学生、4 门课：行 = 学生，列 = 课程
s = np.array([[88, 92, 76, 90],
              [72, 85, 68, 79],
              [95, 60, 82, 98]])
print('成绩表形状：', s.shape)
print(s)
print('每门课平均（axis=0）：', np.round(np.mean(s, axis=0), 2))
print('每个学生最高（axis=1）：', np.max(s, axis=1))
print('每个学生总分（axis=1）：', np.sum(s, axis=1))
print('全表最高分：', int(np.max(s)))
flat = int(np.argmax(s))              # argmax 给的是压平后的位置
print('全表最高分的位置：', flat, '→ 第', flat // 4, '行第', flat % 4, '列')


# ===== [k6-print] np.float64(...) 是从哪冒出来的：三种正确写法对照 =====
import numpy as np

x = np.array([19, 14, 25, 10, 27, 26, 27, 18])
print('直接打印一个 numpy 标量（没问题）：', np.mean(x))
print('掉进坑里：', [np.mean(x), np.max(x), np.min(x)])   # 列表里混了 numpy 标量
print('正确做法 1：先转成 Python 数 ——', [float(np.mean(x)), int(np.max(x)), int(np.min(x))])
print('正确做法 2：要按列表展示数组，用 .tolist() ——', np.array([19, 14, 25]).tolist())
print('数组直接 print 也是干净的：', np.array([19, 14, 25]))

