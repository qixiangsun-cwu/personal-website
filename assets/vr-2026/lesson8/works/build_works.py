#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
build_works.py —— 把「全部提交」的学生作业一次性生成第 8 次课作品墙数据。

用法（在站点仓库的 assets/vr-2026/lesson8/works 目录，或任意位置用绝对路径）：
    python build_works.py --src "D:\收作业\第8次课" --out "D:\各种工作\个人网站\assets\vr-2026\lesson8\works"

输入：--src 下每个学生一个文件夹，形如 230812003_张三_第8次课/
      必需：一张截图（优先 S2.png / S2.jpg；没有就取文件夹里第一张 png/jpg）
      可选：说明.md  第一行一句话（若第一行是“公开名：昵称”，用它当展示名；第一行“不公开”则跳过）
      可选：许可卡.txt  里含“模型/许可”的行（没有就写“待核实”）
输出：
      <out>/<学号>.png              复制后的展示图
      <out>/works-data.js           页面读取的数据（全部作品，按学号排序）
      <out>/works-manifest.csv     教师核对用清单（学号/姓名/图/一句话/许可/状态）

规则：全部提交都上墙，不筛选；`不公开` 的跳过并在清单里标记 skip。
"""
import argparse
import csv
import os
import re
import shutil
import sys

IMG_EXT = ('.png', '.jpg', '.jpeg', '.webp')
PREFERRED = ('s2.png', 's2.jpg', 's2.jpeg', 'screenshot.png', '截图.png')


def read_text(path):
    for enc in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            with open(path, 'r', encoding=enc) as f:
                return f.read()
        except Exception:
            continue
    return ''


def first_line(text):
    for line in text.splitlines():
        line = line.strip().lstrip('#').strip()
        if line:
            return line
    return ''


def parse_caption(text):
    """返回 (caption, public_name, is_private)。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    private = any(re.match(r'^不公开\s*$', l) for l in lines[:3])
    public = ''
    for l in lines[:4]:
        m = re.match(r'^公开名\s*[：:]\s*(.+)$', l)
        if m:
            public = m.group(1).strip()
            break
    caption = ''
    for l in lines:
        if re.match(r'^公开名\s*[：:]', l) or l in ('不公开',):
            continue
        caption = l.lstrip('#').strip()
        if caption:
            break
    return caption, public, private


def parse_license(text):
    model = lic = ''
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if not model and re.search(r'模型|model', s, re.I):
            model = re.sub(r'^.*?[：:]\s*', '', s)[:40]
        if not lic and re.search(r'许可|license|授权', s, re.I):
            lic = re.sub(r'^.*?[：:]\s*', '', s)[:40]
    return model, lic


def find_image(folder):
    files = [f for f in os.listdir(folder) if f.lower().endswith(IMG_EXT)]
    if not files:
        return None
    low = {f.lower(): f for f in files}
    for p in PREFERRED:
        if p in low:
            return low[p]
    # 其次：名字里带 s2 的
    for f in files:
        if 's2' in f.lower():
            return f
    return sorted(files)[0]


def main():
    ap = argparse.ArgumentParser(description='生成第8次课作品墙数据（全部提交）')
    ap.add_argument('--src', required=True, help='收作业根目录（每个学生一个文件夹）')
    ap.add_argument('--out', required=True, help='站点 works 目录（图片与 works-data.js 输出到这里）')
    ap.add_argument('--dry-run', action='store_true', help='只打印，不写文件')
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    out = os.path.abspath(args.out)
    if not os.path.isdir(src):
        print('[X] 找不到收作业目录：%s' % src)
        return 2
    if not args.dry_run:
        os.makedirs(out, exist_ok=True)

    entries, rows, skipped = [], [], []
    for name in sorted(os.listdir(src)):
        folder = os.path.join(src, name)
        if not os.path.isdir(folder):
            continue
        text = read_text(os.path.join(folder, '说明.md')) if os.path.isfile(os.path.join(folder, '说明.md')) else ''
        caption, public, private = parse_caption(text)
        lic_text = read_text(os.path.join(folder, '许可卡.txt')) if os.path.isfile(os.path.join(folder, '许可卡.txt')) else ''
        model, lic = parse_license(lic_text)

        m = re.match(r'^(\d{6,})[_\-\s]+(.+?)(?:_第8次课)?$', name)
        sid = m.group(1) if m else re.sub(r'[^0-9A-Za-z]', '', name)[:12] or name
        realname = m.group(2).strip() if m else name
        display = ('%s %s' % (sid, public)) if public else ('%s %s' % (sid, realname))

        if private:
            skipped.append((sid, display, '不公开（说明.md）'))
            continue

        img = find_image(folder)
        if not img:
            skipped.append((sid, display, '无截图'))
            continue

        ext = os.path.splitext(img)[1].lower()
        out_img = '%s%s' % (sid, '.png' if ext == '.png' else ext)
        if not args.dry_run:
            shutil.copyfile(os.path.join(folder, img), os.path.join(out, out_img))

        caption = (caption or '（未写说明）')[:60]
        model = model or '待核实'
        lic = lic or '待核实'
        entries.append({
            'id': sid,
            'name': display,
            'caption': caption,
            'img': out_img,
            'model': model,
            'license': lic,
        })
        rows.append([sid, display, out_img, caption, model, lic, 'ok'])

    # 去重（同一学号后出现的覆盖）
    seen = {}
    for e in entries:
        seen[e['id']] = e
    entries = [seen[k] for k in sorted(seen.keys())]

    js_lines = [
        '/* 由 build_works.py 生成（%s）；全部提交作品，不筛选。 */' % 'dry-run' if args.dry_run else '/* 由 build_works.py 生成；全部提交作品，不筛选。 */',
        'window.LESSON8_WORKS = [',
    ]
    for e in entries:
        js_lines.append('  { id: %s, name: %s, caption: %s, img: %s, model: %s, license: %s },' % (
            _js(e['id']), _js(e['name']), _js(e['caption']), _js(e['img']), _js(e['model']), _js(e['license'])))
    js_lines.append('];')
    data_js = '\n'.join(js_lines) + '\n'

    print('========== 第 8 次课作品墙生成 ==========')
    print('收作业目录：%s' % src)
    print('输出目录  ：%s' % out)
    print('作品数    ：%d（全部提交）' % len(entries))
    for e in entries:
        print('  + %s  %s  | %s | %s' % (e['id'], e['name'], e['caption'][:24], e['license']))
    if skipped:
        print('跳过      ：%d' % len(skipped))
        for s in skipped:
            print('  - %s  %s（%s）' % s)

    if args.dry_run:
        print('（dry-run：未写文件）')
        return 0

    with open(os.path.join(out, 'works-data.js'), 'w', encoding='utf-8') as f:
        f.write(data_js)
    with open(os.path.join(out, 'works-manifest.csv'), 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['学号', '展示名', '图片', '一句话', '模型', '许可', '状态'])
        w.writerows(rows)
        for s in skipped:
            w.writerow([s[0], s[1], '', '', '', '', 'skip: ' + s[2]])
    print('已写：works-data.js / works-manifest.csv')
    print('下一步：部署站点或运行 deploy_works.ps1')
    return 0


def _js(s):
    return "'" + str(s).replace('\\', '\\\\').replace("'", "\\'").replace('\n', ' ') + "'"


if __name__ == '__main__':
    sys.exit(main())
