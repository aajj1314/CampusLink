# -*- coding: utf-8 -*-
"""
生成应用图标 app.ico / app.png。

用 Pillow 画，不依赖 Qt —— 打包脚本和 Inno Setup 都可能在没有 Qt 环境时
需要这个文件，所以不能借用 gui/style.py 里那套 QPainter 实现。

图案和运行时托盘图标保持一致：圆角渐变底 + Wi-Fi 弧 + 中心圆点。

用法:
    .venv\\Scripts\\python.exe tools\\make_icon.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "campuslink" / "gui" / "assets"

PRIMARY = (37, 99, 235)          # 与 style.PRIMARY 一致
PRIMARY_LIGHT = (96, 145, 245)
PRIMARY_DARK = (23, 66, 163)

SIZES = [16, 24, 32, 48, 64, 128, 256]


def draw(size: int) -> Image.Image:
    # 4 倍超采样再缩回来，边缘才不会有锯齿
    ss = 4
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 竖向渐变的圆角底：逐行填色，简单但够用
    radius = int(s * 0.22)
    grad = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / max(s - 1, 1)
        r = int(PRIMARY_LIGHT[0] + (PRIMARY_DARK[0] - PRIMARY_LIGHT[0]) * t)
        g = int(PRIMARY_LIGHT[1] + (PRIMARY_DARK[1] - PRIMARY_LIGHT[1]) * t)
        b = int(PRIMARY_LIGHT[2] + (PRIMARY_DARK[2] - PRIMARY_LIGHT[2]) * t)
        gd.line([(0, y), (s, y)], fill=(r, g, b, 255))

    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # Wi-Fi 三段弧
    cx, cy = s / 2, s * 0.72
    for rad, width in ((0.16, 0.055), (0.28, 0.065), (0.40, 0.075)):
        rr = s * rad
        box = [cx - rr, cy - rr, cx + rr, cy + rr]
        d.arc(box, start=210, end=330, fill=(255, 255, 255, 255),
              width=max(int(s * width), ss))

    # 中心圆点
    dot = s * 0.055
    d.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=(255, 255, 255, 255))

    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    frames = [draw(s) for s in SIZES]
    ico = OUT_DIR / "app.ico"
    frames[-1].save(ico, format="ICO",
                    sizes=[(s, s) for s in SIZES], append_images=frames[:-1])

    png = OUT_DIR / "app.png"
    frames[-1].save(png, format="PNG")

    print(f"  [OK] {ico}  ({ico.stat().st_size} 字节, {len(SIZES)} 种尺寸)")
    print(f"  [OK] {png}  ({png.stat().st_size} 字节, {frames[-1].size[0]}px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
