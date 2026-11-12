# -*- coding: utf-8 -*-
"""第 8 章《Pandas 数据处理与分析》示例代码 · 模块 8：合并连接与综合案例

配套页面：https://www.qixiangsun.com.cn/python-bigdata-2026-lesson8
运行环境：Python 3.14+ / pandas 3.0.5（pandas 2.x 亦可运行，个别输出格式略有差异）
说明：本模块：concat · merge 四种 how · 连接校验 · 订单全流程 · AI 数据管线。
      页面中每段代码都独立可运行，本文件按页面顺序排列；
      页面上标注“故意报错”的示例未收录（运行会中断），请到页面查看。
用法：python 示例代码_模块8_合并连接与综合.py
"""
import pandas as pd

# ===== [k8-concat-axis0] 例 8-39：竖向摞行 axis=0，ignore_index 决定索引重不重排 =====
import pandas as pd
from io import StringIO

csv = """订单编号,订单日期,用户ID,商品名称,单价,数量,金额
302001,2021-02-01,137401,围巾,21.6,12,259.2
302002,2021-02-01,177562,运动服,210.0,7,1470.0
302003,2021-02-01,133422,T恤,55.93,24,1342.32
302004,2021-02-05,138462,运动服,178.5,24,4284.0
302005,2021-02-05,158949,T恤,65.8,14,921.2
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302008,2021-02-12,186059,T恤,65.8,5,329.0
302009,2021-02-15,199357,,,,540.0
302010,2021-02-20,149590,休闲鞋,162.0,30,4860.0
302011,2021-02-25,149590,运动服,178.5,0,0.0
302012,2021-02-28,137401,围巾,21.6,25,540.0"""
df = pd.read_csv(StringIO(csv), parse_dates=['订单日期'])

df_1 = df.iloc[:6].reset_index(drop=True)    # 模拟第一份文件：6 行，索引从 0 开始
df_2 = df.iloc[6:].reset_index(drop=True)    # 模拟第二份文件：6 行，索引也从 0 开始
print('df_1：', df_1.shape, '  df_2：', df_2.shape)
both = pd.concat([df_1, df_2])
print('直接 concat：', both.shape, ' 索引：', both.index.tolist())
both2 = pd.concat([df_1, df_2], ignore_index=True)
print('ignore_index=True：', both2.shape, ' 索引：', both2.index.tolist())


# ===== [k8-concat-axis1] axis=1 横向拼列：4 行 2 列 + 4 行 2 列 → 4 行 4 列 =====
import pandas as pd
from io import StringIO

csv = """订单编号,订单日期,用户ID,商品名称,单价,数量,金额
302001,2021-02-01,137401,围巾,21.6,12,259.2
302002,2021-02-01,177562,运动服,210.0,7,1470.0
302003,2021-02-01,133422,T恤,55.93,24,1342.32
302004,2021-02-05,138462,运动服,178.5,24,4284.0
302005,2021-02-05,158949,T恤,65.8,14,921.2
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302008,2021-02-12,186059,T恤,65.8,5,329.0
302009,2021-02-15,199357,,,,540.0
302010,2021-02-20,149590,休闲鞋,162.0,30,4860.0
302011,2021-02-25,149590,运动服,178.5,0,0.0
302012,2021-02-28,137401,围巾,21.6,25,540.0"""
df = pd.read_csv(StringIO(csv), parse_dates=['订单日期'])

part_a = df[['订单编号', '金额']].head(4).reset_index(drop=True)
part_b = df[['商品名称', '数量']].head(4).reset_index(drop=True)
print('part_a：', part_a.shape, '  part_b：', part_b.shape)
wide = pd.concat([part_a, part_b], axis=1)
print('axis=1 之后：', wide.shape)
print(wide)
print('axis=0 竖向摞（同样的两张表）：', pd.concat([part_a, part_a]).shape)


# ===== [k8-merge-inner] 例 8-40：订单汇总 ⟕ 商品表，一次看到订购信息和商品详情 =====
import pandas as pd
from io import StringIO

csv = """订单编号,订单日期,用户ID,商品名称,单价,数量,金额
302001,2021-02-01,137401,围巾,21.6,12,259.2
302002,2021-02-01,177562,运动服,210.0,7,1470.0
302003,2021-02-01,133422,T恤,55.93,24,1342.32
302004,2021-02-05,138462,运动服,178.5,24,4284.0
302005,2021-02-05,158949,T恤,65.8,14,921.2
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302008,2021-02-12,186059,T恤,65.8,5,329.0
302009,2021-02-15,199357,,,,540.0
302010,2021-02-20,149590,休闲鞋,162.0,30,4860.0
302011,2021-02-25,149590,运动服,178.5,0,0.0
302012,2021-02-28,137401,围巾,21.6,25,540.0"""
df = pd.read_csv(StringIO(csv), parse_dates=['订单日期'])
goods = pd.read_csv(StringIO("""商品名称,进价,产地,库存,销售价
围巾,15,江苏,50,21.6
运动服,130,北京,20,210.0
T恤,38,上海,125,65.8
卫衣,90,广东,62,126.5
休闲鞋,110,广东,210,162.0
运动鞋,152,福建,48,237.0
太阳帽,11,浙江,10,22.0
"""))
df_sum = df.groupby('商品名称', as_index=False).agg({'数量': 'sum'})
df_sum.columns = ['商品名称', '订购总数量']
df_sum['订购总数量'] = df_sum['订购总数量'].astype(int)
print('订单汇总 df_sum：')
print(df_sum)
print('连接商品表（默认 how=inner）：')
print(pd.merge(df_sum, goods, on='商品名称'))


# ===== [k8-merge-how] 四种 how 的行数对比：inner 5 / left 5 / right 7 / outer 7 =====
import pandas as pd
from io import StringIO

csv = """订单编号,订单日期,用户ID,商品名称,单价,数量,金额
302001,2021-02-01,137401,围巾,21.6,12,259.2
302002,2021-02-01,177562,运动服,210.0,7,1470.0
302003,2021-02-01,133422,T恤,55.93,24,1342.32
302004,2021-02-05,138462,运动服,178.5,24,4284.0
302005,2021-02-05,158949,T恤,65.8,14,921.2
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302008,2021-02-12,186059,T恤,65.8,5,329.0
302009,2021-02-15,199357,,,,540.0
302010,2021-02-20,149590,休闲鞋,162.0,30,4860.0
302011,2021-02-25,149590,运动服,178.5,0,0.0
302012,2021-02-28,137401,围巾,21.6,25,540.0"""
df = pd.read_csv(StringIO(csv), parse_dates=['订单日期'])
goods = pd.read_csv(StringIO("""商品名称,进价,产地,库存,销售价
围巾,15,江苏,50,21.6
运动服,130,北京,20,210.0
T恤,38,上海,125,65.8
卫衣,90,广东,62,126.5
休闲鞋,110,广东,210,162.0
运动鞋,152,福建,48,237.0
太阳帽,11,浙江,10,22.0
"""))
df_sum = df.groupby('商品名称', as_index=False).agg({'数量': 'sum'})
df_sum.columns = ['商品名称', '订购总数量']

for how in ['inner', 'left', 'right', 'outer']:
    m = pd.merge(df_sum, goods, on='商品名称', how=how)
    print(f'how={how:<6} 行数 {m.shape[0]}  列数 {m.shape[1]}')
print('outer 的结果（右表多出的两种商品，订购总数量是 NaN）：')
print(pd.merge(df_sum, goods, on='商品名称', how='outer'))


# ===== [k8-merge-suffix] suffixes：两边同名字段时，用后缀区分（默认是 _x / _y） =====
import pandas as pd

this_month = pd.DataFrame({'商品名称': ['围巾', 'T恤'],
                           '库存': [50, 125]})
last_month = pd.DataFrame({'商品名称': ['围巾', 'T恤'],
                           '库存': [48, 120]})
print('默认后缀 _x / _y：')
print(pd.merge(this_month, last_month, on='商品名称'))
print('自定义后缀：')
print(pd.merge(this_month, last_month, on='商品名称',
               suffixes=('_本月', '_上月')))


# ===== [k8-merge-check] indicator=True：给每一行标上来源（both / left_only / right_only） =====
import pandas as pd
from io import StringIO

csv = """订单编号,订单日期,用户ID,商品名称,单价,数量,金额
302001,2021-02-01,137401,围巾,21.6,12,259.2
302002,2021-02-01,177562,运动服,210.0,7,1470.0
302003,2021-02-01,133422,T恤,55.93,24,1342.32
302004,2021-02-05,138462,运动服,178.5,24,4284.0
302005,2021-02-05,158949,T恤,65.8,14,921.2
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302008,2021-02-12,186059,T恤,65.8,5,329.0
302009,2021-02-15,199357,,,,540.0
302010,2021-02-20,149590,休闲鞋,162.0,30,4860.0
302011,2021-02-25,149590,运动服,178.5,0,0.0
302012,2021-02-28,137401,围巾,21.6,25,540.0"""
df = pd.read_csv(StringIO(csv), parse_dates=['订单日期'])
goods = pd.read_csv(StringIO("""商品名称,进价,产地,库存,销售价
围巾,15,江苏,50,21.6
运动服,130,北京,20,210.0
T恤,38,上海,125,65.8
卫衣,90,广东,62,126.5
休闲鞋,110,广东,210,162.0
运动鞋,152,福建,48,237.0
太阳帽,11,浙江,10,22.0
"""))
df_sum = df.groupby('商品名称', as_index=False).agg({'数量': 'sum'})
df_sum.columns = ['商品名称', '订购总数量']
print('连接前：左表', df_sum.shape, ' 右表', goods.shape)
m = pd.merge(df_sum, goods, on='商品名称', how='outer', indicator=True)
print(m[['商品名称', '订购总数量', '库存', '_merge']])
print('_merge 计数：')
print(m['_merge'].value_counts())


# ===== [k8-merge-dup] 重复键让行数膨胀：3 行 + 3 行 → 4 行；validate 直接拦下 =====
import pandas as pd

left = pd.DataFrame({'商品名称': ['围巾', 'T恤', '卫衣'],
                     '数量': [12, 24, 15]})
right = pd.DataFrame({'商品名称': ['围巾', '围巾', 'T恤'],
                      '产地': ['江苏', '浙江', '上海']})
m = pd.merge(left, right, on='商品名称', how='left')
print('左表 3 行、右表 3 行，连接后：', m.shape)
print('多出来的行，是右表里“围巾”有两条造成的：')
print(m)
try:
    pd.merge(left, right, on='商品名称', validate='one_to_one')
except ValueError as e:
    print('validate=one_to_one 直接把问题拦下：')
    print(type(e).__name__, ':', e)


# ===== [k8-case-import] 综合案例（1/3）：导入 12 行 → 四步清洗到 8 行 → 导出 csv 并回读 =====
import os, tempfile
import pandas as pd

csv = """订单编号,订单日期,用户ID,商品名称,单价,数量,金额
302001,2021-02-01,137401,围巾,21.6,12,259.2
302002,2021-02-01,177562,运动服,210.0,7,1470.0
302003,2021-02-01,133422,T恤,55.93,24,1342.32
302004,2021-02-05,138462,运动服,178.5,24,4284.0
302005,2021-02-05,158949,T恤,65.8,14,921.2
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302008,2021-02-12,186059,T恤,65.8,5,329.0
302009,2021-02-15,199357,,,,540.0
302010,2021-02-20,149590,休闲鞋,162.0,30,4860.0
302011,2021-02-25,149590,运动服,178.5,0,0.0
302012,2021-02-28,137401,围巾,21.6,25,540.0"""
with tempfile.TemporaryDirectory() as td:
    src = os.path.join(td, '订单_原始.csv')
    with open(src, 'w', encoding='utf-8', newline='') as f:
        f.write(csv)                                   # 先落盘，模拟“从文件导入”
    df = pd.read_csv(src, parse_dates=['订单日期'])
    print('① 导入：', df.shape)
    df = df.dropna(subset=['商品名称', '单价', '数量'])
    print('② 去缺失：', df.shape)
    df = df.drop_duplicates()
    print('③ 去重复：', df.shape)
    df = df[~((df['单价'] > 150) & (df['数量'] > 25))]
    print('④ 去异常：', df.shape)
    df = df[df['数量'] > 0]
    print('⑤ 去无意义：', df.shape)
    dst = os.path.join(td, '订单_清洗.csv')
    df.to_csv(dst, index=False)
    back = pd.read_csv(dst, parse_dates=['订单日期'])
    print('⑥ 导出并回读：', back.shape, list(back.columns))


# ===== [k8-case-analyze] 综合案例（2/3）：清洗后查询大单，再按商品分组聚合 =====
import pandas as pd
from io import StringIO

csv = """订单编号,订单日期,用户ID,商品名称,单价,数量,金额
302001,2021-02-01,137401,围巾,21.6,12,259.2
302002,2021-02-01,177562,运动服,210.0,7,1470.0
302003,2021-02-01,133422,T恤,55.93,24,1342.32
302004,2021-02-05,138462,运动服,178.5,24,4284.0
302005,2021-02-05,158949,T恤,65.8,14,921.2
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302008,2021-02-12,186059,T恤,65.8,5,329.0
302009,2021-02-15,199357,,,,540.0
302010,2021-02-20,149590,休闲鞋,162.0,30,4860.0
302011,2021-02-25,149590,运动服,178.5,0,0.0
302012,2021-02-28,137401,围巾,21.6,25,540.0"""
df = pd.read_csv(StringIO(csv), parse_dates=['订单日期'])

clean = df.dropna(subset=['商品名称', '单价', '数量']).drop_duplicates()
clean = clean[~((clean['单价'] > 150) & (clean['数量'] > 25))]
clean = clean[clean['数量'] > 0]
print('清洗后：', clean.shape)
print('查询：金额 >= 1500 的订单')
print(clean[clean['金额'] >= 1500][['订单编号', '商品名称', '金额']])
g = clean.groupby('商品名称', as_index=False).agg(
    订购总数量=('数量', 'sum'), 订购总金额=('金额', 'sum'))
print('分组：每种商品的订购总数量与总金额')
print(g.sort_values('订购总金额', ascending=False))


# ===== [k8-case-pivot] 综合案例（3/3）：pivot_table 周报 → to_excel 导出 → read_excel 回读核对 =====
import os, tempfile
import pandas as pd
from io import StringIO

csv = """订单编号,订单日期,用户ID,商品名称,单价,数量,金额
302001,2021-02-01,137401,围巾,21.6,12,259.2
302002,2021-02-01,177562,运动服,210.0,7,1470.0
302003,2021-02-01,133422,T恤,55.93,24,1342.32
302004,2021-02-05,138462,运动服,178.5,24,4284.0
302005,2021-02-05,158949,T恤,65.8,14,921.2
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302006,2021-02-08,120957,卫衣,126.5,15,1897.5
302008,2021-02-12,186059,T恤,65.8,5,329.0
302009,2021-02-15,199357,,,,540.0
302010,2021-02-20,149590,休闲鞋,162.0,30,4860.0
302011,2021-02-25,149590,运动服,178.5,0,0.0
302012,2021-02-28,137401,围巾,21.6,25,540.0"""
df = pd.read_csv(StringIO(csv), parse_dates=['订单日期'])

clean = df.dropna(subset=['商品名称', '单价', '数量']).drop_duplicates()
clean = clean[~((clean['单价'] > 150) & (clean['数量'] > 25))]
clean = clean[clean['数量'] > 0]
clean['周次'] = clean['订单日期'].dt.isocalendar().week.astype(int)
pv = clean.pivot_table(values='金额', index='周次', columns='商品名称',
                       aggfunc='sum', fill_value=0,
                       margins=True, margins_name='合计')
print(pv)
with tempfile.TemporaryDirectory() as td:
    out = os.path.join(td, '订单周报.xlsx')
    pv.to_excel(out, sheet_name='周报')
    back = pd.read_excel(out, sheet_name='周报', index_col=0)
    print('导出 xlsx 再读回：', back.shape)
    print('合计行：', back.loc['合计'].to_dict())


# ===== [k8-ai-numpy] DataFrame → ndarray → 按列标准化：特征矩阵进模型前的标准动作 =====
import pandas as pd

# 一行一个样本、一列一个特征 —— 这就是训练数据的标准形状
df = pd.DataFrame({'单价': [21.6, 210.0, 55.93, 126.5],
                   '数量': [12.0, 7.0, 24.0, 15.0],
                   '会员等级': ['金卡', '普通', '金卡', '普通']})
X = df[['单价', '数量']].to_numpy(dtype='float64')   # 数值特征 → ndarray
print('X.shape =', X.shape, '  X.dtype =', X.dtype)
Z = (X - X.mean(axis=0)) / X.std(axis=0)              # 按列标准化
print('标准化后每列均值：', Z.mean(axis=0).round(6).tolist())
print('标准化后每列标准差：', Z.std(axis=0).round(6).tolist())
print('第一个样本：', Z[0].round(3).tolist())
print('类别列模型读不懂，用 get_dummies 转 0/1：')
print(pd.get_dummies(df[['会员等级']], dtype=int))


# ===== [k8-ai-dummies] 类别列 → 0/1 特征矩阵：one-hot 编码就是 get_dummies =====
import pandas as pd

df = pd.DataFrame({'商品名称': ['围巾', '运动服', 'T恤', '卫衣', '围巾'],
                   '数量': [12, 7, 24, 15, 25]})
print('原始表（文本列模型读不懂）：')
print(df)
dummies = pd.get_dummies(df, columns=['商品名称'], dtype=int)
print('one-hot 之后：一件商品一列，是它填 1、不是填 0')
print(dummies)
print('特征矩阵形状：', dummies.to_numpy().shape)
print('列名（特征名）：', dummies.columns.tolist())

