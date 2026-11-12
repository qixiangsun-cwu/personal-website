# -*- coding: utf-8 -*-
"""第 8 章《Pandas 数据处理与分析》示例代码 · 模块 3：访问、修改与排序

配套页面：https://www.qixiangsun.com.cn/python-bigdata-2026-lesson8
运行环境：Python 3.14+ / pandas 3.0.5（pandas 2.x 亦可运行，个别输出格式略有差异）
说明：本模块：loc / iloc / at / iat · df[ ] 三种用法 · 布尔索引 · 增删改与转置 · sort_index / sort_values。
      页面中每段代码都独立可运行，本文件按页面顺序排列；
      页面上标注“故意报错”的示例未收录（运行会中断），请到页面查看。
用法：python 示例代码_模块3_访问修改与排序.py
"""
import pandas as pd

# ===== [k3-loc] loc：取一个格子、取一行、取一列、取一块 =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89, 80, 92], 'wang': [90, 80, 70, 75]},
                  index=['语文', '数学', '英语', 'Python'])
print('一个格子：', df.loc['数学', 'wang'])
print('一行：')
print(df.loc['数学'])
print('一列（冒号表示所有行）：')
print(df.loc[:, 'wang'])
print('行列子集：')
print(df.loc[['语文', '英语'], ['wang']])


# ===== [k3-iloc] iloc：按位置取，负数从末尾数、切片不含终点 =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89, 80, 92], 'wang': [90, 80, 70, 75]},
                  index=['语文', '数学', '英语', 'Python'])
print('第 2 行第 2 列：', df.iloc[1, 1])
print('前两行前两列：')
print(df.iloc[0:2, 0:2])
print('第 1、3 行，第 2 列：')
print(df.iloc[[0, 2], [1]])
print('最后一行：')
print(df.iloc[-1])


# ===== [k3-at] at 按标签取单值、iat 按位置取单值，结果和 loc 一致 =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89, 80, 92], 'wang': [90, 80, 70, 75]},
                  index=['语文', '数学', '英语', 'Python'])
print('at 按标签：', df.at['英语', 'wang'])
print('iat 按位置：', df.iat[2, 1])
print('loc 也能取：', df.loc['英语', 'wang'])
print('三者取到同一个数：', df.at['英语', 'wang'] == df.iat[2, 1] == df.loc['英语', 'wang'])


# ===== [k3-bracket] df[] 三种用法：切片选行、单列变 Series、多列仍是 DataFrame =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89, 80, 92], 'wang': [90, 80, 70, 75]},
                  index=['语文', '数学', '英语', 'Python'])
print('① 切片选行：')
print(df[0:2])
print('② 选一列（返回 Series）：')
print(df['zhao'])
print('③ 选多列（返回 DataFrame）：')
print(df[['wang', 'zhao']])
print('点号写法等价：', (df.zhao == df['zhao']).all())


# ===== [k3-bool] 布尔索引两步走：先造一排 True/False，再挑出 True 的行 =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89, 80, 92], 'wang': [90, 80, 70, 75]},
                  index=['语文', '数学', '英语', 'Python'])
mask = df['wang'] >= 80
print('掩码：')
print(mask)
print('只留 True 的行：')
print(df[mask])


# ===== [k3-between] between 区间筛选 + 两个条件用 & 连接（每个条件都要括号） =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89, 80, 92], 'wang': [90, 80, 70, 75]},
                  index=['语文', '数学', '英语', 'Python'])
print('zhao 在 85 到 95 之间：')
print(df[df['zhao'].between(85, 95)])
print('两个条件同时满足（两个括号都不能少）：')
print(df[(df['zhao'] >= 85) & (df['wang'] >= 80)])
print('再加一个 loc，只看某几列：')
print(df.loc[(df['zhao'] >= 85) & (df['wang'] >= 80), ['zhao']])


# ===== [k3-modify] 改一个值（loc 定位）、加一列（新列名赋值） =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89, 80, 92], 'wang': [90, 80, 70, 75]},
                  index=['语文', '数学', '英语', 'Python'])
df.loc['语文', 'wang'] = 95
print('改一个值：')
print(df)
df['li'] = [88, 91, 95, 90]
print('加一列：')
print(df)


# ===== [k3-transpose] 转置 df.T：行列身份互换，shape 也跟着反过来 =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89, 80, 92], 'wang': [90, 80, 70, 75]},
                  index=['语文', '数学', '英语', 'Python'])
print('原表形状：', df.shape)
print('转置后：')
print(df.T)
print('转置后的形状：', df.T.shape)


# ===== [k3-drop] drop 删列、删行：默认返回新表，原表不动 =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89, 80, 92], 'wang': [90, 80, 70, 75]},
                  index=['语文', '数学', '英语', 'Python'])
print('删一列：')
print(df.drop(columns=['wang']))
print('删一行：')
print(df.drop(index=['英语']))
print('原表没变：', df.shape)


# ===== [k3-sortindex] sort_index：按行标签升序 / 降序 =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89, 80, 92], 'wang': [90, 80, 70, 75]},
                  index=['语文', '数学', '英语', 'Python'])
print('按行标签升序：')
print(df.sort_index())
print('按行标签降序：')
print(df.sort_index(ascending=False))


# ===== [k3-sortvals] sort_values：单列排序 + 多列排序（不同列不同方向） =====
import pandas as pd

df = pd.DataFrame({'班级': ['一班', '二班', '一班', '二班', '一班'],
                   '成绩': [93, 80, 88, 95, 93]})
print('按成绩升序：')
print(df.sort_values(by='成绩'))
print('班级升序、成绩降序：')
print(df.sort_values(by=['班级', '成绩'], ascending=[True, False]))


# ===== [k3-sort-axis1] axis=1：按列名排、按某一行的值给列排序 =====
import pandas as pd

df = pd.DataFrame({'语文': [93, 89], '数学': [90, 80], '英语': [80, 70]},
                  index=['赵敏', '王芳'])
print('列名按字典序排（axis=1 排的是列）：')
print(df.sort_index(axis=1))
print('按"赵敏"这一行的分数给列排序：')
print(df.sort_values(by='赵敏', axis=1, ascending=False))


# 注：页面上这些示例是“故意报错”的，未收录到本文件：k3-loc-err、k3-pos-err
