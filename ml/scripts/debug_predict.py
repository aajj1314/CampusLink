# -*- coding: utf-8 -*-
"""诊断：模型在验证集/训练集上的预测对比。用完即删。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
import torch
from train_crnn import CRNN, CaptchaDataset

root = Path(__file__).resolve().parent.parent
mapping = C.read_labels(root / "dataset/labels/labels.csv")
imgs = C.list_images(root / "dataset/raw")
samples = [C.Sample(p, mapping[p.name]) for p in imgs if p.name in mapping]

import random
random.seed(42)
random.shuffle(samples)
val, train = samples[:221], samples[221:]

ckpt = torch.load(root / "models/crnn_math.pt", map_location="cpu")
model = CRNN(C.NUM_CLASSES)
model.load_state_dict(ckpt["model"])
model.eval()

def decode(ds, n, tag):
    print(f"===== {tag} =====")
    ok = 0
    for i in range(n):
        s = ds[i]
        x = torch.from_numpy(C.load_image(s.path))[None]
        with torch.no_grad():
            pr = model(x).permute(1, 0, 2).argmax(2)[:, 0].tolist()
        out, prev = [], -1
        for k in pr:
            if k != 0 and k != prev: out.append(C.IDX_TO_CHAR.get(k, '?'))
            prev = k
        pred = "".join(out)
        mark = "OK " if pred == s.label else "BAD"
        ok += pred == s.label
        print(f" {mark} {s.path.name}  标注={s.label}  预测={pred}")
    print(f" 前{n}张命中 {ok}")

decode(val, 15, "验证集")
decode(train, 8, "训练集")
