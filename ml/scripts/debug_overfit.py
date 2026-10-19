# -*- coding: utf-8 -*-
"""诊断脚本：64 张小样本过拟合测试。用完即删。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from train_crnn import CRNN, CaptchaDataset, collate

root = Path(__file__).resolve().parent.parent
mapping = C.read_labels(root / "dataset/labels/labels.csv")
imgs = C.list_images(root / "dataset/raw")
samples = [C.Sample(p, mapping[p.name]) for p in imgs if p.name in mapping][:64]
print("样本数:", len(samples))
for s in samples[:5]:
    print(" ", s.path.name, s.label, s.indices)

loader = DataLoader(CaptchaDataset(samples, augment=False), batch_size=16,
                    shuffle=True, collate_fn=collate)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("设备:", device)
model = CRNN(C.NUM_CLASSES).to(device)
crit = nn.CTCLoss(blank=C.BLANK_IDX, zero_infinity=True)
opt = torch.optim.Adam(model.parameters(), lr=3e-3)

for ep in range(1, 301):
    model.train()
    tot = 0.0
    for imgs_b, tgt, lens in loader:
        imgs_b = imgs_b.to(device)
        logits = model(imgs_b).permute(1, 0, 2).log_softmax(2)
        T = logits.size(0)
        in_lens = torch.full((imgs_b.size(0),), T, dtype=torch.long)
        loss = crit(logits, tgt, in_lens, lens)
        opt.zero_grad(); loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        opt.step()
        tot += float(loss)
    if ep % 10 == 0 or ep == 1:
        # 看预测
        model.eval()
        with torch.no_grad():
            it = iter(loader)
            bi, tl, ll = next(it)
            pr = model(bi.to(device)).permute(1, 0, 2).argmax(2)
        print(f"ep {ep:3d} loss {tot/len(loader):.4f}  预测: {pr[:,0].tolist()[:12]}  期望: {tl[:ll[0]].tolist()}")

# 全量自测
model.eval()
exact = 0
with torch.no_grad():
    for bi, tl, ll in loader:
        pr = model(bi.to(device)).permute(1, 0, 2).argmax(2)
        off = 0
        for k in range(bi.size(0)):
            L = int(ll[k]); tgt = tl[off:off+L].tolist(); off += L
            seq, prev, out = pr[:, k].tolist(), -1, []
            for x in seq:
                if x != 0 and x != prev: out.append(x)
                prev = x
            exact += (out == tgt)
print(f"64 张自拟合 exact: {exact}/64")
