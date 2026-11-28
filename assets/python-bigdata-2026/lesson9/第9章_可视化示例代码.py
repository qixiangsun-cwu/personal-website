# -*- coding: utf-8 -*-
"""第 9 章 数据可视化 · 教材示例代码合集（chpt9-1 ~ chpt9-18）

用法：
1. 把本文件与「产量.csv」「订单_new.xlsx」放在同一个文件夹；
2. 先安装依赖：pip install pandas matplotlib openpyxl
3. 直接运行：python 第9章_可视化示例代码.py
   每段会依次画出图（窗口关闭后继续下一段）；也可以把某一段单独复制出来运行。
4. 中文字体：Windows 用 SimHei；macOS 改成 'Arial Unicode MS' 或 'PingFang SC'。

数据说明：
- 产量.csv（GBK 编码，index_col=0）：4 个季度 × A/B/C 三组产量
- 订单_new.xlsx：305 行订单（2021 年 2 月），列含 订单日期/商品名称/单价/数量/金额/周次 等
"""

import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False


# ============================================================
# 9-1 折线图：一组数据的走势（plot）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']

df = pd.read_csv(r'产量.csv',index_col=0, encoding='gbk')
x = df.index                # 数据框的索引   
y = df['B组'].values       # B组数据

plt.figure()                 # 创建Figure对象，同时在画布上生成一个轴域
plt.plot(x, y)
##plt.title('B组各季度产量')
##plt.ylabel('产量（万吨）')
plt.show()

# ============================================================
# 9-2 折线图：多组数据对比 + 图例（plot + legend）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']

df = pd.read_csv(r'产量.csv',index_col=0, encoding='gbk')
x = df.index                         
y = df.values                                              # 多个系列数据
legend = df.columns                                       # 列标签作为图例

plt.figure()   
plt.plot(x, y)
plt.legend(legend)                                        # 设置图例
plt.title('三个组各季度产量比较图')
plt.ylabel('产量（万吨)')
plt.show()

# ============================================================
# 9-3 柱形图与条形图（bar / barh，双子图）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']

df = pd.read_csv(r'产量.csv',index_col=0, encoding='gbk')
data = df['B组']                     # B组产量

fig = plt.figure()
fig.add_subplot(1,2,1)               # 添加一个子图，绘制柱形图 
x = data.index
height = data.values
plt.bar(x, height)
plt.title('B组各季度的产量比较图')
plt.ylabel('产量（万吨）')
plt.text(0,height[0],height[0])     # 添加数据标签

fig.add_subplot(1,2,2)               # 添加第2个子图，绘制条形图
y = data.index
width = data.values
plt.barh(y, width)
plt.title('B组各季度的产量比较图')
plt.xlabel('产量（万吨）')
plt.show()

# ============================================================
# 9-4 饼图：各组总产量占比（pie + explode）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['SimHei']

df = pd.read_csv(r'产量.csv',index_col=0, encoding='gbk')
data = df.sum(axis = 0)            # 各部门总产量

plt.figure()
labels = data.index
explode = (0, 0, 0.075)            # 第3块距离圆心0.075
plt.pie(data, explode=explode, labels=labels, autopct='%0.0f%%')  
plt.title('各组总产量比例图', fontdict={'fontsize':16, 'color':'red'})
plt.show()

plt.show()

# ============================================================
# 9-5 散点图：学生成绩分布（scatter）
# ============================================================
import numpy as np
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']

data = [70, 80, 85, 68, 84, 85, 76, 92, 87, 72,
         52, 79, 73, 84, 80, 79, 67, 90,	78, 77]  
data = np.array(data)        # 学生成绩

plt.figure()
plt.scatter(range(1, data.size + 1), data)
plt.title('学生成绩分布图')
plt.xlabel('学生')
plt.grid()                                        # 添加网格线
plt.xticks(range(1, data.size + 1))            # 修改x轴刻度
min_y = np.min(data)
pos_x = np.argmin(data)
plt.text(pos_x, min_y, min_y, color='red')    # 添加数据标签
plt.show()

# ============================================================
# 9-6 直方图：各分数段人数（hist）
# ============================================================
import numpy as np
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']

data = [70, 80, 85, 68, 84, 85, 76, 92, 87, 72,
         52, 79, 73, 84, 80, 79,	67, 90,	78, 77]  
data = np.array(data)

plt.figure()
bins = [50, 60, 70, 80, 90, 100]                                  # 组距
nums, bins_, _ = plt.hist(data, bins=bins, facecolor='g', alpha=0.65)
plt.title('各分数段的人数统计图')
plt.ylabel('人数')
for i,n in enumerate(nums):                               # 添加人数标签
    plt.text(bins[i], n, int(n))
plt.savefig(r'成绩_直方图.png')              # 保存图表文件
plt.show()

plt.show()

# ============================================================
# 9-7 Pandas 绘图：单列折线（Series.plot）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']

df = pd.read_csv(r'产量.csv',index_col=0, encoding='gbk')
df['B组'].plot()
plt.title('B组各季度产量')
plt.ylabel('产量（万吨）')
plt.show()

# ============================================================
# 9-8 Pandas 绘图：多列柱状（DataFrame.plot.bar）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']

df = pd.read_csv(r'产量.csv',index_col=0, encoding='gbk')
df.plot.bar()
plt.title('三个组各季度产量比较图')
plt.ylabel('产量（万吨）')
plt.xticks(rotation=20)                              # 设置刻度标签倾斜角度                
plt.show()

# ============================================================
# 9-9 Pandas 绘图：单列箱线（Series.plot.box）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']

df = pd.read_csv(r'产量.csv',index_col=0, encoding='gbk')
df['B组'].plot.box()
plt.show()

plt.show()

# ============================================================
# 9-10 条形图：商品订购总数量（plot(kind='barh')）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']

df = pd.read_excel(r'订单_new.xlsx')
df2 = df.groupby('商品名称').agg({'数量': 'sum'})
df2 = df2.sort_values(by='数量')
df2['数量'].plot(kind='barh')              
plt.title('商品订购总数量比较图')
plt.show()

# ============================================================
# 9-11 多系列柱状图：周次 × 商品（pivot_table + plot.bar）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['SimHei']

df = pd.read_excel(r'订单_new.xlsx')
df2 = df.pivot_table(values='数量', index='周次', 
columns='商品名称',aggfunc='sum')
df2.plot(kind='bar')
plt.title('每周各种商品的订购总数量比较图')
plt.legend(ncol=2)                                          # 图例显示为2列
plt.show()

plt.show()

# ============================================================
# 9-12 直方图：围巾每日订购数量（plot.hist）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']

df = pd.read_excel(r'订单_new.xlsx')
df2 = df[df.商品名称 == '围巾'].groupby('订单日期').agg({'数量':'sum'})
bins = [0, 20, 40, 60, 80, 100]
df2['数量'].plot.hist(bins = bins)
plt.title('围巾每日订购数量的直方图')
plt.ylabel('频数')            
plt.show()

# ============================================================
# 9-13 饼图：各商品订购总数量占比（plot.pie）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['SimHei']

df = pd.read_excel(r'订单_new.xlsx')
df2 = df.groupby('商品名称').agg({'数量':'sum'})
df2 = df2.sort_values(by='数量', ascending=False)
df2['数量'].plot.pie(autopct='%0.0f%%')
plt.title('各商品的订购总数量比例图')
plt.ylabel('')                                                # 清除y轴标题
plt.show()

# ============================================================
# 9-14 折线图 + 极值标注：每日订购金额（groupby + plot + text）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']

def get_pos(df):
    '''功能：获取数据集中最小值和最大值的位置 '''
    data = df.values
    min_y = data.min().round(2)                           # 最小值
    max_y = data.max().round(2)                           # 最大值
    min_idx = data.argmin()       
    max_idx = data.argmax()       
    min_x = df.index[min_idx]                             # 最小值的行索引  
    max_x = df.index[max_idx]                             # 最大值的行索引
    pos = [(min_x, min_y), (max_x, max_y)]  
    return pos

df = pd.read_excel(r'订单_new.xlsx')
df2 = df.groupby('订单日期').agg ({'金额':'sum'})
df2['金额'].plot()                                              
plt.title('2月份每日订购金额变化图')
pos_min, pos_max = get_pos(df2)                               # 调用函数
plt.text(pos_min[0], pos_min[1], pos_min[1], color='m')     # 标注最小值
plt.text(pos_max[0], pos_max[1], pos_max[1], color='r')     # 标注最大值
plt.show()

plt.show()

# ============================================================
# 9-15 散点图：运动服每日订购数量（plot.scatter）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_excel(r'订单_new.xlsx')
df2 = df.pivot_table(values='数量', index='订单日期', columns='商品名称', 
                       aggfunc='sum', fill_value=0)
df2 = df2.reset_index()
df2.plot.scatter(x='订单日期', y='运动服')
plt.xticks(rotation=30, fontsize=8)
plt.title('运动服的每日订购数量变化图', fontproperties='SimHei')
plt.ylabel('')                                                # 清除y轴标题                          
plt.xlabel('')                                                # 清除x轴标题
plt.grid()
plt.show()

# ============================================================
# 9-16 散点图：T恤与运动服数量的关系（plot.scatter）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']

df = pd.read_excel(r'订单_new.xlsx')
df2 = df.pivot_table(values='数量', index='订单日期', columns='商品名称', 
                     aggfunc='sum', fill_value=0)
df2.plot.scatter(x='T恤', y='运动服')
plt.title('T恤和运动服的每日订购数量关系图')
plt.show()

# ============================================================
# 9-17 气泡图：每日金额与数量（scatter 的 s 参数）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']

df = pd.read_excel(r'订单_new.xlsx')
df2 = df.groupby('订单日期').agg ({'金额':'sum','数量':sum}).reset_index()
df2.plot.scatter(x='订单日期', y='金额', s='数量', c='g',alpha=0.5)
plt.title('每日订购金额和订购数量变化图')
plt.show()

# ============================================================
# 9-18 箱线图：各商品订单金额分布（boxplot）
# ============================================================
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']

df = pd.read_excel(r'订单_new.xlsx')
df.boxplot(column='金额', by='商品名称')       # 按商品名称分组，按金额统计plt.title('商品订单金额分布图')
plt.savefig(r'订单_箱线图.png')    # 保存图表文件
plt.show()
