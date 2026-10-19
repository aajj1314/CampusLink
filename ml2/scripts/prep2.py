# -*- coding: utf-8 -*-
"""
ml2 专用预处理：先对验证码图做一次**固定的**「对比度增强 + 整体降亮」，
再交给 ml/scripts/common.preprocess_pil 归一成模型输入。

## 为什么单独搞一个 ml2

`ml/` 的模型是在**原图**上训练的（推理也直接吃原图）。实验观察到：
把图做强对比度处理后，**人眼**更清晰，但**喂给在原图上训练的模型会掉点**
（见 `ml/dataset/发现-对比度增强预处理.md` §6：k=4.0 时答案正确率 92.2% → 62.1%）。
根因是**训练-推理分布不一致**。

ml2 验证另一条路：**训练与推理都固定用同一张处理版图**，把分布重新对齐，
看能否追平/超过原图基线（原图基线：独立测试集 92.2% 正确 / 6.9% 错答）。

## 铁律：train 与 inference 必须共用本文件的处理

只要训练和推理用的变换不一致，分布就会再次错开、实验就失去意义。
所以 `train_crnn2.py` 与 `evaluate2.py` 都 import 这里的 `process()` / `load_processed()`，
不要各写一份。

## 变换定义（与肉眼复核用的 demo 完全一致）

用「发现文档 §2」的**固定锚点 128** 公式，逐像素、RGB 通道独立：

```
对比度：v' = clip((v - 128) * k + 128, 0, 255)      # 先 clip
降亮：  v'' = clip(v' * b, 0, 255)                   # 再 clip
默认   k = 4.0, b = 0.7
```

> 与 demo 一致：这两步**各夹一次值域**，所以 ×4.0 时被压到纯白的背景
> 再乘 0.7 会落成灰底（≈179），而不是被"乘回来"——这正是当初 demo 里
> 看到的"整体降亮"效果。
>
> ⚠️ 与 `ml/scripts/contrast_ab.py` 的实现有细微出入：那边用
> `PIL.ImageEnhance.Contrast`，其拉伸锚点是**每张图自己的均值**（本类图约 200+）、
> 不是固定 128。k 较大时两者差别明显（锚点 128 把 v<96 / v>191 夹死，
> 锚点 200 则夹的区间整体上移）。本文件实现的是**发现文档 §2 的公式**，
> 也即 demo 里你看到的那一版；与 contrast_ab 的逐 k 数字不是严格同一变换，
> 对照时请留意。

> 处理是**在线**做的（在 Dataset 里现算），不预生成图片文件。
> 这样 train/inference 天然共用同一段代码；也避免把增强图写进 `raw/`
> （`raw/` 是只读数据集，HANDOFF 要求不许改动）。
> 想肉眼复核实际输入，可用本文件 CLI 把处理版图落盘。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# 复用 ml/ 里的字符集 / 标签 / CTC 编解码 / 归一化，但**不改** common.py。
_ML_SCRIPTS = Path(__file__).resolve().parents[2] / "ml" / "scripts"
if str(_ML_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_ML_SCRIPTS))
import common as C  # noqa: E402

#: 本轮实验参数（「对比度 ×4.0 后整体降亮」）
DEFAULT_CONTRAST = 4.0
DEFAULT_BRIGHTNESS = 0.7


def process(img: Image.Image,
            contrast: float = DEFAULT_CONTRAST,
            brightness: float = DEFAULT_BRIGHTNESS) -> Image.Image:
    """固定锚点 128 的对比度拉伸 + 整体降亮，返回处理后的 PIL(RGB) 图。

    train 与 inference 必须调用同一个函数、同一组参数。
    """
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    if contrast != 1.0:
        arr = np.clip((arr - 128.0) * contrast + 128.0, 0.0, 255.0)
    if brightness != 1.0:
        arr = np.clip(arr * brightness, 0.0, 255.0)
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def load_processed(path: str | Path,
                   contrast: float = DEFAULT_CONTRAST,
                   brightness: float = DEFAULT_BRIGHTNESS) -> np.ndarray:
    """读图 → 处理 → common.preprocess_pil，返回 [1, IMG_H, IMG_W] 的 [-1,1] 张量。"""
    with Image.open(path) as im:
        proc = process(im, contrast, brightness)
    return C.preprocess_pil(proc)


# ------------------------------------------------------------------ CLI：把处理版图落盘供肉眼复核
def _dump_one(src: Path, out_dir: Path, contrast: float, brightness: float) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        proc = process(im, contrast, brightness)
    dst = out_dir / src.name
    proc.save(dst)
    return dst


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="把「处理版」图落盘，供肉眼复核训练/推理实际吃到的输入长什么样。"
                    "注意：训练时是在线处理，不需要预生成这些文件。")
    ap.add_argument("--src", required=True, help="单张图片或目录")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--contrast", type=float, default=DEFAULT_CONTRAST)
    ap.add_argument("--brightness", type=float, default=DEFAULT_BRIGHTNESS)
    args = ap.parse_args(argv)

    src = Path(args.src)
    out_dir = Path(args.out)
    files = [src] if src.is_file() else sorted(
        p for p in src.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"})
    if not files:
        print(f"没有图片：{src}", file=sys.stderr)
        return 2
    for p in files:
        print(_dump_one(p, out_dir, args.contrast, args.brightness))
    print(f"\n处理参数：对比度 ×{args.contrast}，亮度 ×{args.brightness}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
