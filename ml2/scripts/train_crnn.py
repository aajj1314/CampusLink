# -*- coding: utf-8 -*-
"""
ml2 训练脚本 —— 在「处理版图」上训练 CRNN + CTC。自包含，不 import ml/。

数据来自 ml2/dataset/（由 `build_dataset.py` 生成）：
    train/ + train_labels.csv      训练（含留出验证切分）
    test/  + test_labels.csv       独立测试集（本脚本不碰，交给 evaluate.py）

模型契约与 ml/ 保持一致（输入 1x32x200、时间步 T=50、13 类）——因为要与
「原图模型」做同口径竞争比较，架构变量应当固定；但实现是独立写的。

用法（仓库根目录，先 source .venv/bin/activate）：

    python ml2/scripts/build_dataset.py           # 先生成处理版数据集
    python ml2/scripts/train_crnn.py --epochs 300 --batch 32 --lr 3e-3 \
        --workers 4 --sched-patience 30 --patience 60

产物：ml2/models/crnn_proc.pt（最优）+ _last.pt + train_log.txt
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core as K  # noqa: E402

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
class ConvBlock(nn.Module):
    """Conv3x3(pad1) + BN + ReLU。"""

    def __init__(self, cin: int, cout: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(cin, cout, 3, padding=1, bias=False),
            nn.BatchNorm2d(cout),
            nn.ReLU(inplace=True),
        )

    def forward(self, x):
        return self.net(x)


class CRNN(nn.Module):
    """轻量 CRNN：CNN 把 [B,1,32,200] 压成 [B,512,1,50]，BiLSTM 出 [B,50,13]。

    宽度方向只降 4 倍（200→50）以留够 T=50 的时间步；高度方向降 32 倍到 1。
    """

    def __init__(self, num_classes: int):
        super().__init__()
        self.features = nn.Sequential(
            ConvBlock(1, 64), nn.MaxPool2d(2, 2),        # 16 x 100
            ConvBlock(64, 128), nn.MaxPool2d(2, 2),      #  8 x  50
            ConvBlock(128, 256), nn.MaxPool2d((2, 1)),   #  4 x  50
            ConvBlock(256, 256), nn.MaxPool2d((2, 1)),   #  2 x  50
            ConvBlock(256, 512), nn.MaxPool2d((2, 1)),   #  1 x  50
        )
        self.sequence = nn.LSTM(512, 256, num_layers=2, bidirectional=True,
                                batch_first=True, dropout=0.1)
        self.head = nn.Linear(512, num_classes)

    def forward(self, x):
        f = self.features(x).squeeze(2)          # [B,512,T]
        out, _ = self.sequence(f.permute(0, 2, 1))
        return self.head(out)                    # [B,T,num_classes]


# ================================================================== 数据增强
def augment(img: np.ndarray) -> np.ndarray:
    """
    img: [1,32,200]，值域 [-1,1]。针对本门户的干扰特性做增强：
    亮度/对比度抖动、平移、±4° 旋转、合成穿字斜线、高斯噪声、随机擦除。

    增强配方与 ml/ 的 `_augment` 等价（同样的算子与概率），实现独立重写，
    这样两条线的比较只差"是否用处理版图"这一个变量。
    """
    from PIL import Image

    # 亮度 / 对比度抖动
    if random.random() < 0.7:
        img = img + np.random.uniform(-0.15, 0.15)
    if random.random() < 0.5:
        img = img * np.random.uniform(0.8, 1.2)

    # 平移（背景填白）
    if random.random() < 0.6:
        dx, dy = random.randint(-3, 3), random.randint(-2, 2)
        h, w = img.shape[1], img.shape[2]
        out = np.full_like(img, 1.0)
        out[:, max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)] = \
            img[:, max(0, -dy):h + min(0, -dy), max(0, -dx):w + min(0, -dx)]
        img = out

    # 轻微旋转（±4°，大了会把 6/9 混淆）
    if random.random() < 0.5:
        u8 = ((np.clip(img[0], -1, 1) * 0.5 + 0.5) * 255).astype(np.uint8)
        rot = Image.fromarray(u8, "L").rotate(
            random.uniform(-4, 4), resample=Image.Resampling.BILINEAR, fillcolor=255)
        img = (np.asarray(rot, dtype=np.float32) / 255.0 - 0.5) / 0.5
        img = img[None]

    # 合成穿字细斜线（门户的核心干扰）
    for _ in range(random.randint(0, 2)):
        if random.random() < 0.7:
            y0 = random.uniform(4, K.IMG_H - 4)
            slope = np.tan(np.deg2rad(random.uniform(-35, 35)))
            xs = np.arange(K.IMG_W)
            ys = (y0 + slope * (xs - K.IMG_W / 2)).astype(int)
            m = (ys >= 0) & (ys < K.IMG_H)
            img[0, ys[m], xs[m]] = np.random.uniform(-1.0, -0.4)

    # 高斯噪声
    if random.random() < 0.5:
        img = img + np.random.normal(0, 0.06, img.shape).astype(np.float32)

    # 随机擦除小块（模拟小字干扰被当成笔画）
    if random.random() < 0.3:
        cx = random.randint(0, K.IMG_W - 6)
        cy = random.randint(0, K.IMG_H - 4)
        img[:, cy:cy + random.randint(2, 4), cx:cx + random.randint(3, 6)] = \
            np.random.uniform(-1, 0.2)

    return np.clip(img, -1, 1).astype(np.float32)


class CaptchaDataset(Dataset):
    """读 ml2/dataset 里的**处理版图**（不再做在线变换）。"""

    def __init__(self, samples: list[K.Sample], augment_on: bool = False):
        self.samples = samples
        self.augment_on = augment_on

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, i: int):
        s = self.samples[i]
        img = K.load_input(s.path)                       # [1,H,W]
        if self.augment_on:
            img = augment(img)
        return torch.from_numpy(img), torch.tensor(s.indices, dtype=torch.long)


def collate(batch):
    imgs = torch.stack([b[0] for b in batch])
    targets = torch.cat([b[1] for b in batch])
    lengths = torch.tensor([len(b[1]) for b in batch], dtype=torch.long)
    return imgs, targets, lengths


def edit_distance(a: list[int], b: list[int]) -> int:
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev = cur
    return dp[n]


@torch.no_grad()
def evaluate(model, loader, device) -> tuple[float, float]:
    """返回 (序列完全正确比例, 平均编辑距离)。"""
    model.eval()
    total = exact = 0
    dist = 0
    for imgs, targets, lengths in loader:
        preds = model(imgs.to(device)).permute(1, 0, 2).argmax(2)   # [T,B]
        off = 0
        for bi in range(imgs.size(0)):
            L = int(lengths[bi])
            tgt = [int(t) for t in targets[off:off + L]]
            off += L
            seq, prev = [], -1
            for k in preds[:, bi].tolist():
                if k != K.BLANK_IDX and k != prev:
                    seq.append(k)
                prev = k
            total += 1
            exact += int(seq == tgt)
            dist += edit_distance(seq, tgt)
    return (exact / total if total else 0.0, dist / total if total else 0.0)


# ================================================================== 主流程
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ml2：在处理版图上训练 CRNN+CTC")
    ap.add_argument("--data", default=str(K.DATA_DIR), help="处理版数据集目录")
    ap.add_argument("--out", default=str(K.ML2_ROOT / "models"), help="模型输出目录")
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--val-ratio", type=float, default=0.15)
    ap.add_argument("--patience", type=int, default=60, help="验证集不升多少轮后早停")
    ap.add_argument("--sched-patience", type=int, default=30,
                    help="ReduceLROnPlateau 耐心（CTC 前期有全 blank 平台期，不能太小）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=0, help="Windows 上建议 0")
    args = ap.parse_args(argv)

    data = Path(args.data)
    train_dir = data / "train"
    train_labels = data / "train_labels.csv"
    if not train_labels.exists() or not train_dir.is_dir():
        print(f"没找到处理版数据集：{data}\n先跑 python ml2/scripts/build_dataset.py",
              file=sys.stderr)
        return 2

    samples = K.build_samples(train_dir, train_labels)
    if len(samples) < 50:
        print(f"有效样本只有 {len(samples)} 条，至少 50 条才能训。", file=sys.stderr)
        return 2

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    random.shuffle(samples)
    n_val = max(1, int(len(samples) * args.val_ratio))
    val_samples, train_samples = samples[:n_val], samples[n_val:]

    print(f"数据目录 {data}")
    print(f"样本：训练 {len(train_samples)} / 验证 {len(val_samples)}")
    print(f"字符集 {K.CHARS}（{K.NUM_CLASSES} 类含 blank）；输入 {K.IMG_H}x{K.IMG_W}")

    train_loader = DataLoader(CaptchaDataset(train_samples, True), batch_size=args.batch,
                              shuffle=True, collate_fn=collate, num_workers=args.workers)
    val_loader = DataLoader(CaptchaDataset(val_samples, False), batch_size=args.batch,
                            shuffle=False, collate_fn=collate, num_workers=args.workers)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = CRNN(K.NUM_CLASSES).to(device)
    print(f"设备 {device}  参数量 {sum(p.numel() for p in model.parameters()) / 1e6:.2f}M")

    criterion = nn.CTCLoss(blank=K.BLANK_IDX, zero_infinity=True)
    optim = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optim, mode="max", factor=0.5, patience=args.sched_patience)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    best_path, last_path = out_dir / "crnn_proc.pt", out_dir / "crnn_proc_last.pt"
    log_path = out_dir / "train_log.txt"

    best, stale = -1.0, 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        t0, loss_sum, nb = time.time(), 0.0, 0
        for imgs, targets, tgt_lens in train_loader:
            imgs = imgs.to(device)
            logp = model(imgs).permute(1, 0, 2).log_softmax(2)
            in_lens = torch.full((imgs.size(0),), logp.size(0), dtype=torch.long)
            loss = criterion(logp, targets, in_lens, tgt_lens)
            optim.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optim.step()
            loss_sum += float(loss)
            nb += 1

        acc, dist = evaluate(model, val_loader, device)
        sched.step(acc)
        line = (f"epoch {epoch:3d}  loss {loss_sum / max(nb, 1):.4f}  "
                f"val_exact {acc:.1%}  val_edit {dist:.2f}  "
                f"lr {optim.param_groups[0]['lr']:.2e}  {time.time() - t0:.0f}s")
        print(line)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

        ckpt = {"model": model.state_dict(), "chars": K.CHARS,
                "img_h": K.IMG_H, "img_w": K.IMG_W, "acc": acc, "ml2": True}
        torch.save(ckpt, last_path)
        if acc > best:
            best, stale = acc, 0
            torch.save(ckpt, best_path)
            print(f"         ↳ 新的最优，已存 {best_path.name}")
        else:
            stale += 1
            if stale >= args.patience:
                print(f"验证集连续 {stale} 轮没提升，早停。")
                break

    print(f"\n完成。最优验证准确率 {best:.1%}  →  {best_path}")
    print(f"下一步：python ml2/scripts/evaluate.py --pt {best_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
