# -*- coding: utf-8 -*-
"""
在「处理版」验证码图上训练 CRNN + CTC —— ml2 专用。

与 `ml/scripts/train_crnn.py` 的**唯一区别**：数据加载时多一步固定的
「对比度 ×k + 降亮 ×b」处理（`prep2.load_processed`）。
模型结构（CRNN）、数据增强（`_augment`）、CTC 解码、训练循环口径全部与 ml/ 一致，
这样两边的结果才可比。

**这里训出来的模型，只能配合同一套预处理使用**：
训练吃什么、推理就必须吃什么（用 `evaluate2.py` 评估即走同一预处理）。
拿它去识别未处理的**原图**，等价于分布不一致，会掉点。

用法（在仓库根目录，先 `source .venv/bin/activate`）：

    # 默认即「对比度 ×4.0 + 降亮 0.7」
    python ml2/scripts/train_crnn2.py --epochs 300 --batch 32 --lr 3e-3 \
        --workers 4 --sched-patience 30 --patience 60

    # 换别的处理档（例如 ×2.5 不降亮）
    python ml2/scripts/train_crnn2.py --contrast 2.5 --brightness 1.0

产物：`ml2/models/crnn_math2.pt`（最优）+ `..._last.pt`（每轮）+ `train_log.txt`。
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np

_THIS = Path(__file__).resolve()
_ML2 = _THIS.parents[1]                       # ml2/
_ML_SCRIPTS = _THIS.parents[2] / "ml" / "scripts"
_REPO = _THIS.parents[2]                      # CampusLink/
for _p in (str(_THIS.parent), str(_ML_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import common as C          # noqa: E402  字符集 / 标签 / CTC / 归一化（复用，不改）
import prep2 as P           # noqa: E402  处理版图预处理（train/inference 共用）
from train_crnn import CRNN, _augment, _edit_distance, collate  # noqa: E402  结构口径与 ml/ 完全一致

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, Dataset
except ImportError:  # pragma: no cover
    print("缺少 PyTorch。安装 CPU 版：\n"
          "  pip install torch --index-url https://download.pytorch.org/whl/cpu",
          file=sys.stderr)
    raise SystemExit(1)


class ProcessedCaptchaDataset(Dataset):
    """与 ml/ 的 CaptchaDataset 同构，只把 load_image 换成 load_processed。"""

    def __init__(self, samples: list[C.Sample], contrast: float, brightness: float,
                 augment: bool = False):
        self.samples = samples
        self.contrast = contrast
        self.brightness = brightness
        self.augment = augment

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, i: int):
        s = self.samples[i]
        img = P.load_processed(s.path, self.contrast, self.brightness)  # [1, IMG_H, IMG_W]
        if self.augment:
            img = _augment(img)
        return torch.from_numpy(img), torch.tensor(s.indices, dtype=torch.long)


@torch.no_grad()
def evaluate(model, loader, device) -> tuple[float, float]:
    """返回 (序列完全正确的比例, 平均编辑距离)。口径与 ml/ 一致。"""
    model.eval()
    total = exact = 0
    dist_sum = 0
    for imgs, targets, lengths in loader:
        imgs = imgs.to(device)
        logits = model(imgs).permute(1, 0, 2)
        preds = logits.argmax(2)
        offset = 0
        for bi in range(imgs.size(0)):
            L = int(lengths[bi])
            tgt = [int(t) for t in targets[offset:offset + L]]
            offset += L
            out, prev = [], -1
            for k in preds[:, bi].tolist():
                if k != C.BLANK_IDX and k != prev:
                    out.append(k)
                prev = k
            total += 1
            if out == tgt:
                exact += 1
            dist_sum += _edit_distance(out, tgt)
    return (exact / total if total else 0.0, dist_sum / total if total else 0.0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="在「处理版」验证码图上训练 CRNN（ml2）")
    ap.add_argument("--raw", default=str(_REPO / "ml" / "dataset" / "raw"),
                    help="验证码原图目录（默认复用 ml/dataset/raw）")
    ap.add_argument("--labels", default=str(_REPO / "ml" / "dataset" / "labels" / C.LABEL_FILE))
    ap.add_argument("--out", default=str(_ML2 / "models"), help="模型输出目录")
    ap.add_argument("--contrast", type=float, default=P.DEFAULT_CONTRAST,
                    help=f"对比度倍数，默认 {P.DEFAULT_CONTRAST}")
    ap.add_argument("--brightness", type=float, default=P.DEFAULT_BRIGHTNESS,
                    help=f"亮度倍数，默认 {P.DEFAULT_BRIGHTNESS}")
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--val-ratio", type=float, default=0.15)
    ap.add_argument("--patience", type=int, default=60, help="验证集不升多少轮后早停")
    ap.add_argument("--sched-patience", type=int, default=30,
                    help="ReduceLROnPlateau 的耐心轮数（CTC 前期有全 blank 平台期，不能太小）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=0, help="Windows 上建议 0")
    args = ap.parse_args(argv)

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    raw_dir = Path(args.raw)
    mapping = C.read_labels(args.labels)
    all_imgs = C.list_images(raw_dir)
    if not all_imgs:
        print(f"没找到图片：{raw_dir}", file=sys.stderr)
        return 2
    samples = [C.Sample(p, mapping[p.name]) for p in all_imgs if p.name in mapping]
    if len(samples) < 50:
        print(f"有效标注只有 {len(samples)} 条，至少 50 条才能训。", file=sys.stderr)
        return 2

    random.shuffle(samples)
    n_val = max(1, int(len(samples) * args.val_ratio))
    val_samples, train_samples = samples[:n_val], samples[n_val:]

    print(f"样本：训练 {len(train_samples)} / 验证 {len(val_samples)}")
    print(f"字符集 {C.CHARS}（{C.NUM_CLASSES} 类含 blank）")
    print(f"预处理：对比度 ×{args.contrast} → 亮度 ×{args.brightness}（train 与 inference 同此）")

    train_loader = DataLoader(
        ProcessedCaptchaDataset(train_samples, args.contrast, args.brightness, augment=True),
        batch_size=args.batch, shuffle=True, collate_fn=collate, num_workers=args.workers)
    val_loader = DataLoader(
        ProcessedCaptchaDataset(val_samples, args.contrast, args.brightness, augment=False),
        batch_size=args.batch, shuffle=False, collate_fn=collate, num_workers=args.workers)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = CRNN(C.NUM_CLASSES).to(device)
    print(f"设备：{device}  参数量：{sum(p.numel() for p in model.parameters()) / 1e6:.2f}M")

    criterion = nn.CTCLoss(blank=C.BLANK_IDX, zero_infinity=True)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=0.5, patience=args.sched_patience)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    best_path = out_dir / "crnn_math2.pt"
    last_path = out_dir / "crnn_math2_last.pt"
    log_path = out_dir / "train_log.txt"

    best_acc, stale = -1.0, 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        t0 = time.time()
        loss_sum, nb = 0.0, 0
        for imgs, targets, tgt_lens in train_loader:
            imgs = imgs.to(device)
            log_probs = model(imgs).permute(1, 0, 2).log_softmax(2)
            in_lens = torch.full((imgs.size(0),), log_probs.size(0), dtype=torch.long)
            loss = criterion(log_probs, targets, in_lens, tgt_lens)
            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            loss_sum += float(loss)
            nb += 1

        acc, dist = evaluate(model, val_loader, device)
        scheduler.step(acc)
        line = (f"epoch {epoch:3d}  loss {loss_sum / max(nb, 1):.4f}  "
                f"val_exact {acc:.1%}  val_edit {dist:.2f}  "
                f"lr {optimizer.param_groups[0]['lr']:.2e}  {time.time() - t0:.0f}s")
        print(line)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

        ckpt = {"model": model.state_dict(), "chars": C.CHARS,
                "img_h": C.IMG_H, "img_w": C.IMG_W, "acc": acc,
                "contrast": args.contrast, "brightness": args.brightness, "ml2": True}
        torch.save(ckpt, last_path)
        if acc > best_acc:
            best_acc, stale = acc, 0
            torch.save(ckpt, best_path)
            print(f"         ↳ 新的最优，已存 {best_path.name}")
        else:
            stale += 1
            if stale >= args.patience:
                print(f"验证集连续 {stale} 轮没提升，早停。")
                break

    print(f"\n完成。最优验证准确率 {best_acc:.1%}  →  {best_path}")
    print(f"下一步：python ml2/scripts/evaluate2.py --pt {best_path} "
          f"--contrast {args.contrast} --brightness {args.brightness}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
