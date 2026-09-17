# -*- coding: utf-8 -*-
"""生成"真实照片降采样退化集" 测试数据/degrade_real/（第七轮 C 项）

用法:  python 项目代码/tools/make_degrade_real.py [输出目录]
依赖:  pip install pillow
输入:  测试数据/real2/labels.csv   (file,text,bx,by,bw,bh —— 真值车牌框)
输出:  <输出目录>/<原图名>_s<百分比>.jpg + labels.csv
       labels.csv 列: file,text,category,plate_w,bx,by,bw,bh

和 tools/make_degrade.py 的合成退化集是**互补**的两件事:
  合成集: 画面尺寸固定, 只有车牌被降采样 —— 回答"车牌宽到多少像素时, 哪一步开始崩",
          变量干净、可复现, 但背景是假的;
  本脚本: 把**整张真实照片**按比例缩小 —— 回答"同一套结论在真实图像上成不成立",
          有真实的字体/光照/压缩, 但每档只有一个尺寸。

做法: 整图 LANCZOS 降采样到 s 倍, 真值框同步乘 s。
      这是"同一张照片用更低分辨率传感器拍"的模型, 也是本轮唯一能自动做出来的
      "真实小目标"样本来源 —— 真拍一张更远的照片不是脚本能干的。
注意: 降采样只能模拟分辨率损失, 模拟不了真实远拍的运动模糊/曝光变化,
      所以它是"必要不充分"的证据, 结论里要写清楚。
"""
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))                  # 项目代码/tools
ROOT = os.path.dirname(os.path.dirname(HERE))                      # 车牌识别项目/
SRC = os.path.join(ROOT, '测试数据', 'real2')
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, '测试数据', 'degrade_real')

SCALES = [1.0, 0.80, 0.65, 0.50, 0.40, 0.32]
QUALITY = 88


def read_labels(path):
    with open(path, encoding='utf-8') as f:
        lines = [ln.rstrip('\n') for ln in f if ln.strip()]
    head = lines[0].split(',')
    rows = []
    for ln in lines[1:]:
        parts = ln.split(',', len(head) - 1)
        rows.append(dict(zip(head, parts)))
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = read_labels(os.path.join(SRC, 'labels.csv'))
    out_rows, k = [], 0
    for r in rows:
        if not all(k in r and r[k].strip() for k in ('bx', 'by', 'bw', 'bh')):
            print('跳过（没有真值框）: %s' % r['file'])
            continue
        x, y, w, h = (int(r['bx']), int(r['by']), int(r['bw']), int(r['bh']))
        img = Image.open(os.path.join(SRC, r['file']))
        for s in SCALES:
            tw = max(1, int(round(img.width * s)))
            th = max(1, int(round(img.height * s)))
            small = img.resize((tw, th), Image.LANCZOS)
            stem = os.path.splitext(r['file'])[0]
            name = '%s_s%03d.jpg' % (stem, int(round(s * 100)))
            small.save(os.path.join(OUT, name), quality=QUALITY)
            bw = max(1, int(round(w * s)))
            box = (int(round(x * s)), int(round(y * s)), bw, max(1, int(round(h * s))))
            # category 用**源照片名**: 分组统计要看的是"三张源照片各自的曲线",
            # 档位已经写在文件名里, 再按档位分组就成了每张一组(没有信息量)。
            out_rows.append('%s,%s,%s,%d,%d,%d,%d,%d'
                            % ((name, r['text'], stem, box[2]) + box))
            k += 1
    with open(os.path.join(OUT, 'labels.csv'), 'w', encoding='utf-8') as f:
        f.write('file,text,category,plate_w,bx,by,bw,bh\n' + '\n'.join(out_rows) + '\n')
    print('生成 %d 张真实降采样图（%d 张原图 x %d 档）-> %s' % (k, len(rows), len(SCALES), OUT))


if __name__ == '__main__':
    main()
