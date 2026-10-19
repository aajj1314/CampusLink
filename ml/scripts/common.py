# -*- coding: utf-8 -*-
"""
CampusLink 算式验证码识别 —— 训练/推理共用的定义。

设计对齐 AntiCAP 的 CRNN 路线（`[AntiCAP]-CRNN_Math.onnx` 输出 T=50、19 类），
但**字符集按本门户重做**：该门户的运算符是**汉字** `加`/`乘`（`÷`/`-` 也有），
AntiCAP 的 `0123456789+-*/÷×=?` 里没有汉字，直接套用会把 `乘` 读成 `×`、
把干扰字读成数字，答案必错（实测 10 张全错）。

本文件不 import torch —— 标注工具只要 numpy/PIL 就能跑。
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# ------------------------------------------------------------------ 字符集
#
# 只用 14 个符号：10 个数字 + 4 个汉字运算符。
#
# 为什么统一成汉字：验证码里 `加`/`乘` 是**画在图上**的原始字形，而 `÷`/`-`
# 是符号。训练时把符号形式归一成汉字，模型只需学 4 类运算符；
# 解析阶段再由 captcha_math 换回 `+ - * /`。
#
# 为什么不把 `=` `?` 放进字符集：尾部 `=?` 是固定标记、对求值毫无贡献，
# 但它是干扰 OCR 的最大元凶（`8加4=?` → '8加42'）。让模型只负责
# `A <op> B`，尾部交给 CTC 当空白忽略。
CHARS = "0123456789加减乘除"          # 12 个
NUM_CLASSES = len(CHARS) + 1          # +1 是 CTC blank
BLANK_IDX = 0
CHAR_TO_IDX = {c: i + 1 for i, c in enumerate(CHARS)}
IDX_TO_CHAR = {i + 1: c for i, c in enumerate(CHARS)}

#: 输入尺寸（高, 宽）。真实图是 200x60，这里统一压到 32x200 ——
#: 高度压到 32 是为了让 CNN 做 5 次 2x1 池化后正好落到 1，
#: 宽度保持 200 是为了让 T=50 有足够时间步容纳 `A <op> B`（最长 4 字符）。
IMG_H = 32
IMG_W = 200

#: 运算符写法归一。训练标签与推理输出都用归一后的形式。
_OP_NORMALIZE = [
    ("＋", "加"), ("+", "加"),
    ("－", "减"), ("−", "减"), ("—", "减"), ("-", "减"),
    ("＊", "乘"), ("*", "乘"), ("×", "乘"), ("x", "乘"), ("X", "乘"),
    ("／", "除"), ("/", "除"), ("÷", "除"),
]


def normalize_text(s: str) -> str:
    """
    把任意写法的算式归一成 `A<汉字运算符>B`。

    训练标签与推理输出走同一个函数，保证编码一致。
    尾部 `=?`、空白、以及不在字符集里的字符一律丢掉。
    """
    if not s:
        return ""
    for src, dst in _OP_NORMALIZE:
        s = s.replace(src, dst)
    return "".join(ch for ch in s if ch in CHAR_TO_IDX)


def encode(text: str) -> list[int]:
    """文本 → 索引序列（CTC 标签，不含 blank）。"""
    return [CHAR_TO_IDX[c] for c in normalize_text(text)]


def ctc_greedy_decode(logits: np.ndarray) -> list[str]:
    """
    CTC 贪心解码：去重复、去 blank。

    logits 形状 [T, C] 或 [B, T, C]。
    """
    if logits.ndim == 3:
        logits = logits[0]
    idx = np.argmax(logits, axis=1)
    out: list[str] = []
    prev = -1
    for k in idx:
        k = int(k)
        if k != BLANK_IDX and k != prev:
            out.append(IDX_TO_CHAR.get(k, ""))
        prev = k
    return ["".join(out)]


# ------------------------------------------------------------------ 预处理
def load_image(path: str | os.PathLike) -> np.ndarray:
    """
    读图 → 灰度 → 缩放到 IMG_H x IMG_W → 归一化到 [-1, 1]。

    为什么先转灰度：验证码的主字符颜色是**随机**的（深青/深蓝/深绿/深紫），
    彩色通道只会把"同一个字的不同颜色"变成不同样本，在小数据集上
    白白扩大方差。形状才是判别依据。
    """
    img = Image.open(path).convert("L")
    return preprocess_pil(img)


def preprocess_pil(img: Image.Image) -> np.ndarray:
    """PIL 图 → 模型输入张量 [1, IMG_H, IMG_W]，值域 [-1, 1]。"""
    img = img.convert("L").resize((IMG_W, IMG_H), Image.Resampling.BILINEAR)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = (arr - 0.5) / 0.5
    return arr[None, :, :]


def preprocess_bytes(data: bytes) -> np.ndarray | None:
    """从内存里的图片字节做同样的预处理（推理时用）。"""
    import io
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None
    return preprocess_pil(img)


# ------------------------------------------------------------------ 数据集清单
LABEL_FILE = "labels.csv"     # 两列：filename,label


@dataclass(frozen=True)
class Sample:
    path: Path
    label: str                # 已归一化的 `A<op>B`

    @property
    def indices(self) -> list[int]:
        return encode(self.label)


def read_labels(labels_csv: str | os.PathLike) -> dict[str, str]:
    """
    读标注表 → {文件名: 归一化标签}。

    容忍三种写法（方便手工编辑）：
        cap_0001.png,7加9
        cap_0001.png,7+9        # 符号形式会被归一成汉字
        cap_0001.png,7加9=?     # 尾部 =? 会被丢掉
    空标签 / 解析不出算式的行会被跳过（当作未标注）。
    """
    out: dict[str, str] = {}
    p = Path(labels_csv)
    if not p.exists():
        return out
    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.reader(fh):
            if not row or len(row) < 2:
                continue
            name = row[0].strip()
            if not name or name.lower() in ("filename", "file", "文件名"):
                continue
            label = normalize_text(row[1])
            # 至少要有「数字 + 运算符 + 数字」才是有效标签
            if len(label) >= 3 and re.search(r"\d[加减乘除]\d", label):
                out[name] = label
    return out


def write_labels(labels_csv: str | os.PathLike, mapping: dict[str, str]) -> None:
    """整表重写（标注工具用）。utf-8-sig 让 Excel 打开不乱码。"""
    p = Path(labels_csv)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["filename", "label"])
        for name in sorted(mapping):
            w.writerow([name, mapping[name]])
    tmp.replace(p)


def list_images(raw_dir: str | os.PathLike) -> list[Path]:
    d = Path(raw_dir)
    if not d.is_dir():
        return []
    exts = {".png", ".jpg", ".jpeg", ".bmp"}
    return sorted(p for p in d.iterdir() if p.suffix.lower() in exts)
