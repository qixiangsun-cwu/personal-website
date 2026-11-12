# -*- coding: utf-8 -*-
"""第 8 章《Pandas 数据处理与分析》示例代码 · 模块 2：DataFrame 创建与查看

配套页面：https://www.qixiangsun.com.cn/python-bigdata-2026-lesson8
运行环境：Python 3.14+ / pandas 3.0.5（pandas 2.x 亦可运行，个别输出格式略有差异）
说明：本模块：嵌套列表 / 字典建表 · index 与 columns · shape / dtypes · head / info / describe。
      页面中每段代码都独立可运行，本文件按页面顺序排列；
      页面上标注“故意报错”的示例未收录（运行会中断），请到页面查看。
用法：python 示例代码_模块2_DataFrame创建与查看.py
"""
import pandas as pd

# ===== [k2-anatomy] index / columns / values 三部分，以及“一列就是一个 Series” =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89, 80, 92], 'wang': [90, 80, 70, 75]},
                  index=['语文', '数学', '英语', 'Python'])
print('行索引：', list(df.index))
print('列索引：', list(df.columns))
print('值矩阵：')
print(df.values)
print('shape：', df.shape)
print('取一列（Series）：')
print(df['zhao'])


# ===== [k2-list] 嵌套列表建表：行列名默认都是 0、1、2… =====
import pandas as pd

data = [[93, 90], [89, 80], [80, 70], [92, 75]]
df = pd.DataFrame(data)
print('默认行列名：')
print(df)
print('行名：', list(df.index))
print('列名：', list(df.columns))


# ===== [k2-list-idx] 嵌套列表 + index / columns：给表配上行列名 =====
import pandas as pd

data = [[93, 90], [89, 80], [80, 70], [92, 75]]
df = pd.DataFrame(data,
                  index=['语文', '数学', '英语', 'Python'],
                  columns=['zhao', 'wang'])
print(df)
print('shape：', df.shape)


# ===== [k2-dict] 字典建表：键 → 列名，值列表 → 每一列的数据 =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89, 80, 92],
                   'wang': [90, 80, 70, 75]},
                  index=['语文', '数学', '英语', 'Python'])
print(df)
print('列名就是字典的键：', list(df.columns))
print('行名由 index 指定：', list(df.index))


# ===== [k2-dict-nan] 两列长度不同：按标签对齐，缺的格子补 NaN =====
import pandas as pd

zhao = pd.Series([93, 89, 80], index=['语文', '数学', '英语'])
wang = pd.Series([90, 80, 70, 75], index=['语文', '数学', '英语', 'Python'])
df = pd.DataFrame({'zhao': zhao, 'wang': wang})
print(df)
print('行索引是两边标签的并集：', list(df.index))


# ===== [k2-setindex] 直接赋值：改行索引、改列索引，改完再看一眼 =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89, 80, 92], 'wang': [90, 80, 70, 75]})
df.index = ['语文', '数学', '英语', 'Python']
print('改行索引后：')
print(df)
df.columns = ['赵敏', '王芳']
print('改列索引后：')
print(df)


# ===== [k2-rename] rename：只改想改的名字，返回新表、原表不变 =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89], 'wang': [90, 80]},
                  index=['语文', '数学'])
df2 = df.rename(columns={'zhao': '赵敏', 'wang': '王芳'},
                index={'语文': '语文课'})
print('rename 之后的新表：')
print(df2)
print('原表没变：', list(df.columns))


# ===== [k2-basic] shape / index / columns / values 一把打完，values 是 ndarray =====
import pandas as pd

df = pd.DataFrame({'zhao': [93, 89, 80, 92], 'wang': [90, 80, 70, 75]},
                  index=['语文', '数学', '英语', 'Python'])
print('shape：', df.shape)
print('index：', list(df.index))
print('columns：', list(df.columns))
print('values：')
print(df.values)
print('values 的类型：', type(df.values).__name__)


# ===== [k2-dtypes] dtypes：数值列 int64、字符串列 str、布尔列 bool =====
import pandas as pd

df = pd.DataFrame({'姓名': ['赵敏', '王芳', '李雷'],
                   '成绩': [93, 89, 80],
                   '及格': [True, True, False]})
print(df.dtypes)
print('字符串列的 dtype：', df['姓名'].dtype)
print('单独一列的类型：', type(df['姓名']).__name__)


# ===== [k2-headtail] head / tail：只看前几行、后几行 =====
import pandas as pd

df = pd.DataFrame({'姓名': ['赵敏', '王芳', '李雷', '韩梅', '张伟', '刘洋'],
                   '成绩': [93, 89, 80, 92, 78, 85]})
print('前 3 行：')
print(df.head(3))
print('后 2 行：')
print(df.tail(2))


# ===== [k2-info] info()：行数 / 非空计数 / 每列 dtype（str、float64、datetime64[us]） =====
import pandas as pd

df = pd.DataFrame({'姓名': ['赵敏', '王芳', '李雷'],
                   '成绩': [93, None, 80],
                   '入学日期': pd.to_datetime(['2021-09-01', '2021-09-01', '2022-09-01'])})
df.info()
print('--- 直接打印看内容 ---')
print(df)


# ===== [k2-describe] describe()：数值列的八项统计摘要 =====
import pandas as pd

df = pd.DataFrame({'姓名': ['赵敏', '王芳', '李雷', '韩梅'],
                   '班级': ['一班', '二班', '一班', '二班'],
                   '成绩': [93, 89, 80, 92]})
print(df.describe())
print('字符串列被跳过了，单独看它：')
print(df['班级'].describe())


# 注：页面上这些示例是“故意报错”的，未收录到本文件：k2-list-err、k2-dict-err
