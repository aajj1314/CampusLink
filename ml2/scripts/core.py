# -*- coding: utf-8 -*-
"""
ml2 核心定义 —— 自包含，**不 import ml/ 的任何代码**。

ml2 是一条**独立的**模型线：训练/推理都只吃「处理版图」（对比度增强 + 整体降亮后
落盘的图片），与 ml/ 的原图模型是竞争关系。

刻意只共用两样东西：
  1. 字符集契约 `CHARS = "0123456789加减乘除"` —— 必须与 ml/ 一致，
     否则复用的 `labels.csv` 会整体错位。
  2. `campuslink.core.captcha_math.solve` —— 判「答案对不对」的口径。
     这是客户端模块（不是 ml/ 的训练代码），用它才能和原图基线的口径对齐。

除这两点外，本文件及其兄弟脚本全部自己实现：字符集表、CTC 编解码、图像预处理、
处理版变换、标签读写、模型、训练循环、评估。
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# ------------------------------------------------------------------ 路径
_CORE = Path(__file__).resolve()
SCRIPTS_DIR = _CORE.parent                 # ml2/scripts
ML2_ROOT = _CORE.parents[1]                # ml2/
REPO_ROOT = _CORE.parents[2]               # CampusLink/

#: ml2 自己的数据集目录（处理版图 + 复用的标注），由 build_dataset.py 生成
DATA_DIR = ML2_ROOT / "dataset"
TRAIN_DIR = DATA_DIR / "train"
TEST_DIR = DATA_DIR / "test"
TRAIN_LABELS = DATA_DIR / "train_labels.csv"
TEST_LABELS = DATA_DIR / "test_labels.csv"

#: 默认数据来源（ml2 只读它们、不改动）
SRC_RAW = REPO_ROOT / "ml" / "dataset" / "raw"
SRC_LABELS = REPO_ROOT / "ml" / "dataset" / "labels" / "labels.csv"
SRC_TEST_RAW = REPO_ROOT / "ml" / "dataset" / "test_raw"
SRC_TEST_LABELS = REPO_ROOT / "ml" / "dataset" / "test_labels.csv"

# ------------------------------------------------------------------ 字符集
#: 必须与 ml/scripts/common.py 的 CHARS 完全一致（复用标注的前提）。
CHARS = "0123456789加减乘除"          # 12 个
NUM_CLASSES = len(CHARS) + 1          # +1 = CTC blank
BLANK_IDX = 0
CHAR_TO_IDX = {c: i + 1 for i, c in enumerate(CHARS)}
IDX_TO_CHAR = {i + 1: c for i, c in enumerate(CHARS)}

#: 输入尺寸（高, 宽）。原图 200x60 压到 32x200。
IMG_H = 32
IMG_W = 200

# ------------------------------------------------------------------ 处理版变换
#: 「对比度 ×4.0 后整体降亮」—— 处理版图的定义参数。
DEFAULT_CONTRAST = 4.0
DEFAULT_BRIGHTNESS = 0.7

#: 运算符别名 → 汉字（训练标签/模型输出都用汉字形式）
_OP_TO_CN = [
    ("＋", "加"), ("+", "加"),
    ("－", "减"), ("−", "减"), ("—", "减"), ("-", "减"),
    ("＊", "乘"), ("*", "乘"), ("×", "乘"), ("x", "乘"), ("X", "乘"),
    ("／", "除"), ("/", "除"), ("÷", "除"),
]

_LABEL_RE = re.compile(r"\d[加减乘除]\d")
_IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}


# ------------------------------------------------------------------ 文本编解码
def normalize_text(s: str) -> str:
    """任意写法 → `A<汉字运算符>B`；丢弃 `=?`、空白与不在字符集里的字符。"""
    if not s:
        return ""
    for src, dst in _OP_TO_CN:
        s = s.replace(src, dst)
    return "".join(ch for ch in s if ch in CHAR_TO_IDX)


def encode(label: str) -> list[int]:
    """标签 → CTC 目标索引序列（不含 blank）。"""
    return [CHAR_TO_IDX[c] for c in normalize_text(label)]


def ctc_greedy_decode(logits: np.ndarray) -> list[str]:
    """CTC 贪心解码：去重复、去 blank。logits 形状 [T,C] 或 [B,T,C]。"""
    if logits.ndim == 3:
        logits = logits[0]
    best = np.argmax(logits, axis=1)
    out, prev = [], -1
    for k in map(int, best):
        if k != BLANK_IDX and k != prev:
            out.append(IDX_TO_CHAR.get(k, ""))
        prev = k
    return ["".join(out)]


# ------------------------------------------------------------------ 图像：处理变换 + 归一化
def process_image(img: Image.Image,
                  contrast: float = DEFAULT_CONTRAST,
                  brightness: float = DEFAULT_BRIGHTNESS) -> Image.Image:
    """固定锚点 128 的对比度拉伸 + 整体降亮（这就是「处理版图」的定义）。

        v'  = clip((v - 128) * contrast + 128, 0, 255)     # 先夹一次
        v'' = clip(v' * brightness, 0, 255)                # 再夹一次

    两步各夹一次值域，所以 ×4.0 被压成纯白的背景再 ×0.7 会落成灰底（≈179）。
    """
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    if contrast != 1.0:
        arr = np.clip((arr - 128.0) * contrast + 128.0, 0.0, 255.0)
    if brightness != 1.0:
        arr = np.clip(arr * brightness, 0.0, 255.0)
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def preprocess_pil(img: Image.Image) -> np.ndarray:
    """PIL 图 → 灰度 → 缩放到 IMG_H x IMG_W → 归一化 [-1,1]，返回 [1,H,W]。"""
    img = img.convert("L").resize((IMG_W, IMG_H), Image.Resampling.BILINEAR)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    return ((arr - 0.5) / 0.5)[None, :, :]


def load_input(path: str | os.PathLike) -> np.ndarray:
    """读**处理版图**文件 → 模型输入 [1, IMG_H, IMG_W]。"""
    with Image.open(path) as im:
        return preprocess_pil(im)


# ------------------------------------------------------------------ 标签与清单
@dataclass(frozen=True)
class Sample:
    path: Path
    label: str                      # 已归一化 `A<汉字op>B`

    @property
    def indices(self) -> list[int]:
        return encode(self.label)


def read_labels(csv_path: str | os.PathLike) -> dict[str, str]:
    """读两列 CSV（filename,label）→ {文件名: 归一化标签}。容忍符号写法与尾部 `=?`。"""
    out: dict[str, str] = {}
    p = Path(csv_path)
    if not p.exists():
        return out
    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.reader(fh):
            if len(row) < 2:
                continue
            name = row[0].strip()
            if not name or name.lower() in ("filename", "file", "文件名"):
                continue
            label = normalize_text(row[1])
            if len(label) >= 3 and _LABEL_RE.search(label):
                out[name] = label
    return out


def write_labels(csv_path: str | os.PathLike, mapping: dict[str, str]) -> None:
    """整表写出（UTF-8 无 BOM、LF），排序保证可复现。"""
    p = Path(csv_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["filename", "label"])
        for name in sorted(mapping):
            w.writerow([name, mapping[name]])


def list_images(directory: str | os.PathLike) -> list[Path]:
    d = Path(directory)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix.lower() in _IMG_EXTS)


def build_samples(images_dir: str | os.PathLike,
                  labels_csv: str | os.PathLike) -> list[Sample]:
    """目录里的图片 ∩ 标签表 → Sample 列表（只保留有标注的）。"""
    mapping = read_labels(labels_csv)
    return [Sample(p, mapping[p.name]) for p in list_images(images_dir) if p.name in mapping]
