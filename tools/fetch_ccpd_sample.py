# -*- coding: utf-8 -*-
"""从 CCPD 抽一小批真实照片 + 标注, 用来填充真实评测集（第八轮）

用法:
  python 项目代码/tools/fetch_ccpd_sample.py                       # 默认 300 张
  python 项目代码/tools/fetch_ccpd_sample.py --max 400 --per-subset 60
  python 项目代码/tools/fetch_ccpd_sample.py --no-proxy            # 直连(系统代理已开时)

为什么能"只拉一点":
  CCPD2019 整包 12.5 GB。这里用的是一个按**单文件**存放的 30k 子集 zip
  (zenitsu09/ccpd-subset-30k, 1.5 GB) —— zip 的中央目录在文件末尾, 可以只把
  目录读下来(几十 KB), 再从里面**挑几百个条目**用 HTTP Range 精确取出来,
  全程只下十几 MB。不需要任何登录, 也不需要整包。
  另一个好处: 文件名保留了 CCPD 原始标注串, 里面就带着 subset 标签、
  可见框、四角、亮度和模糊度 —— 标注不用另找。

文件名格式(CCPD 官方约定):
  <id>-<面积>_<倾角>-<x1,y1_x2,y2>-<四角>-<7个字符索引>-<亮度>-<模糊度>_<subset>_<序号>.jpg
  字符索引按 CCPD 的三张表查: provinces / alphabets / ads(见下方常量)。
  许可证: CCPD 为 MIT(见 detectRecog/CCPD); 引用请注明
  《Towards End-to-End License Plate Detection and Recognition》(ECCV 2018)。

输出: <out>/<subset>/<原文件名>.jpg + <out>/labels.csv
      labels.csv 列: file,text,category,plate_w,plate_h,bx,by,bw,bh,qx1..qy4
      bx..bh 是**四角**的轴对齐外接框(不是文件名第 2 段那个 rect 字段: 实测它对不上车牌);
      qx1..qy4 是**四角本身**(已按质心极角重排成环)。评测要用它算多边形 IoU ——
      四角是旋转矩形, 拿外接框当真值连"完美轴对齐检测器"都过不了 IoU>=0.5(见 matlab/quadIoU.m)。
      text 为空 = 该图本来就没有车牌(负样本, 如 ccpd_np), 评测时单独统计拒识/误检。

⚠️ 用法边界(和自拍照片不是一回事):
  - CCPD 是**停车场固定视角、单车、近景**为主的图; 它能补"真实照片的数量"
    和"小目标/模糊/夜间的样本", 补不了"真实场景的构图多样性"。
  - 车牌号是真实号牌。本仓库只在本地评测用, 图不随版本库分发。
"""
import argparse
import csv
import math
import io
import os
import re
import urllib.request
import zlib
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ZIP_URL = ('https://huggingface.co/datasets/zenitsu09/ccpd-subset-30k/'
           'resolve/main/ccpd_subset_30k.zip')

# CCPD 官方字符索引表（README 原文，末位 O = "无字符"）
PROVINCES = ["皖", "沪", "津", "渝", "冀", "晋", "蒙", "辽", "吉", "黑", "苏", "浙",
             "京", "闽", "赣", "鲁", "豫", "鄂", "湘", "粤", "桂", "琼", "川", "贵",
             "云", "藏", "陕", "甘", "青", "宁", "新", "警", "学", "O"]
ALPHABETS = list('ABCDEFGHJKLMNPQRSTUVWXYZ') + ['O']
ADS = list('ABCDEFGHJKLMNPQRSTUVWXYZ') + list('0123456789') + ['O']

NAME_RE = re.compile(
    r'^(?P<id>\d+)-(?P<area>\d+)_(?P<tilt>\d+)-'
    r'(?P<x1>\d+),(?P<y1>\d+)_(?P<x2>\d+),(?P<y2>\d+)-'
    r'(?P<corners>[^-]+)-(?P<idx>[\d_]+)-'
    r'(?P<bright>\d+)-(?P<blur>\d+)_(?P<subset>ccpd_[a-z]+)_(?P<seq>\d+)\.jpg$')

# 无车牌图(ccpd_np)的文件名只有 <序号>_ccpd_np_<编号>.jpg, 没有标注串
NP_RE = re.compile(r'^(?P<seq>\d+)_ccpd_np_(?P<no>\d+)\.jpg$')


class HttpRangeFile(io.RawIOBase):
    """只读的远程文件: 每次 read 都发一个 HTTP Range 请求"""

    def __init__(self, url, proxy=None):
        self.url = url
        handlers = [urllib.request.ProxyHandler({'http': proxy, 'https': proxy})] if proxy else []
        self.opener = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
        with self.opener.open(req, timeout=60) as r:
            self.size = int(r.headers['Content-Length'])
        self.pos = 0
        self.requests = 0

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def tell(self):
        return self.pos

    def seekable(self):
        return True

    def readable(self):
        return True

    def fetch(self, start, end):
        req = urllib.request.Request(
            self.url, headers={'Range': 'bytes=%d-%d' % (start, end), 'User-Agent': 'Mozilla/5.0'})
        with self.opener.open(req, timeout=180) as r:
            self.requests += 1
            return r.read()

    def readinto(self, b):
        n = len(b)
        if n == 0 or self.pos >= self.size:
            return 0
        chunk = self.fetch(self.pos, min(self.size - 1, self.pos + n - 1))
        b[:len(chunk)] = chunk
        self.pos += len(chunk)
        return len(chunk)


def parse_entry(name):
    """CCPD 文件名 -> 记录; 解析不了(无车牌图)返回 None"""
    if NP_RE.match(name):
        return {'text': '', 'subset': 'ccpd_np', 'plate_w': 0, 'plate_h': 0, 'bbox': [0, 0, 0, 0],
                'quad': [0] * 8, 'bright': -1, 'blur': -1, 'tilt': -1, 'noplate': True}
    m = NAME_RE.match(name)
    if not m:
        return None
    g = m.groupdict()
    idx = [int(v) for v in g['idx'].split('_')]
    if len(idx) == 7:                       # 普通车牌: 省 + 字母 + 5 位字母数字
        tables = (PROVINCES,) + (ALPHABETS,) + (ADS,) * 5
    elif len(idx) == 8:                     # 新能源: 省 + 字母 + 1 位 + 5 位数字
        tables = (PROVINCES,) + (ALPHABETS,) + (ALPHABETS,) + (ADS,) * 5
    else:
        return None
    if any(i >= len(t) for i, t in zip(idx, tables)):
        return None
    # 第 3 位: 字母表里 24=O(非法), 而数码表里 24=数字 0 —— 新能源第 3 位可能是数字,
    # 两张表在 0~23 上完全一致, 所以在下标 >=24 时改查 ADS 是安全的。
    parts = [t[i] for i, t in zip(idx, tables)]
    if len(idx) == 8 and idx[2] >= 24:
        parts[2] = ADS[idx[2]]
    text = ''.join(parts)
    if 'O' in text:
        return None
    # 用**四角**定框, 不用文件名第 2 段的 rect 字段。实测(皖S69016 那张):
    #   rect = [385,430,83,53], 四角外接框 = [384,440,89,47], 车牌蓝色像素 = [377,440,95,47]
    #   —— rect 整体偏上、比车牌高 10 px, 叠在原图上框到车标去了; 四角才贴合车牌。
    # 所以 rect 那一段(和它前面的 <面积> 一样)是另一个口径的标注, 别拿来做真值。
    pts = []
    for q in g['corners'].split('_'):
        px, py = q.split(',')
        pts.append((int(px), int(py)))
    if len(pts) != 4:
        return None
    pts = order_quad(pts)                     # 少数图的顶点序是乱的, 按质心极角重排
    xs = [q[0] for q in pts]
    ys = [q[1] for q in pts]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)

    def _d(q, r):
        return ((q[0] - r[0]) ** 2 + (q[1] - r[1]) ** 2) ** 0.5

    edge_w = (_d(pts[0], pts[1]) + _d(pts[2], pts[3])) / 2.0   # 上下边 -> 车牌宽
    edge_h = (_d(pts[1], pts[2]) + _d(pts[3], pts[0])) / 2.0   # 左右边 -> 车牌高
    return {'text': text, 'subset': g['subset'],
            'plate_w': int(round(edge_w)), 'plate_h': int(round(edge_h)),
            'bbox': [x1, y1, x2 - x1 + 1, y2 - y1 + 1],
            'quad': [c for q in pts for c in q],
            'bright': int(g['bright']), 'blur': int(g['blur']), 'tilt': int(g['tilt'])}


LABEL_HEAD = ['file', 'text', 'category', 'plate_w', 'plate_h', 'bx', 'by', 'bw', 'bh',
              'qx1', 'qy1', 'qx2', 'qy2', 'qx3', 'qy3', 'qx4', 'qy4']


def order_quad(pts):
    """把 4 个顶点按质心极角排成逆时针环 —— 少数图 CCPD 给的顶点序是乱的,
    不排的话多边形自交, 面积(进而 IoU)会算错"""
    cx = sum(q[0] for q in pts) / 4.0
    cy = sum(q[1] for q in pts) / 4.0
    return sorted(pts, key=lambda q: math.atan2(q[1] - cy, q[0] - cx))


def label_row(relpath, rec):
    """一条标注 -> labels.csv 的一行(负样本 text 空、框全 0, 让 bench_stage 走负样本口径)"""
    if rec.get('noplate'):
        return [relpath, '', 'ccpd_np', 0, 0, 0, 0, 0, 0] + [0] * 8
    b = rec['bbox']
    return [relpath, rec['text'], rec['subset'], rec['plate_w'], rec['plate_h'],
            b[0], b[1], b[2], b[3]] + list(rec['quad'])


def stratified(records, max_n, per_subset):
    """按 subset 轮转, 每个 subset 内按车牌宽等距抽 —— 小目标到大目标都要有"""
    by = {}
    for e in records:
        if e['rec'].get('noplate'):
            continue
        by.setdefault(e['rec']['subset'], []).append(e)
    picks, order = [], sorted(by)
    for s in order:
        v = sorted(by[s], key=lambda e: e['rec']['plate_w'])
        k = min(per_subset, len(v), max_n)
        if k <= 0:
            continue
        by[s] = [v[round(i * (len(v) - 1) / (k - 1))] for i in range(k)] if k > 1 else v[:1]
    while len(picks) < max_n:
        moved = False
        for s in order:
            if by[s] and len(picks) < max_n:
                picks.append(by[s].pop(0))
                moved = True
        if not moved:
            break
    return picks


def fetch_images(rf, picks, out_dir):
    """按中央目录里的 offset 排序, 把相邻的合并成一个大 Range 请求, 再逐条解压"""
    picks = sorted(picks, key=lambda e: e['info'].header_offset)
    os.makedirs(out_dir, exist_ok=True)
    got, group, groups = 0, [], []
    for e in picks:
        span = (e['info'].header_offset, e['info'].header_offset + 30 + len(e['info'].filename)
                + len(e['info'].extra) + e['info'].compress_size)
        if group and span[1] - group[0][0] > 4 << 20:
            groups.append(group)
            group = []
        group.append((span[0], span[1], e))
    if group:
        groups.append(group)
    print('      条目分 %d 组下载' % len(groups))
    for g in groups:
        lo = g[0][0]
        hi = max(s[1] for s in g)
        blob = rf.fetch(lo, hi - 1)
        for s0, s1, e in g:
            info = e['info']
            off = info.header_offset - lo
            nlen = int.from_bytes(blob[off + 26:off + 28], 'little')
            elen = int.from_bytes(blob[off + 28:off + 30], 'little')
            d0 = off + 30 + nlen + elen
            raw = blob[d0:d0 + info.compress_size]
            data = raw if info.compress_type == zipfile.ZIP_STORED else zlib.decompress(raw, -15)
            d = os.path.join(out_dir, e['rec']['subset'])
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, os.path.basename(info.filename)), 'wb') as f:
                f.write(data)
            got += 1
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max', type=int, default=300, help='总共导多少张')
    ap.add_argument('--per-subset', type=int, default=60, help='每个 subset 最多多少张')
    ap.add_argument('--np', type=int, default=40, help='再抽多少张"真实但无车牌"图(负样本)')
    ap.add_argument('--out', default=os.path.join(ROOT, '测试数据', 'ccpd'))
    ap.add_argument('--proxy', default='http://127.0.0.1:7890')
    ap.add_argument('--no-proxy', action='store_true', help='直连(系统代理已开时用这个)')
    ap.add_argument('--relabel', action='store_true',
                    help='不联网: 只按 --out 里已有的文件名重写 labels.csv(改了取框口径后用)')
    a = ap.parse_args()
    proxy = None if a.no_proxy else a.proxy

    if a.relabel:
        rows, bad = [], []
        for dirpath, _, fns in os.walk(a.out):
            for fn in sorted(fns):
                if not fn.lower().endswith('.jpg'):
                    continue
                rec = parse_entry(fn)
                if not rec:
                    bad.append(fn)
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), a.out)
                rows.append(label_row(rel, rec))
        with open(os.path.join(a.out, 'labels.csv'), 'w', encoding='utf-8', newline='') as f:
            wr = csv.writer(f)
            wr.writerow(LABEL_HEAD)
            wr.writerows(rows)
        print('--relabel: 重写 %d 行 -> %s (解析不了 %d 个文件名)'
              % (len(rows), os.path.join(a.out, 'labels.csv'), len(bad)))
        return

    print('[1/4] 读 zip 中央目录 ...')
    rf = HttpRangeFile(ZIP_URL, proxy)
    z = zipfile.ZipFile(io.BufferedReader(rf))
    names = z.namelist()
    print('      共 %d 个条目, 用了 %d 次 Range 请求' % (len(names), rf.requests))

    parsed = []
    for info in z.infolist():
        rec = parse_entry(info.filename)
        if rec:
            parsed.append({'info': info, 'rec': rec})
    bad = len(names) - len(parsed)
    sub = {}
    for e in parsed:
        sub[e['rec']['subset']] = sub.get(e['rec']['subset'], 0) + 1
    print('[2/4] 可解析 %d 张 (无车牌/解析不了 %d 张)' % (len(parsed), bad))
    print('      subset 分布: ' + ', '.join('%s=%d' % kv for kv in sorted(sub.items())))

    picks = stratified(parsed, a.max, a.per_subset)
    noplate = [e for e in parsed if e['rec'].get('noplate')]
    noplate = noplate[::max(1, len(noplate) // a.np)][:a.np] if noplate and a.np else []
    picks = picks + noplate
    print('[3/4] 选中 %d 张 (按 subset 轮转, 每个 subset 内按车牌宽等距) + %d 张无车牌负样本'
          % (len(picks) - len(noplate), len(noplate)))

    got = fetch_images(rf, picks, a.out)
    if got != len(picks):
        print('      ⚠ 期望 %d 张, 实际落盘 %d 张' % (len(picks), got))

    rows = []
    for e in sorted(picks, key=lambda e: os.path.join(e['rec']['subset'],
                                                      os.path.basename(e['info'].filename))):
        f = os.path.join(e['rec']['subset'], os.path.basename(e['info'].filename))
        rows.append(label_row(f, e['rec']))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'labels.csv'), 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(LABEL_HEAD)
        wr.writerows(rows)
    print('[4/4] 写出 %d 张, 共 %d 次 Range 请求 -> %s' % (len(rows), rf.requests, a.out))


if __name__ == '__main__':
    main()
