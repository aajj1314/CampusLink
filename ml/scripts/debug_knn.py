# -*- coding: utf-8 -*-
"""诊断：像素 k-NN 基线。验证集能否靠训练集最近邻认出？用完即删。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
import numpy as np
import random

root = Path(__file__).resolve().parent.parent
mapping = C.read_labels(root / "dataset/labels/labels.csv")
imgs = C.list_images(root / "dataset/raw")
samples = [C.Sample(p, mapping[p.name]) for p in imgs if p.name in mapping]
random.seed(42)
random.shuffle(samples)
val, train = samples[:221], samples[221:]

def feat(s):
    return C.load_image(s.path).ravel()

Xtr = np.stack([feat(s) for s in train])
ytr = [s.label for s in train]
print("训练特征:", Xtr.shape)

hits1 = hits3 = 0
for i, s in enumerate(val):
    q = feat(s)
    d = ((Xtr - q) ** 2).mean(axis=1)
    top = np.argsort(d)[:3]
    preds = [ytr[j] for j in top]
    hits1 += preds[0] == s.label
    hits3 += s.label in preds
    if i < 10:
        print(f" {s.path.name} 标注={s.label}  kNNTop1={preds[0]}  Top3={preds}")
print(f"\nk-NN Top1: {hits1}/{len(val)} = {hits1/len(val):.1%}   Top3: {hits3/len(val):.1%}")
