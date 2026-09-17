# -*- coding: utf-8 -*-
"""生成"小目标退化集" 测试数据/degrade/ —— 可复现的退化基准（第七轮 C 项）

用法:  python 项目代码/tools/make_degrade.py [输出目录]
依赖:  pip install pillow
输出:  <输出目录>/w220_1.jpg ... 共 10 档 x 6 张 = 60 张 + labels.csv
       labels.csv 列: file,text,category,kind,plate_w,bx,by,bw,bh

为什么单独做这个:
  real02 那类照片（车牌 93 px 宽、单个字符不到 10 px）在整个数据里只有一张，
  "小目标到底卡在哪一步"根本没有样本可谈。这个集把**车牌宽度**当成一个受控变量
  扫一遍，每档 6 张，就能看出**定位 / 分割 / 识别**各自在哪个宽度失守。

关键设计（为什么不是"直接按小尺寸渲染"）:
  直接用小字号渲染出来的车牌，边缘仍然是清晰的矢量边缘，比真实照片乐观得多。
  这里**按 440x140 的原生尺寸渲染，再降采样到目标宽度** —— 降采样倍数就是
  "离得多远"，这才是远处车牌在传感器上的真实样子。之后再加一个**固定像素量级**
  的高斯模糊（镜头 PSF 在像素上是常数，不随距离变）和 JPEG 压缩。

退化档位: 220 / 180 / 150 / 125 / 105 / 90 / 75 / 60 / 48 / 38 px 宽
  （90 那一档 ≈ real02 的实际宽度；38 那一档单字只有 4 px）
每档 4 张 7 位蓝牌 + 2 张 8 位绿牌（新能源），画面固定 1000x680。
每档用的是**同一批 6 块车牌**（只降采样倍数不同），场景背景/车牌底色/倾角各自随机 ——
这样档与档之间的差就只剩"宽度"这一项，不会混进"换了一批牌"的噪声。
随机种子固定，同一台机器上可复现；汉字字形取系统字体，换机器会略有差异。
"""
import os
import random
import sys

from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from make_hard import make_plate, scene_bg, PROV, LETTERS, DIGITS   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))          # 归档根 车牌识别项目/
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, '测试数据', 'degrade')

FRAME = (1000, 680)
LEVELS = [220, 180, 150, 125, 105, 90, 75, 60, 48, 38]
N_BLUE, N_GREEN = 4, 2
BLUR = 0.8                    # 固定像素量级的镜头/压缩软化（降采样已经提供了分辨率损失）
QUALITY = 85


def rand_text(nch):
    if nch == 7:
        return random.choice(PROV) + random.choice(LETTERS) + ''.join(random.choice(DIGITS) for _ in range(5))
    return (random.choice(PROV) + random.choice('ADF') + random.choice(LETTERS)
            + ''.join(random.choice(DIGITS) for _ in range(5)))


def build(text, target_w, seed, kind):
    """返回 (图, GT 框)。先把车牌按原生 440x140 画出来, 再降采样到 target_w。"""
    random.seed(seed)
    bg = scene_bg(FRAME, seed)
    bgc = ((random.randint(10, 30), random.randint(55, 75), random.randint(140, 165)) if kind == 'blue7'
           else (random.randint(20, 45), random.randint(120, 160), random.randint(60, 90)))
    plate = make_plate(text, 440, 140, bgc)                       # 原生分辨率
    plate = plate.resize((target_w, max(12, round(140 * target_w / 440.0))), Image.LANCZOS)
    angle = random.uniform(-4, 4)
    rot = plate.rotate(angle, resample=Image.BICUBIC, expand=True)
    mask = Image.new('L', plate.size, 255).rotate(angle, resample=Image.BICUBIC, expand=True)
    cx = FRAME[0] // 2 + random.randint(-40, 40)
    cy = int(FRAME[1] * 0.45) + random.randint(-30, 30)
    px, py = int(cx - rot.width / 2), int(cy - rot.height / 2)
    bg.paste(rot, (px, py), mask)
    bb = mask.getbbox()                                            # 旋转后不透明区域的紧包围盒
    box = (px + bb[0], py + bb[1], bb[2] - bb[0], bb[3] - bb[1])
    img = bg.filter(ImageFilter.GaussianBlur(BLUR))
    return img, box


def main():
    random.seed(7_1017)
    os.makedirs(OUT, exist_ok=True)
    plates = []
    for i in range(N_BLUE + N_GREEN):
        nch = 8 if i >= N_BLUE else 7
        plates.append((rand_text(nch), 'green8' if nch == 8 else 'blue7'))
    rows, k = [], 0
    for w in LEVELS:
        for i, (text, kind) in enumerate(plates):
            k += 1
            name = 'w%03d_%d.jpg' % (w, i + 1)
            img, box = build(text, w, seed=90000 + w * 10 + i, kind=kind)
            img.save(os.path.join(OUT, name), quality=QUALITY)
            rows.append('%s,%s,w%d,%s,%d,%d,%d,%d,%d' % ((name, text, w, kind, w) + box))
    with open(os.path.join(OUT, 'labels.csv'), 'w', encoding='utf-8') as f:
        f.write('file,text,category,kind,plate_w,bx,by,bw,bh\n' + '\n'.join(rows) + '\n')
    print('生成 %d 张退化图（%d 档 x %d 张）-> %s' % (len(rows), len(LEVELS), N_BLUE + N_GREEN, OUT))


if __name__ == '__main__':
    main()
