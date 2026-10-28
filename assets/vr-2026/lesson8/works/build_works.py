#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
build_works.py —— 把「全部提交」的学生作业一次性生成第 8 次课作品墙（含每个学生的“打开作业”详情页）。

用法：
    py -3 build_works.py --src "D:\收作业\第8次课" --out "D:\各种工作\个人网站\assets\vr-2026\lesson8\works"

输入：--src 下每个学生一个文件夹，形如 230812008_严玉_第8次课/
      图片：优先 S2.png / S2.jpg；没有就按文件名找 03/工作台/workbench（S2）、01/交互/interaction（S1）、02/转换/converter（S3）
      可选：说明.md  第一行一句话（若第一行是“公开名：昵称”，用它当展示名；第一行“不公开”则跳过）
      可选：许可卡.txt  里含“模型/许可”的行
      可选：workbench_scene.json / scene.json
输出（<out>）：
      <id>.jpg|png             作品墙缩略图（S2）
      <id>/S1.* S2.* S3.*      详情页用三张图
      <id>/scene.json          场景 JSON
      <id>/license.txt         许可
      <id>.html                单个学生的“打开作业”详情页
      works-data.js            作品墙数据（全部作品，按学号排序；含 detail）
      works-manifest.csv       教师核对清单

规则：全部提交都上墙，不筛选；`不公开` 的跳过并在清单里标记 skip。
"""
import argparse
import csv
import html
import os
import re
import shutil
import sys

IMG_EXT = ('.png', '.jpg', '.jpeg', '.webp')
S2_KEYS = ('s2', '03', '工作台', 'workbench')
S1_KEYS = ('s1', '01', '交互', 'interaction', '实验室')
S3_KEYS = ('s3', '02', '转换', 'converter', '转换器')


def read_text(path):
    for enc in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            with open(path, 'r', encoding=enc) as f:
                return f.read()
        except Exception:
            continue
    return ''


def parse_caption(text):
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
        if re.match(r'^公开名\s*[：:]', l) or l == '不公开':
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
            model = re.sub(r'^.*?[：:]\s*', '', s)[:60]
        if not lic and re.search(r'许可|license|授权', s, re.I):
            lic = re.sub(r'^.*?[：:]\s*', '', s)[:60]
    return model or '', lic or ''


def list_images(folder):
    return [f for f in os.listdir(folder) if f.lower().endswith(IMG_EXT)]


def pick_image(files, keys):
    for k in keys:
        for f in files:
            if k in f.lower():
                return f
    return None


def copy_img(folder, name, target_base, target_dir):
    src = os.path.join(folder, name)
    ext = os.path.splitext(name)[1].lower()
    if ext == '.jpeg':
        ext = '.jpg'
    dst_name = target_base + ext
    shutil.copyfile(src, os.path.join(target_dir, dst_name))
    return dst_name


def detail_html(entry, images, scene_text, license_text):
    e = lambda s: html.escape(str(s), quote=True)
    labels = {'S1': 'S1 · 01 交互实验室（状态机 / HUD）',
              'S2': 'S2 · 03 模型工作台（旋转 / 缩放 / 换色 / 动画，上墙图）',
              'S3': 'S3 · 02 模型转换器（源 / 输出 / 变化%）'}
    cards = []
    for key in ('S1', 'S2', 'S3'):
        rel = images.get(key)
        if not rel:
            continue
        cards.append(
            '<figure class="shot"><a href="%s" target="_blank" rel="noopener"><img src="%s" alt="%s"></a>'
            '<figcaption>%s</figcaption></figure>' % (e(rel), e(rel), e(labels[key]), e(labels[key]))
        )
    json_block = ''
    if scene_text:
        json_block = '<h2>场景 JSON（节选）</h2><pre class="json">%s</pre>' % html.escape(scene_text[:6000])
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>%(name)s · 第8次课课堂作业 | 2026年虚拟现实技术</title>
<style>
  body{margin:0;background:#f6f3ec;color:#2b2820;font-family:"Times New Roman","宋体",SimSun,serif}
  .wrap{max-width:1080px;margin:0 auto;padding:24px 20px 60px}
  a{color:#1f3864}
  .top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
  .back{font-size:14px;text-decoration:none;color:#306998}
  h1{font-size:30px;margin:14px 0 6px;color:#1f3864}
  .cap{font-size:19px;line-height:1.7;margin:8px 0 4px}
  .tags span{display:inline-block;background:#eaf1f8;color:#306998;border-radius:999px;padding:3px 11px;font-size:13px;margin:6px 8px 0 0}
  .tags .lic{background:#f3efe3;color:#8a6d1f}
  h2{font-size:19px;margin:26px 0 10px;color:#1f3864;border-left:4px solid #306998;padding-left:10px}
  .shots{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
  .shot{margin:0;background:#fffdf8;border:1px solid #e4ddcc;border-radius:10px;overflow:hidden}
  .shot img{display:block;width:100%%;height:auto}
  .shot figcaption{font-size:13px;color:#5d594c;padding:8px 10px;line-height:1.5}
  .json{background:#101a2c;color:#dfe8f5;border-radius:8px;padding:12px 14px;font-size:12.5px;line-height:1.5;overflow:auto;max-height:360px;white-space:pre-wrap;word-break:break-all}
  .licbox{background:#fffdf8;border:1px solid #e4ddcc;border-radius:10px;padding:12px 16px;font-size:14px;line-height:1.9;white-space:pre-wrap}
  .downloads{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}
  .downloads a{background:#306998;color:#fff;text-decoration:none;border-radius:7px;padding:8px 13px;font-size:14px}
  .note{font-size:13px;color:#7a7568;margin-top:26px;line-height:1.8}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <a class="back" href="../../../../vr-2026-lesson8-works.html">← 返回第 8 次课作品墙</a>
    <a class="back" href="../../../../vr-2026-lesson8.html">本课主页 →</a>
  </div>
  <h1>%(name)s</h1>
  <p class="cap">%(caption)s</p>
  <div class="tags"><span>模型：%(model)s</span><span class="lic">%(license)s</span></div>
  %(cards)s
  %(json)s
  <h2>许可卡</h2>
  <div class="licbox">%(lictext)s</div>
  <div class="downloads">
    <a href="%(s2)s" download>下载上墙图 S2</a>
    %(dl)s
  </div>
  <p class="note">本页由第 8 次课作品墙生成；全部提交作品都会公开展示。如作者要求更正一句话、署名或许可信息，请联系教师修改。</p>
</div>
</body>
</html>
""" % {
        'name': e(entry['name']),
        'caption': e(entry['caption']),
        'model': e(entry['model']),
        'license': e(entry['license']),
        'cards': '\n'.join(cards),
        'json': json_block,
        'lictext': html.escape(license_text or '（未提交许可卡）'),
        's2': e(images.get('S2', '')),
        'dl': ''.join('<a href="%s" download>下载 %s</a>' % (e(images[k]), e(k)) for k in ('S1', 'S3') if images.get(k)),
    }


def main():
    ap = argparse.ArgumentParser(description='生成第8次课作品墙数据（全部提交）')
    ap.add_argument('--src', required=True, help='收作业根目录（每个学生一个文件夹）')
    ap.add_argument('--out', required=True, help='站点 works 目录（图片、详情页与 works-data.js 输出到这里）')
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

        files = list_images(folder)
        s2name = pick_image(files, S2_KEYS) or (files[0] if files else None)
        if not s2name:
            skipped.append((sid, display, '无截图'))
            continue
        s1name = pick_image(files, S1_KEYS)
        s3name = pick_image(files, S3_KEYS)
        if s1name == s2name:
            s1name = None
        if s3name in (s2name, s1name):
            s3name = None

        scene_path = None
        for cand in ('workbench_scene.json', 'scene.json'):
            p = os.path.join(folder, cand)
            if os.path.isfile(p):
                scene_path = p
                break
        scene_text = read_text(scene_path) if scene_path else ''

        licsrc = os.path.join(folder, '许可卡.txt')
        if not os.path.isfile(licsrc):
            licsrc = os.path.join(folder, '许可证.txt')
        license_text = read_text(licsrc) if os.path.isfile(licsrc) else ''

        caption = (caption or '（未写说明）')[:60]
        model = model or '待核实'
        lic = lic or '待核实'

        entry = {'id': sid, 'name': display, 'caption': caption, 'model': model, 'license': lic}

        if args.dry_run:
            entry.update(img='', detail='')
            entries.append(entry)
            rows.append([sid, display, '', caption, model, lic, 'ok'])
            continue

        sub = os.path.join(out, sid)
        os.makedirs(sub, exist_ok=True)
        images = {}
        images['S2'] = copy_img(folder, s2name, 'S2', sub)
        if s1name:
            images['S1'] = copy_img(folder, s1name, 'S1', sub)
        if s3name:
            images['S3'] = copy_img(folder, s3name, 'S3', sub)
        s2ext = os.path.splitext(images['S2'])[1]
        thumb = sid + s2ext
        shutil.copyfile(os.path.join(sub, images['S2']), os.path.join(out, thumb))
        if scene_text:
            with open(os.path.join(sub, 'scene.json'), 'w', encoding='utf-8') as f:
                f.write(scene_text)
        if license_text:
            with open(os.path.join(sub, 'license.txt'), 'w', encoding='utf-8') as f:
                f.write(license_text)
        entry['img'] = thumb
        entry['detail'] = sid + '.html'
        with open(os.path.join(out, sid + '.html'), 'w', encoding='utf-8') as f:
            detail_imgs = {k: sid + '/' + v for k, v in images.items()}
            f.write(detail_html(entry, detail_imgs, scene_text, license_text))
        entries.append(entry)
        rows.append([sid, display, thumb, caption, model, lic, 'ok'])

    seen = {}
    for e in entries:
        seen[e['id']] = e
    entries = [seen[k] for k in sorted(seen.keys())]

    js_lines = [
        '/* 由 build_works.py 生成；全部提交作品，不筛选。 */',
        'window.LESSON8_WORKS = [',
    ]
    for e in entries:
        extra = (", detail: %s" % _js(e['detail'])) if e.get('detail') else ''
        js_lines.append('  { id: %s, name: %s, caption: %s, img: %s, model: %s, license: %s%s },' % (
            _js(e['id']), _js(e['name']), _js(e['caption']), _js(e['img']), _js(e['model']), _js(e['license']), extra))
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
        w.writerow(['学号', '展示名', '缩略图', '一句话', '模型', '许可', '状态'])
        w.writerows(rows)
        for s in skipped:
            w.writerow([s[0], s[1], '', '', '', '', 'skip: ' + s[2]])
    print('已写：works-data.js / works-manifest.csv / <学号>.html 详情页')
    print('下一步：部署站点或运行 deploy_works.ps1')
    return 0


def _js(s):
    return "'" + str(s).replace('\\', '\\\\').replace("'", "\\'").replace('\n', ' ') + "'"


if __name__ == '__main__':
    sys.exit(main())
