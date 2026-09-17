"""生成基准测试图 bench/ (20 张) + labels.csv

用法:  python tools/make_bench.py
依赖:  pip install pillow
输出:  matlab/bench/bench01.jpg ~ bench20.jpg, matlab/bench/labels.csv

每张图随机: 省份/字母/数字、倾斜 -9~9 度、车牌尺寸 0.6~1.15 倍、随机模糊与噪声,
底色 80% 蓝牌 / 10% 绿牌 / 10% 黄牌。随机种子固定, 同一台机器上可复现。
注意: 汉字字形取自系统字体(黑体/微软雅黑/宋体), 换机器时字体不同会得到略有差异的图。
"""
import os
import random

from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'matlab', 'bench')
os.makedirs(OUT, exist_ok=True)

FONT_PATH = next((p for p in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc',
                              r'C:\Windows\Fonts\simsun.ttc'] if os.path.exists(p)), None)
F = lambda s: ImageFont.truetype(FONT_PATH, s) if FONT_PATH else ImageFont.load_default()

PROV = '京津冀晋蒙辽吉黑沪苏浙皖闽赣鲁豫鄂湘粤桂琼渝川贵云藏陕甘青宁新'
LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ'      # 车牌不用 I O
DIGITS_L = '0123456789'


def raw_glyph(ch, size=140):
    f = F(size)
    box = f.getbbox(ch)
    img = Image.new('L', (box[2] - box[0] + 8, box[3] - box[1] + 8), 0)
    ImageDraw.Draw(img).text((4 - box[0], 4 - box[1]), ch, font=f, fill=255)
    return img.crop(img.getbbox())


def make_plate(text, w, h, bg):
    CHAR_H = int(h * 0.66)
    SLOT = int(w * 0.104)
    GAP = int(w * 0.027)
    total = 7 * SLOT + 6 * GAP
    x0 = (w - total) // 2
    top = (h - CHAR_H) // 2

    wmax = max(g.width * CHAR_H / g.height for g in (raw_glyph(c) for c in text))
    k = SLOT / wmax

    plate = Image.new('RGB', (w, h), bg)
    d = ImageDraw.Draw(plate)
    d.rectangle([1, 1, w - 2, h - 2], outline=(246, 246, 246), width=max(2, h // 28))
    x = x0
    for ch in text:
        g = raw_glyph(ch)
        nw = max(1, round(g.width * CHAR_H / g.height * k))
        g = g.resize((nw, CHAR_H), Image.LANCZOS)
        plate.paste((250, 250, 250), (x + (SLOT - nw) // 2, top), g)
        x += SLOT + GAP
    return plate


def scene(text, angle, scale, blur, seed):
    """返回 (成品图, GT 车牌框); 框 = 车牌在成品图里的紧包围盒 (x y w h, 0 基)。"""
    random.seed(seed)
    size = (1000, 680)
    base = random.randint(40, 80)
    bg = Image.new('RGB', size, (base, base + 2, base + 5))
    d = ImageDraw.Draw(bg)
    for y in range(size[1]):
        v = base + int(random.gauss(0, 3))
        d.line([(0, y), (size[0], y)], fill=(v, v + 3, v + 6))
    for _ in range(3000):
        X, Y = random.randrange(size[0]), random.randrange(size[1])
        g = random.randint(-20, 20)
        c = bg.getpixel((X, Y))
        bg.putpixel((X, Y), tuple(max(0, min(255, q + g)) for q in c))
    kind = random.random()
    if kind < 0.8:
        bgc = (random.randint(10, 30), random.randint(55, 75), random.randint(140, 165))
    elif kind < 0.9:
        bgc = (random.randint(20, 45), random.randint(120, 160), random.randint(60, 90))
    else:
        bgc = (random.randint(210, 240), random.randint(170, 200), random.randint(20, 45))

    pw = int(440 * scale)
    ph = int(pw * 140 / 440)
    plate = make_plate(text, pw, ph, bgc)
    rot = plate.rotate(angle, resample=Image.BICUBIC, expand=True)
    mask = Image.new('L', plate.size, 255).rotate(angle, resample=Image.BICUBIC, expand=True)
    px, py = (size[0] - rot.width) // 2, int(size[1] * 0.42)
    bg.paste(rot, (px, py), mask)
    bb = mask.getbbox()
    box = (px + bb[0], py + bb[1], bb[2] - bb[0], bb[3] - bb[1])
    return bg.filter(ImageFilter.GaussianBlur(blur)), box


def main():
    rows = []
    random.seed(2026)
    for i in range(20):
        text = random.choice(PROV) + random.choice(LETTERS) + ''.join(random.choice(DIGITS_L) for _ in range(5))
        angle = random.uniform(-9, 9)
        scale = random.uniform(0.6, 1.15)
        blur = random.uniform(0.4, 1.2)
        name = 'bench%02d.jpg' % (i + 1)
        img, box = scene(text, angle, scale, blur, seed=i + 100)
        img.save(os.path.join(OUT, name), quality=86)
        rows.append('%s,%s,%.1f,%.2f,%.1f,%d,%d,%d,%d' % ((name, text, angle, scale, blur) + box))

    with open(os.path.join(OUT, 'labels.csv'), 'w', encoding='utf-8') as f:
        f.write('file,text,angle,scale,blur,bx,by,bw,bh\n' + '\n'.join(rows) + '\n')
    print('生成 20 张测试图 ->', OUT)


if __name__ == '__main__':
    main()
