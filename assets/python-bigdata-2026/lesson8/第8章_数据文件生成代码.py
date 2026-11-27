# -*- coding: utf-8 -*-
"""第 8 章《Pandas 数据处理与分析》—— 数据文件生成代码

用途
    把课件里用到的 Excel / CSV 数据文件一次性造出来：课堂上要现场重做数据、
    或者想核对下载区里的文件是怎么来的，都可以直接跑这一段代码。

用法
    1. 装依赖：pip install pandas openpyxl numpy
    2. 把本文件与 订单.xlsx、订单_J1.xlsx、订单_J2.xlsx 放在同一个文件夹
       （这三个原始数据到课程网页「配套代码与数据下载」里下载）
    3. 运行：python 第8章_数据文件生成代码.py
    4. 生成结果默认放在本文件旁边的「第8章数据」文件夹，可用 --out 指定别处

能生成的文件（9 个）
    商品.csv              7 种商品信息（进价 / 产地 / 库存 / 销售价），GBK 编码
    精简订单数据.csv      页面统一使用的 12 行示例数据，UTF-8
    学生成绩.csv          4 门课 × 2 名同学，UTF-8
    月度销售_2025.csv     2025 年 12 个月销售数据（随机种子 42），UTF-8
    订单_clean.xlsx       订单.xlsx 删除缺失值（305 行 × 7 列）
    订单_fill.xlsx        订单.xlsx 填充缺失值（310 行 × 7 列）
    订单_new.xlsx         删除缺失值 + 地区编码 + 周次（305 行 × 9 列）
    订单_part.csv         订单日期 / 商品名称 / 数量 三列，GBK 编码
    订单_Jan.xlsx         订单_J1 + 订单_J2 拼接（332 行）

注意
    * 订单.xlsx、订单_J1.xlsx、订单_J2.xlsx 是课件给定的原始数据，无法凭空生成，
      必须先下载；本脚本读取它们，产出 5 个派生文件。
    * 找不到这三个文件时，脚本只生成前 4 个小 CSV，其余给出提示，不会报错退出。
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

# 本文件所在文件夹（默认到这里找原始数据、也默认生成到这里）
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR_DEFAULT = os.path.join(HERE, '第8章数据')

# 12 行精简订单数据：页面所有示例统一用它，含 1 组缺失值、1 组重复行、2 个异常值
MINI_ORDER_CSV = """订单编号,订单日期,用户ID,商品名称,单价,数量,金额
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

# 商品信息表（merge 连接练习用）：7 种商品
PRODUCTS = [
    ['围巾', 15, '江苏', 50, 21.6],
    ['运动服', 130, '北京', 20, 210],
    ['T恤', 38, '上海', 125, 65.8],
    ['卫衣', 90, '广东', 62, 126.5],
    ['休闲鞋', 110, '广东', 210, 162],
    ['运动鞋', 152, '福建', 48, 237],
    ['太阳帽', 11, '浙江', 10, 22],
]
PRODUCT_COLUMNS = ['商品名称', '进价', '产地', '库存', '销售价']


def fmt_size(n: int) -> str:
    if n < 1024:
        return f'{n} B'
    if n < 1024 * 1024:
        return f'{n / 1024:.1f} KB'
    return f'{n / 1024 / 1024:.1f} MB'


def report(out_dir: str, name: str, df: pd.DataFrame | None = None) -> None:
    """打印一行生成结果：文件名、行列数、大小。"""
    path = os.path.join(out_dir, name)
    size = fmt_size(os.path.getsize(path))
    if df is None:
        print(f'  {name:<22} {size:>8}')
    else:
        print(f'  {name:<22} {df.shape[0]:>4} 行 × {df.shape[1]} 列   {size:>8}')


def save_excel(df: pd.DataFrame, out_dir: str, name: str) -> None:
    df.to_excel(os.path.join(out_dir, name), index=False)
    report(out_dir, name, df)


# ---------- 第一部分：不依赖原始数据，直接生成 4 个小文件 ----------
def gen_small_files(out_dir: str) -> None:
    # ① 商品.csv：GBK 编码 + Windows 换行（与课件文件一致），销售价按最少小数位输出
    products = pd.DataFrame(PRODUCTS, columns=PRODUCT_COLUMNS)
    products['销售价'] = products['销售价'].map(lambda x: f'{x:g}')
    path = os.path.join(out_dir, '商品.csv')
    products.to_csv(path, encoding='gbk', index=False, lineterminator='\r\n')
    report(out_dir, '商品.csv', products)

    # ② 精简订单数据.csv：直接写入固定文本，保证与页面示例逐字一致
    path = os.path.join(out_dir, '精简订单数据.csv')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(MINI_ORDER_CSV)
    report(out_dir, '精简订单数据.csv', pd.read_csv(path))

    # ③ 学生成绩.csv：第 1—3 章的示例小表
    score = pd.DataFrame({'zhao': [93, 89, 80, 92], 'wang': [90, 80, 70, 75]},
                         index=['语文', '数学', '英语', 'Python'])
    score.to_csv(os.path.join(out_dir, '学生成绩.csv'), encoding='utf-8')
    report(out_dir, '学生成绩.csv', score)

    # ④ 月度销售_2025.csv：课后作业 1 用，固定随机种子 42
    np.random.seed(42)
    months = pd.date_range(start='2025-01-01', periods=12, freq='MS')
    sales = pd.DataFrame({
        '服装': np.random.randint(0, 51, size=12),
        '化妆品': np.random.randint(0, 51, size=12),
        '日用品': np.random.randint(0, 51, size=12),
    }, index=months)
    sales.to_csv(os.path.join(out_dir, '月度销售_2025.csv'),
                 encoding='utf-8', index_label='月份')
    report(out_dir, '月度销售_2025.csv', sales)


# ---------- 第二部分：从 订单.xlsx 派生 4 个文件 ----------
def gen_from_orders(src_dir: str, out_dir: str) -> bool:
    path = os.path.join(src_dir, '订单.xlsx')
    if not os.path.exists(path):
        print('  [跳过] 没找到 订单.xlsx —— 请先到课程网页下载，再放到本文件旁边')
        return False

    try:
        orders = pd.read_excel(path, sheet_name='订单')   # 工作簿里第一个工作表
    except ValueError:                                     # 没有名为「订单」的表时读第一张表
        orders = pd.read_excel(path)

    # ① 删除缺失值 → 订单_clean.xlsx（305 行）
    clean = orders.dropna()
    save_excel(clean, out_dir, '订单_clean.xlsx')

    # ② 填充缺失值 → 订单_fill.xlsx（310 行）
    #    课件做法：商品名称填最常见的「T恤」，单价填最常见的 65.8，数量按 1 件处理，
    #    这 5 行的金额 = 单价 × 数量，重新算一遍。
    filled = orders.copy()
    filled['商品名称'] = filled['商品名称'].fillna('T恤')
    filled['单价'] = filled['单价'].fillna(65.8)
    filled['数量'] = filled['数量'].fillna(1)
    missing_rows = orders['商品名称'].isna()
    filled.loc[missing_rows, '金额'] = (filled.loc[missing_rows, '单价']
                                        * filled.loc[missing_rows, '数量'])
    save_excel(filled, out_dir, '订单_fill.xlsx')

    # ③ 清洗 + 地区编码 + 周次 → 订单_new.xlsx（305 行 × 9 列）
    new = clean.copy()
    new['地区编码'] = new['用户ID'].astype(str).str[:2].astype(int)
    new['周次'] = pd.to_datetime(new['订单日期']).dt.isocalendar().week.astype(int)
    save_excel(new, out_dir, '订单_new.xlsx')

    # ④ 只导出三列（GBK 编码）→ 订单_part.csv
    part = orders[['订单日期', '商品名称', '数量']]
    part.to_csv(os.path.join(out_dir, '订单_part.csv'), encoding='gbk', index=False)
    report(out_dir, '订单_part.csv', part)
    return True


# ---------- 第三部分：合并 1 月上下半月 ----------
def gen_jan(src_dir: str, out_dir: str) -> bool:
    j1_path = os.path.join(src_dir, '订单_J1.xlsx')
    j2_path = os.path.join(src_dir, '订单_J2.xlsx')
    if not (os.path.exists(j1_path) and os.path.exists(j2_path)):
        print('  [跳过] 没找到 订单_J1.xlsx / 订单_J2.xlsx —— 请先到课程网页下载')
        return False

    jan = pd.concat([pd.read_excel(j1_path), pd.read_excel(j2_path)], ignore_index=True)
    save_excel(jan, out_dir, '订单_Jan.xlsx')
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description='生成第 8 章用到的 Excel / CSV 数据文件')
    parser.add_argument('--src', default=HERE, help='原始数据所在的文件夹（默认本文件所在文件夹）')
    parser.add_argument('--out', default=OUT_DIR_DEFAULT, help='生成文件放到哪个文件夹')
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    print('运行环境：Python %s / pandas %s / numpy %s' % (
        sys.version.split()[0], pd.__version__, np.__version__))
    print('原始数据：%s' % args.src)
    print('输出目录：%s\n' % args.out)

    print('一、直接生成的小数据（不需要原始数据）')
    gen_small_files(args.out)

    print('\n二、从 订单.xlsx 派生')
    gen_from_orders(args.src, args.out)

    print('\n三、拼接 1 月订单')
    gen_jan(args.src, args.out)

    print('\n完成。生成的文件可以和第 8 章网页「配套代码与数据下载」里的同名文件对照，'
          '内容一致（CSV 与网页上的文件逐字节相同）。')


if __name__ == '__main__':
    main()
