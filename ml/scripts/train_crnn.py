# -*- coding: utf-8 -*-
"""
训练算式验证码识别模型（CRNN + CTC）。

架构对齐 AntiCAP 的 `[AntiCAP]-CRNN_Math.onnx`（同尺寸输入、同 T=50 时间步），
但字符集换成本门户的 `0123456789加减乘除` —— 该门户的运算符是**汉字**，
AntiCAP 那个只有符号的模型在这儿实测 10 张全错。

用法：
    # 先在 ml/ 目录下准备好 dataset/raw/*.png 和 dataset/labels/labels.csv
    python ml/scripts/train_crnn.py --epochs 60 --batch 32

产物：ml/models/crnn_math.pt（最优权重）+ ml/models/crnn_math_last.pt
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, Dataset
except ImportError:  # pragma: no cover
    print("缺少 PyTorch。安装 CPU 版：\n"
          "  pip install torch --index-url https://download.pytorch.org/whl/cpu",
          file=sys.stderr)
    raise SystemExit(1)


# ================================================================== 模型
class CRNN(nn.Module):
    """
    经典 CRNN（Shi et al. 2015）的轻量版。

    输入 [B, 1, 32, 200]  →  CNN  →  [B, T=50, 512]  →  BiLSTM  →  [B, T, num_classes]

    池化安排：W 方向只降 4 倍（200→50），保证 T=50 的时间步足够容纳
    `A<op>B` 最长 4 个字符（CTC 要求 T >= 2L+1 = 9）；
    H 方向降 32 倍降到 1，把每列压成一个特征向量。
    """

    def __init__(self, num_classes: int):
        super().__init__()
        self.cnn = nn.Sequential(
            nn.Conv2d(1, 64, 3, 1, 1), nn.BatchNorm2d(64), nn.ReLU(True),
            nn.MaxPool2d(2, 2),                       # 16 x 100
            nn.Conv2d(64, 128, 3, 1, 1), nn.BatchNorm2d(128), nn.ReLU(True),
            nn.MaxPool2d(2, 2),                       #  8 x  50
            nn.Conv2d(128, 256, 3, 1, 1), nn.BatchNorm2d(256), nn.ReLU(True),
            nn.MaxPool2d((2, 1)),                     #  4 x  50
            nn.Conv2d(256, 256, 3, 1, 1), nn.BatchNorm2d(256), nn.ReLU(True),
            nn.MaxPool2d((2, 1)),                     #  2 x  50
            nn.Conv2d(256, 512, 3, 1, 1), nn.BatchNorm2d(512), nn.ReLU(True),
            nn.MaxPool2d((2, 1)),                     #  1 x  50
        )
        self.rnn = nn.LSTM(512, 256, num_layers=2,
                           bidirectional=True, batch_first=True, dropout=0.1)
        self.fc = nn.Linear(512, num_classes)

    def forward(self, x):
        feat = self.cnn(x)                  # [B, 512, 1, T]
        feat = feat.squeeze(2)              # [B, 512, T]
        feat = feat.permute(0, 2, 1)        # [B, T, 512]
        out, _ = self.rnn(feat)             # [B, T, 512]
        return self.fc(out)                 # [B, T, num_classes]


# ================================================================== 数据
def _augment(img: np.ndarray) -> np.ndarray:
    """
    针对本门户干扰特性的增强（输入 [1,32,200]，值域 [-1,1]）：
    - 亮度/对比度抖动：模拟颜色随机
    - 平移/旋转：模拟字符压边与倾斜
    - 合成穿字细斜线：门户的核心干扰就是深色细斜线
    - 高斯噪声 / 随机擦除：模拟细笔画小字干扰
    """
    from PIL import Image as _Img
    # 亮度 / 对比度
    if random.random() < 0.7:
        img = img + np.random.uniform(-0.15, 0.15)
    if random.random() < 0.5:
        img = img * np.random.uniform(0.8, 1.2)
    # 平移（背景填白）
    if random.random() < 0.6:
        dx = random.randint(-3, 3)
        dy = random.randint(-2, 2)
        out = np.full_like(img, 1.0)
        h, w = img.shape[1], img.shape[2]
        out[:, max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)] = \
            img[:, max(0, -dy):h + min(0, -dy), max(0, -dx):w + min(0, -dx)]
        img = out
    # 轻微旋转（±4°，大了会把 6/9 混淆）
    if random.random() < 0.5:
        ang = random.uniform(-4, 4)
        u8 = (np.clip(img[0], -1, 1) * 0.5 + 0.5) * 255
        pil = _Img.fromarray(u8.astype(np.uint8), "L").rotate(
            ang, resample=_Img.BILINEAR, fillcolor=255)
        img = (np.asarray(pil, dtype=np.float32) / 255.0 - 0.5) / 0.5
        img = img[None, :, :]
    # 合成穿字细斜线（0~2 条，深色）
    for _ in range(random.randint(0, 2)):
        if random.random() < 0.7:
            y0 = random.uniform(4, C.IMG_H - 4)
            slope = np.tan(np.deg2rad(random.uniform(-35, 35)))
            xs = np.arange(C.IMG_W)
            ys = (y0 + slope * (xs - C.IMG_W / 2)).astype(int)
            m = (ys >= 0) & (ys < C.IMG_H)
            img[0, ys[m], xs[m]] = np.random.uniform(-1.0, -0.4)
    # 高斯噪声
    if random.random() < 0.5:
        img = img + np.random.normal(0, 0.06, img.shape).astype(np.float32)
    # 随机擦除小块（模拟小字干扰被当成笔画的情形）
    if random.random() < 0.3:
        cx = random.randint(0, C.IMG_W - 6)
        cy = random.randint(0, C.IMG_H - 4)
        img[:, cy:cy + random.randint(2, 4), cx:cx + random.randint(3, 6)] = \
            np.random.uniform(-1, 0.2)
    return np.clip(img, -1, 1).astype(np.float32)


class CaptchaDataset(Dataset):
    def __init__(self, samples: list[C.Sample], augment: bool = False):
        self.samples = samples
        self.augment = augment

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, i: int):
        s = self.samples[i]
        img = C.load_image(s.path)          # [1, 32, 200]
        if self.augment:
            img = _augment(img)
        return torch.from_numpy(img), torch.tensor(s.indices, dtype=torch.long)


def collate(batch):
    """CTC 需要把变长标签拼成一维 + 给出各自长度。"""
    imgs = torch.stack([b[0] for b in batch])
    targets = torch.cat([b[1] for b in batch])
    lengths = torch.tensor([len(b[1]) for b in batch], dtype=torch.long)
    return imgs, targets, lengths


@torch.no_grad()
def evaluate(model, loader, device) -> tuple[float, float]:
    """返回 (序列完全正确的比例, 平均编辑距离)。"""
    model.eval()
    total = exact = 0
    dist_sum = 0
    for imgs, targets, lengths in loader:
        imgs = imgs.to(device)
        logits = model(imgs).permute(1, 0, 2)          # [T, B, C]
        preds = logits.argmax(2)
        # 逐样本解码
        offset = 0
        for bi in range(imgs.size(0)):
            L = int(lengths[bi])
            gold = C.IDX_TO_CHAR  # 便于阅读
            tgt = [int(t) for t in targets[offset:offset + L]]
            offset += L
            seq = preds[:, bi].tolist()
            out, prev = [], -1
            for k in seq:
                if k != C.BLANK_IDX and k != prev:
                    out.append(k)
                prev = k
            total += 1
            if out == tgt:
                exact += 1
            dist_sum += _edit_distance(out, tgt)
    if total == 0:
        return 0.0, 0.0
    return exact / total, dist_sum / total


def _edit_distance(a: list[int], b: list[int]) -> int:
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev = cur
    return dp[n]


# ================================================================== 主流程
def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="训练算式验证码 CRNN 模型")
    ap.add_argument("--raw", default=str(root / "dataset" / "raw"),
                    help="验证码图片目录")
    ap.add_argument("--labels", default=str(root / "dataset" / "labels" / C.LABEL_FILE),
                    help="标注 CSV")
    ap.add_argument("--out", default=str(root / "models"), help="模型输出目录")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--val-ratio", type=float, default=0.15)
    ap.add_argument("--patience", type=int, default=12, help="验证集不升多少轮后早停")
    ap.add_argument("--sched-patience", type=int, default=4,
                    help="ReduceLROnPlateau 的耐心轮数。CTC 前期有全 blank 平台期，"
                         "应设得比平台期长（建议 10~15），否则 lr 被提前砍残")
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
        print(f"没找到图片：{raw_dir}\n先跑 tools/captcha_sampler.py 采样", file=sys.stderr)
        return 2

    samples = [C.Sample(p, mapping[p.name]) for p in all_imgs if p.name in mapping]
    if len(samples) < 50:
        print(f"有效标注只有 {len(samples)} 条（共 {len(all_imgs)} 张图）。"
              f"至少 50 条才能训；建议 500+。\n"
              f"用 ml/scripts/label_tool.py 标注，或先跑 autolabel.py 预标。",
              file=sys.stderr)
        return 2

    random.shuffle(samples)
    n_val = max(1, int(len(samples) * args.val_ratio))
    val_samples, train_samples = samples[:n_val], samples[n_val:]

    print(f"样本：训练 {len(train_samples)} / 验证 {len(val_samples)}")
    print(f"字符集 {C.CHARS}（{C.NUM_CLASSES} 类含 blank）")

    train_loader = DataLoader(CaptchaDataset(train_samples, augment=True),
                              batch_size=args.batch, shuffle=True,
                              collate_fn=collate, num_workers=args.workers)
    val_loader = DataLoader(CaptchaDataset(val_samples, augment=False),
                            batch_size=args.batch, shuffle=False,
                            collate_fn=collate, num_workers=args.workers)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = CRNN(C.NUM_CLASSES).to(device)
    print(f"设备：{device}  参数量：{sum(p.numel() for p in model.parameters()) / 1e6:.2f}M")

    criterion = nn.CTCLoss(blank=C.BLANK_IDX, zero_infinity=True)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=0.5, patience=args.sched_patience)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    best_path = out_dir / "crnn_math.pt"
    last_path = out_dir / "crnn_math_last.pt"

    best_acc = -1.0
    stale = 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        t0 = time.time()
        loss_sum = 0.0
        nb = 0
        for imgs, targets, tgt_lens in train_loader:
            imgs = imgs.to(device)
            logits = model(imgs)                       # [B, T, C]
            log_probs = logits.permute(1, 0, 2).log_softmax(2)
            T = log_probs.size(0)
            in_lens = torch.full((imgs.size(0),), T, dtype=torch.long)
            loss = criterion(log_probs, targets, in_lens, tgt_lens)
            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            loss_sum += float(loss)
            nb += 1

        acc, dist = evaluate(model, val_loader, device)
        scheduler.step(acc)
        print(f"epoch {epoch:3d}  loss {loss_sum / max(nb,1):.4f}  "
              f"val_exact {acc:.1%}  val_edit {dist:.2f}  "
              f"lr {optimizer.param_groups[0]['lr']:.2e}  {time.time()-t0:.0f}s")

        torch.save({"model": model.state_dict(), "chars": C.CHARS,
                    "img_h": C.IMG_H, "img_w": C.IMG_W, "acc": acc}, last_path)

        if acc > best_acc:
            best_acc = acc
            stale = 0
            torch.save({"model": model.state_dict(), "chars": C.CHARS,
                        "img_h": C.IMG_H, "img_w": C.IMG_W, "acc": acc}, best_path)
            print(f"         ↳ 新的最优，已存 {best_path.name}")
        else:
            stale += 1
            if stale >= args.patience:
                print(f"验证集连续 {stale} 轮没提升，早停。")
                break

    print(f"\n完成。最优验证准确率 {best_acc:.1%}  →  {best_path}")
    print("下一步：python ml/scripts/export_onnx.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
