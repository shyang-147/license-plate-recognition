# -*- coding: utf-8 -*-
"""生成难集 matlab/hard/ (24 张) + labels.csv —— 比 bench/ 难一档的评测集

用法:  python tools/make_hard.py [输出目录]
依赖:  pip install pillow
输出:  <输出目录>/ne01~ne06.jpg 等 24 张 + labels.csv
       列: file,text,category,bx,by,bw,bh   (bx..bh 是车牌在成品图里的真值框)

bench/ 那 20 张都是"干净背景 + 小角度旋转"的图, 在上面调好的参数到真实照片上
并不成立(实测 20 张只错 9 张, 真实路拍 3 张全错)。这个难集把难点分开, 每类 6 张:

    newenergy  8 位新能源绿牌(省简称 + 字母 + 字母 + 5 位), 考察字符数自适应
    persp      7 位蓝牌 + 梯形畸变(上边明显比下边窄), 考察透视校正
    small      7 位蓝牌, 车牌在 1000x680 的画面里只有 120~170 px 宽, 考察定位下限
    night      7 位蓝牌, 亮度 x0.45 + 强噪声 + 强模糊

分类统计比一个总分有用: 总分掉下来时, 一眼能看出是哪一类场景拖后腿。

注意: 汉字字形取自系统字体(黑体/微软雅黑/宋体), 换机器时字体不同会得到略有差异的图。
      生成的图只用于本地评测, 已加进 .gitignore, 不进版本库。
"""
import os, random, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'matlab', 'hard')
os.makedirs(OUT, exist_ok=True)

FONT_PATH = next((p for p in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc',
                              r'C:\Windows\Fonts\simsun.ttc'] if os.path.exists(p)), None)
F = lambda s: ImageFont.truetype(FONT_PATH, s) if FONT_PATH else ImageFont.load_default()
PROV = '京津冀晋蒙辽吉黑沪苏浙皖闽赣鲁豫鄂湘粤桂琼渝川贵云藏陕甘青宁新'
LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
DIGITS = '0123456789'

def raw_glyph(ch, size=140):
    f = F(size)
    box = f.getbbox(ch)
    img = Image.new('L', (box[2]-box[0]+8, box[3]-box[1]+8), 0)
    ImageDraw.Draw(img).text((4-box[0], 4-box[1]), ch, font=f, fill=255)
    return img.crop(img.getbbox())

def make_plate(text, w, h, bg, fg=(250, 250, 250)):
    CHAR_H = int(h * 0.66)
    SLOT = int(w * 0.104 * 7 / len(text))
    GAP = int(SLOT * 0.26)
    total = len(text) * SLOT + (len(text)-1) * GAP
    x0 = (w - total) // 2
    top = (h - CHAR_H) // 2
    wmax = max(g.width * CHAR_H / g.height for g in (raw_glyph(c) for c in text))
    k = SLOT / wmax
    plate = Image.new('RGB', (w, h), bg)
    d = ImageDraw.Draw(plate)
    d.rectangle([1, 1, w-2, h-2], outline=fg, width=max(2, h//28))
    x = x0
    for ch in text:
        g = raw_glyph(ch)
        nw = max(1, round(g.width * CHAR_H / g.height * k))
        g = g.resize((nw, CHAR_H), Image.LANCZOS)
        plate.paste(fg, (x + (SLOT-nw)//2, top), g)
        x += SLOT + GAP
    return plate

def scene_bg(size=(1000, 680), seed=1, dim=1.0):
    random.seed(seed)
    base = random.randint(40, 80)
    bg = Image.new('RGB', size, (base, base+2, base+5))
    d = ImageDraw.Draw(bg)
    for y in range(size[1]):
        v = base + int(random.gauss(0, 3))
        d.line([(0, y), (size[0], y)], fill=(v, v+3, v+6))
    for _ in range(3000):
        X, Y = random.randrange(size[0]), random.randrange(size[1])
        g = random.randint(-20, 20)
        c = bg.getpixel((X, Y))
        bg.putpixel((X, Y), tuple(max(0, min(255, q+g)) for q in c))
    if dim != 1.0:
        bg = bg.point(lambda v: int(v * dim))
    return bg

def quad_blit(scene, plate, quad):
    """Keystone blit: map the source rectangle onto the destination quad (TL,TR,BR,BL)."""
    (tl, tr, br, bl) = quad
    sw, sh = plate.size
    px = plate.load()
    dst = scene.load()
    ys = [min(p[1] for p in quad), max(p[1] for p in quad)]
    for y in range(int(ys[0]), int(ys[1]) + 1):
        tL = (y - tl[1]) / float(bl[1] - tl[1])
        tR = (y - tr[1]) / float(br[1] - tr[1])
        if not (0 <= tL <= 1 and 0 <= tR <= 1):
            continue
        xL = tl[0] + tL * (bl[0] - tl[0])
        xR = tr[0] + tR * (br[0] - tr[0])
        v = int(min(sh - 1, max(0, tL * sh)))
        n = max(1, int(xR - xL))
        for i in range(n):
            u = int(min(sw - 1, (i / float(n)) * sw))
            x = int(xL) + i
            if 0 <= x < scene.size[0]:
                dst[x, y] = px[u, v]

def scene(text, angle, scale, blur, seed, warp=0.0, dim=1.0, size=(1000, 680), bgc=None):
    """返回 (成品图, GT 车牌框)。框是"车牌在成品图里的紧包围盒"(x y w h, 0 基)。"""
    random.seed(seed)
    bg = scene_bg(size, seed, dim)
    if bgc is None:
        bgc = (random.randint(10, 30), random.randint(55, 75), random.randint(140, 165))
    pw = int(440 * scale)
    ph = int(pw * 140 / 440)
    plate = make_plate(text, pw, ph, bgc)
    cx, cy = size[0] // 2, int(size[1] * 0.45)
    if warp > 0:
        x0, y0 = cx - pw / 2.0, cy - ph / 2.0
        inset = pw * warp
        quad = [(x0 + inset, y0), (x0 + pw - inset * 0.4, y0 + ph * 0.06),
                (x0 + pw, y0 + ph), (x0 - inset * 0.2, y0 + ph * 0.94)]
        quad_blit(bg, plate, quad)
        xs = [p[0] for p in quad]; ys = [p[1] for p in quad]
        box = (int(round(min(xs))), int(round(min(ys))),
               int(round(max(xs) - min(xs))), int(round(max(ys) - min(ys))))
    else:
        rot = plate.rotate(angle, resample=Image.BICUBIC, expand=True)
        mask = Image.new('L', plate.size, 255).rotate(angle, resample=Image.BICUBIC, expand=True)
        px, py = int(cx - rot.width / 2), int(cy - rot.height / 2)
        bg.paste(rot, (px, py), mask)
        bb = mask.getbbox()
        box = (px + bb[0], py + bb[1], bb[2] - bb[0], bb[3] - bb[1])
    img = bg.filter(ImageFilter.GaussianBlur(blur))
    if dim != 1.0:
        img = img.point(lambda v: int(min(255, v * dim)))
        rnd = random.Random(seed)
        px = img.load()
        for _ in range(9000):
            x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
            c = px[x, y]
            g = rnd.randint(-45, 45)
            px[x, y] = tuple(max(0, min(255, q + g)) for q in c)
    return img, box

def main(outDir=None):
    if outDir is None:
        outDir = sys.argv[1] if len(sys.argv) > 1 else OUT
    os.makedirs(outDir, exist_ok=True)
    random.seed(777)
    rows = []
    # 1) new energy, 8 chars
    for i in range(6):
        text = random.choice(PROV) + random.choice('ADF') + random.choice(LETTERS) + \
               ''.join(random.choice(DIGITS) for _ in range(5))
        name = 'ne%02d.jpg' % (i + 1)
        img, box = scene(text, random.uniform(-6, 6), random.uniform(0.75, 1.1), random.uniform(0.4, 1.0),
                         seed=200 + i, bgc=(random.randint(20, 45), random.randint(120, 160), random.randint(60, 90)))
        img.save(os.path.join(outDir, name), quality=88)
        rows.append('%s,%s,newenergy,%d,%d,%d,%d' % ((name, text) + box))
    # 2) perspective (keystone)
    for i in range(6):
        text = random.choice(PROV) + random.choice(LETTERS) + ''.join(random.choice(DIGITS) for _ in range(5))
        name = 'pe%02d.jpg' % (i + 1)
        img, box = scene(text, 0.0, random.uniform(0.9, 1.15), random.uniform(0.4, 0.9),
                         seed=300 + i, warp=random.uniform(0.10, 0.22))
        img.save(os.path.join(outDir, name), quality=88)
        rows.append('%s,%s,persp,%d,%d,%d,%d' % ((name, text) + box))
    # 3) small (120-170 px wide plate)
    for i in range(6):
        text = random.choice(PROV) + random.choice(LETTERS) + ''.join(random.choice(DIGITS) for _ in range(5))
        name = 'sm%02d.jpg' % (i + 1)
        scale = random.uniform(0.27, 0.39)     # 440*0.27 = 119 px
        img, box = scene(text, random.uniform(-5, 5), scale, random.uniform(0.3, 0.8), seed=400 + i)
        img.save(os.path.join(outDir, name), quality=88)
        rows.append('%s,%s,small,%d,%d,%d,%d' % ((name, text) + box))
    # 4) night-ish
    for i in range(6):
        text = random.choice(PROV) + random.choice(LETTERS) + ''.join(random.choice(DIGITS) for _ in range(5))
        name = 'ni%02d.jpg' % (i + 1)
        img, box = scene(text, random.uniform(-6, 6), random.uniform(0.8, 1.1), random.uniform(0.9, 1.5),
                         seed=500 + i, dim=0.45)
        img.save(os.path.join(outDir, name), quality=80)
        rows.append('%s,%s,night,%d,%d,%d,%d' % ((name, text) + box))
    with open(os.path.join(outDir, 'labels.csv'), 'w', encoding='utf-8') as f:
        f.write('file,text,category,bx,by,bw,bh\n' + '\n'.join(rows) + '\n')
    print('生成 %d 张难集图 -> %s' % (len(rows), outDir))

if __name__ == '__main__':
    main()
