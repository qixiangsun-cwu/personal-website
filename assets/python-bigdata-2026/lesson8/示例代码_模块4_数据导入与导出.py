# -*- coding: utf-8 -*-
"""第 8 章《Pandas 数据处理与分析》示例代码 · 模块 4：数据导入与导出

配套页面：https://www.qixiangsun.com.cn/python-bigdata-2026-lesson8
运行环境：Python 3.14+ / pandas 3.0.5（pandas 2.x 亦可运行，个别输出格式略有差异）
说明：本模块：数据分析流程 · read_excel / read_csv / read_table · 导入参数 · to_excel / to_csv。
      页面中每段代码都独立可运行，本文件按页面顺序排列；
      页面上标注“故意报错”的示例未收录（运行会中断），请到页面查看。
用法：python 示例代码_模块4_数据导入与导出.py
"""
import pandas as pd

# ===== [k4-pipeline] 五步在 12 行订单数据上走一遍：导入 → 检查 → 清洗 → 分析 → 结论 =====
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
302012,2021-02-28,137401,围巾,21.6,25,540.0
"""
raw = pd.read_csv(StringIO(csv), parse_dates=['订单日期'])

print('① 数据准备：表', raw.shape)
print('② 先检查：缺失格', int(raw.isnull().sum().sum()), '个')
df = raw.dropna().drop_duplicates()                                # 去缺失、去重
df = df[(df['金额'] != 0) & ~((df['单价'] > 150) & (df['数量'] > 25))]  # 去异常
print('③ 清洗后：', df.shape[0], '行')
print('④ 分析：金额合计', round(df['金额'].sum(), 2), '元')
print('⑤ 结论：最大单笔', df['金额'].max(), '元')


# ===== [k4-excel] 先写两个工作表，再用 sheet_name 分别读回来（例 8-4 的 Excel 部分） =====
import os, tempfile

import pandas as pd

with tempfile.TemporaryDirectory() as td:
    path = os.path.join(td, '订单.xlsx')
    df = pd.DataFrame({'订单编号': [302001, 302002, 302003],
                       '商品名称': ['围巾', '运动服', 'T恤'],
                       '金额': [259.2, 1470.0, 1342.32]})
    with pd.ExcelWriter(path) as writer:
        df.to_excel(writer, sheet_name='订单', index=False)
        df[['商品名称', '金额']].to_excel(writer, sheet_name='汇总', index=False)
    print('工作表“订单”：')
    print(pd.read_excel(path, sheet_name='订单'))
    print('工作表“汇总”：')
    print(pd.read_excel(path, sheet_name='汇总'))


# ===== [k4-excel-params] index_col 把编号变成行索引，usecols 只读需要的列，nrows 只读前几行 =====
import os, tempfile

import pandas as pd

with tempfile.TemporaryDirectory() as td:
    path = os.path.join(td, '成绩.xlsx')
    score = pd.DataFrame({'学号': ['2401', '2402', '2403'],
                          '姓名': ['赵敏', '王芳', '李娜'],
                          '成绩': [93, 89, 80]})
    score.to_excel(path, index=False)
    # 用第 0 列（学号）做行索引，只读 A:C 三列，只读前 2 行
    part = pd.read_excel(path, index_col=0, usecols='A:C', nrows=2)
    print(part)
    print('行索引：', list(part.index))


# ===== [k4-csv] 商品表写成 CSV 再读回来：先 to_csv 再 read_csv，形状和内容都对得上 =====
import os, tempfile
from io import StringIO

import pandas as pd

goods = pd.read_csv(StringIO("""商品名称,进价,产地,库存,销售价
围巾,15,江苏,50,21.6
运动服,130,北京,20,210.0
T恤,38,上海,125,65.8
卫衣,90,广东,62,126.5
休闲鞋,110,广东,210,162.0
运动鞋,152,福建,48,237.0
太阳帽,11,浙江,10,22.0
"""))
with tempfile.TemporaryDirectory() as td:
    path = os.path.join(td, '商品.csv')
    goods.to_csv(path, index=False, encoding='utf-8')
    back = pd.read_csv(path)
    print('写出去再读回来：', back.shape)
    print(back.head(3))


# ===== [k4-read-table] Tab 分隔的 .txt：read_table 默认就按 Tab 切，read_csv 要显式写 sep='\t' =====
from io import StringIO

import pandas as pd

txt = """商品名称\t进价\t库存
围巾\t15\t50
运动服\t130\t20
T恤\t38\t125
"""
t1 = pd.read_table(StringIO(txt))
print('read_table 默认按 Tab 分隔：')
print(t1)
t2 = pd.read_csv(StringIO(txt), sep='\t')
print('read_csv 指定 sep=Tab，结果一样吗？', t1.equals(t2))


# ===== [k4-header-names] 没有表头的 CSV：header=None 先占位，names 再起名，列名就正过来了 =====
from io import StringIO

import pandas as pd

# 为了看清参数，这里用 4 行迷你表；12 行订单数据上写法完全一样
txt = """302001,2021-02-01,围巾,259.2
302002,2021-02-01,运动服,1470.0
302003,2021-02-01,T恤,1342.32
302004,2021-02-05,运动服,4284.0
"""
df = pd.read_csv(StringIO(txt), header=None,
                 names=['订单编号', '订单日期', '商品名称', '金额'])
print(df)
print('列名：', list(df.columns))


# ===== [k4-index-usecols] index_col 指定行索引 + usecols 只读 5 列：读进来就是一张“分析就绪”的表 =====
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
302012,2021-02-28,137401,围巾,21.6,25,540.0
"""
df = pd.read_csv(StringIO(csv), usecols=['订单编号', '订单日期', '商品名称', '数量', '金额'],
                 index_col='订单编号', parse_dates=['订单日期'])
print(df.head(3))
print('行索引：', list(df.index[:3]))
print('列数：', df.shape[1])


# ===== [k4-check-info] 导入后先 head 看长相、再 info 看类型：例 8-5 的检查习惯 =====
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
302012,2021-02-28,137401,围巾,21.6,25,540.0
"""
df = pd.read_csv(StringIO(csv), parse_dates=['订单日期'])
print(df.head(2))
df.info()


# ===== [k4-to-csv] 取 3 列导出 CSV，index=False 保证文件第一行就是表头（例 8-6） =====
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
302012,2021-02-28,137401,围巾,21.6,25,540.0
"""
df = pd.read_csv(StringIO(csv), parse_dates=['订单日期'])
part = df[['订单日期', '商品名称', '数量']].head(4)
with tempfile.TemporaryDirectory() as td:
    path = os.path.join(td, '订单_part.csv')
    part.to_csv(path, index=False, encoding='utf-8')
    print(open(path, encoding='utf-8').read())


# ===== [k4-to-excel] to_excel 指定工作表名、挑列、不加索引，读回来核对形状 =====
import os, tempfile

import pandas as pd

with tempfile.TemporaryDirectory() as td:
    path = os.path.join(td, '销售.xlsx')
    part = pd.DataFrame({'商品名称': ['围巾', '运动服', 'T恤', '卫衣'],
                         '金额': [259.2, 1470.0, 1342.32, 1897.5]})
    part.to_excel(path, sheet_name='销售',
                  columns=['商品名称', '金额'], index=False)
    back = pd.read_excel(path, sheet_name='销售')
    print('读回来：', back.shape)
    print(back)


# ===== [k4-gbk] encoding='gbk' 读回来：同一个文件，指定编码就从报错变正常 =====
import os, tempfile

import pandas as pd

with tempfile.TemporaryDirectory() as td:
    path = os.path.join(td, '商品_gbk.csv')
    goods = pd.DataFrame({'商品名称': ['围巾', '运动服', 'T恤'],
                          '进价': [15, 130, 38]})
    goods.to_csv(path, index=False, encoding='gbk')
    back = pd.read_csv(path, encoding='gbk')
    print(back)


# 注：页面上这些示例是“故意报错”的，未收录到本文件：k4-path-error、k4-encoding-error
