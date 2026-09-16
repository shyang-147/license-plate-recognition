"""生成工程自带演示图 images/demo_plate*.jpg (3 张)

用法:  python tools/make_demo.py
依赖:  pip install pillow
输出:  matlab/images/demo_plate.jpg   (京A12345, -6.0 度)
       matlab/images/demo_plate2.jpg  (沪B8K9Z2, +3.5 度)
       matlab/images/demo_plate3.jpg  (粤B1234A, +1.0 度)
"""
import os
import random

from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'matlab', 'images')
os.makedirs(OUT, exist_ok=True)

FONT_PATH = next((p for p in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc',
                              r'C:\Windows\Fonts\simsun.ttc'] if os.path.exists(p)), None)
F = lambda s: ImageFont.truetype(FONT_PATH, s) if FONT_PATH else ImageFont.load_default()

W, H = 440, 140          # 国标蓝牌比例
CHAR_H, SLOT_W, GAP, MARGIN = 92, 46, 12, 23
TEXT_TOP = (H - CHAR_H) // 2


def raw_glyph(ch, size=140):
    f = F(size)
    box = f.getbbox(ch)
    img = Image.new('L', (box[2] - box[0] + 8, box[3] - box[1] + 8), 0)
    ImageDraw.Draw(img).text((4 - box[0], 4 - box[1]), ch, font=f, fill=255)
    return img.crop(img.getbbox())


def make_plate(text):
    # 车牌用的是"压缩体": 所有字按同一个横向压缩系数 k 处理, 窄字(如 1)依然窄
    wmax = max(g.width * CHAR_H / g.height for g in (raw_glyph(c) for c in text))
    k = SLOT_W / wmax

    plate = Image.new('RGB', (W, H), (16, 62, 152))
    d = ImageDraw.Draw(plate)
    d.rectangle([1, 1, W - 2, H - 2], outline=(248, 248, 248), width=5)

    x = MARGIN
    for ch in text:
        g = raw_glyph(ch)
        nw = max(1, round(g.width * CHAR_H / g.height * k))
        g = g.resize((nw, CHAR_H), Image.LANCZOS)
        plate.paste((252, 252, 252), (x + (SLOT_W - nw) // 2, TEXT_TOP), g)
        x += SLOT_W + GAP
    return plate


def scene(text, angle, size=(1000, 680), seed=1, blur=0.7):
    random.seed(seed)
    bg = Image.new('RGB', size, (80, 82, 86))
    d = ImageDraw.Draw(bg)
    for y in range(size[1]):
        v = 55 + int(65 * y / size[1])
        d.line([(0, y), (size[0], y)], fill=(v, v + 3, v + 7))
    for _ in range(2000):
        X, Y = random.randrange(size[0]), random.randrange(size[1])
        c = bg.getpixel((X, Y))
        g = random.randint(-16, 16)
        bg.putpixel((X, Y), tuple(max(0, min(255, q + g)) for q in c))
    plate = make_plate(text)
    rot = plate.rotate(angle, resample=Image.BICUBIC, expand=True)
    mask = Image.new('L', plate.size, 255).rotate(angle, resample=Image.BICUBIC, expand=True)
    bg.paste(rot, ((size[0] - rot.width) // 2, int(size[1] * 0.40)), mask)
    return bg.filter(ImageFilter.GaussianBlur(blur))


def main():
    for name, text, ang, seed in [('demo_plate.jpg', '京A12345', -6.0, 3),
                                  ('demo_plate2.jpg', '沪B8K9Z2', 3.5, 7),
                                  ('demo_plate3.jpg', '粤B1234A', 1.0, 11)]:
        p = os.path.join(OUT, name)
        scene(text, ang, seed=seed).save(p, quality=88)
        print('saved', p)


if __name__ == '__main__':
    main()