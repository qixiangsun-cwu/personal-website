# -*- coding: utf-8 -*-
"""第 8 章《Pandas 数据处理与分析》示例代码 · 模块 1：Pandas 与 Series

配套页面：https://www.qixiangsun.com.cn/python-bigdata-2026-lesson8
运行环境：Python 3.14+ / pandas 3.0.5（pandas 2.x 亦可运行，个别输出格式略有差异）
说明：本模块：Pandas 入门 · Series 结构 · 创建 / 访问 / 统计 · date_range 时间索引。
      页面中每段代码都独立可运行，本文件按页面顺序排列；
      页面上标注“故意报错”的示例未收录（运行会中断），请到页面查看。
用法：python 示例代码_模块1_Series.py
"""
import pandas as pd

# ===== [k1-why] 列表要对齐两列，Series 把名字和值绑在一起 =====
import pandas as pd

scores = [93, 89, 80, 92]
subjects = ['语文', '数学', '英语', 'Python']
print('列表里"数学"的成绩：', scores[subjects.index('数学')])
print('顺序一旦写岔就取错：', scores[0])

s = pd.Series(scores, index=subjects)
print('Series 里"数学"的成绩：', s['数学'])
print(s)


# ===== [k1-parts] index 与 values：默认索引 0..n-1，values 就是 ndarray =====
import pandas as pd

s = pd.Series([93, 89, 80, 92])
print('默认索引：', s.index)
print('值：', s.values)
print('类型：', type(s).__name__)

t = pd.Series([93, 89, 80, 92], index=['语文', '数学', '英语', 'Python'])
print('自定义索引：', t.index)
print('values 的类型：', type(t.values).__name__)
print('index 的类型：', type(t.index).__name__)


# ===== [k1-create] 五种创建方式：列表 / 元组 / range / 字典 / 一维数组 =====
import pandas as pd
import numpy as np

print('列表：', pd.Series([90, 75, 80]).tolist())
print('元组：', pd.Series((90, 75, 80)).tolist())
print('range：', pd.Series(range(1, 6)).tolist())
print('字典：', pd.Series({'语文': 90, '数学': 75, '英语': 80}).to_dict())
print('数组：', pd.Series(np.array([90, 75, 80])).tolist())

s = pd.Series([90, 75, 80], index=['语文', '数学', '英语'], dtype='float64')
print('加 index 和 dtype 之后：')
print(s)


# ===== [k1-access] 按标签取、按位置取、切片、多个标签一起取 =====
import pandas as pd

s = pd.Series([93, 89, 80, 92], index=['语文', '数学', '英语', 'Python'])
print('按标签取：', s['数学'])
print('按位置取：', s.iloc[1])
try:
    print(s[1])
except KeyError:
    print('s[1] 报错：KeyError（pandas 3 不再把整数下标当位置）')
print('标签切片（含终点）：')
print(s['数学':'英语'])
print('多个标签一起取：')
print(s[['语文', '英语']])


# ===== [k1-bool] 布尔筛选：先比较出一排 True/False，再当筛子用 =====
import pandas as pd

s = pd.Series([93, 89, 80, 92], index=['语文', '数学', '英语', 'Python'])
print('比较得到一排 True/False：')
print(s >= 85)
print('把这些 True 当筛子：')
print(s[s >= 85])
print('不及格的科目：', list(s[s < 85].index))


# ===== [k1-modify] 改一个值、加一门课、drop 掉再确认 =====
import pandas as pd

s = pd.Series([93, 89, 80, 92], index=['语文', '数学', '英语', 'Python'])
s['英语'] = 85
print('改一个值：')
print(s)
s['Java'] = 78
print('加一门课：')
print(s)
print('drop 之后还在不在：', 'Java' in s.drop('Java').index, '|', 'Java' in s.index)


# ===== [k1-stat] 与标量运算逐元素生效；sum / mean / max / min / count =====
import pandas as pd

s = pd.Series([93, 89, 80, 92], index=['语文', '数学', '英语', 'Python'])
print('每门加 5 分：')
print(s + 5)
print('总分：', s.sum())
print('平均分：', s.mean(), '→ 保留一位', round(s.mean(), 1))
print('最高分：', s.max(), '| 最低分：', s.min(), '| 科目数：', s.count())


# ===== [k1-align] 两个 Series 相加：按标签配对，缺标签补 NaN =====
import pandas as pd

a = pd.Series([90, 75, 80], index=['语文', '数学', '英语'])
b = pd.Series([5, 10, 0], index=['英语', '语文', '数学'])
print('a + b（标签顺序不同也能对上）：')
print(a + b)

c = pd.Series([1, 2], index=['语文', '数学'])
print('a + c（英语在 c 里没有，结果补 NaN）：')
print(a + c)


# ===== [k1-daterange] date_range 三种频率：D 按日、W 按周、ME 按月末 =====
import pandas as pd

d1 = pd.date_range(start='2022-01-01', periods=5, freq='D')
d2 = pd.date_range(start='2022-01-01', periods=5, freq='W')
d3 = pd.date_range(start='2022-01-01', periods=5, freq='ME')

print('D  按日：', [d.strftime('%m-%d') for d in d1])
print('W  按周（周日）：', [d.strftime('%m-%d') for d in d2])
print('ME 按月末：', [d.strftime('%m-%d') for d in d3])


# ===== [k1-sales] 例 8-1 简化版：12 个月销售额 + 按月份查询 =====
import pandas as pd

date = pd.date_range(start='20220101', freq='ME', periods=12)
money = [1580, 2000, 1250, 1760, 2180, 2100, 1890, 2016, 2210, 1908, 1500, 1786]
sales = pd.Series(money, index=date)
print(sales.head())
print('2022 年 3 月：', sales['2022-03'].iloc[0])
print('一季度（1—3 月）：')
print(sales['2022-01':'2022-03'])


# ===== [k1-sales-stat] 时间索引 Series 的筛选与统计：超过 2000 的月份 / 全年总额 / 月均 =====
import pandas as pd

date = pd.date_range(start='20220101', freq='ME', periods=12)
money = [1580, 2000, 1250, 1760, 2180, 2100, 1890, 2016, 2210, 1908, 1500, 1786]
sales = pd.Series(money, index=date)
print('全年销售额：', sales.sum())
print('月均销售额：', round(sales.mean(), 1))
print('超过 2000 的月份：')
print(sales[sales >= 2000])


# 注：页面上这些示例是“故意报错”的，未收录到本文件：k1-create-err、k1-freqM
