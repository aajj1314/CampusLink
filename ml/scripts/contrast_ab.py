# -*- coding: utf-8 -*-
"""
对比度增强预处理 A/B 对照实验 —— 把「发现-对比度增强预处理.md」从假设变成数据。

## 实验设计（对应发现文档 §6 的待验证项）

固定同一模型、同一独立测试集（`test_raw/` 116 张，从未参与训练），只变预处理：

    组 A   k=1.0            基线（等价于现有识别链路：原图直接进模型）
    组 B   k=1.5            轻度增强
    组 C   k=2.5            文档推荐的折中值
    组 D   k=4.0            强增强（文档提醒浅色笔画可能被削平）
    组 E   k=2.5 灰度域     先转灰度再拉伸（对照组：排除彩色饱和度变化的影响）

增强实现与发现文档的 ColorMatrix 公式**逐像素等价**：
    out = clip((in - 128) * k + 128, 0, 255)
即 PIL 的 `ImageEnhance.Contrast(img).enhance(k)`（文档 §3 也给了这个等价写法）。

## 怎么读结果

对每组报三个数（口径与 `evaluate.py` 一致）：

    正确     最终答案对（能提交的）
    错答     会提交一个**错误**答案 —— 最危险的指标
    安全失败 求不出答案 → 换图重试（无害）

判读标准：一个 k 值值得采用，当且仅当「错答率下降」且「正确率不降」。
只提升正确率但错答率也涨的，**不能用**（本项目原则：宁可换图，不能错答）。

## 用法

    python ml/scripts/contrast_ab.py                      # 默认测试集
    python ml/scripts/contrast_ab.py --onnx 别的.onnx     # 换模型对照
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
from campuslink.core.captcha_math import solve  # noqa: E402

OK, WRONG, SAFE = "ok", "wrong", "safe"


def _verdict(gold: str, pred: str) -> str:
    ga, pa = solve(gold), solve(pred)
    if pred == gold or (pa is not None and pa == ga):
        return OK
    return SAFE if pa is None else WRONG


def preprocess(img: "Image.Image", k: float, gray_domain: bool = False) -> np.ndarray:
    """
    验证码图 → 模型输入 [1,1,32,200]。

    k > 1 时先做对比度拉伸（RGB 域，与发现文档等价；gray_domain=True 则在
    灰度域拉伸），再走训练侧统一的 preprocess_pil（缩放 + [-1,1] 归一）。
    k = 1.0 时不增强，与 `common.load_image` 的路径完全一致。
    """
    if k != 1.0:
        from PIL import ImageEnhance
        if gray_domain:
            img = img.convert("L")
        img = ImageEnhance.Contrast(img).enhance(k)
    return C.preprocess_pil(img)


def run_group(sess, in_name, images, mapping, k: float, gray_domain: bool = False):
    from PIL import Image as _Img
    rows = []
    for p in images:
        img = _Img.open(p)
        x = preprocess(img, k, gray_domain)[None, :, :, :].astype(np.float32)
        logits = sess.run(None, {in_name: x})[0]
        pred = C.ctc_greedy_decode(logits)[0]
        gold = mapping[p.name]
        rows.append({"name": p.name, "gold": gold, "pred": pred,
                     "verdict": _verdict(gold, pred)})
    return rows


def _stat(rows):
    n = len(rows)
    ok = sum(r["verdict"] == OK for r in rows)
    wrong = sum(r["verdict"] == WRONG for r in rows)
    safe = sum(r["verdict"] == SAFE for r in rows)
    return ok, wrong, safe, n


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="对比度增强 A/B 对照")
    ap.add_argument("--onnx", default=str(root / "models" / "crnn_math.onnx"))
    ap.add_argument("--raw", default=str(root / "dataset" / "test_raw"))
    ap.add_argument("--labels", default=str(root / "dataset" / "test_labels.csv"))
    ap.add_argument("--show-errors", type=int, default=8)
    args = ap.parse_args(argv)

    try:
        import onnxruntime as ort
    except ImportError:
        print("缺少 onnxruntime：pip install onnxruntime", file=sys.stderr)
        return 2

    sess = ort.InferenceSession(str(args.onnx), providers=["CPUExecutionProvider"])
    in_name = sess.get_inputs()[0].name

    mapping = C.read_labels(args.labels)
    images = [p for p in C.list_images(args.raw) if p.name in mapping]
    if not images:
        print("没有可评估样本", file=sys.stderr)
        return 2

    groups = [
        ("A 基线 k=1.0", 1.0, False),
        ("B k=1.5", 1.5, False),
        ("C k=2.5", 2.5, False),
        ("D k=4.0", 4.0, False),
        ("E k=2.5 灰度域", 2.5, True),
    ]

    print(f"模型 {Path(args.onnx).name}，样本 {len(images)} 张（独立测试集）\n")
    print(f"{'组':<16}{'正确':>8}{'错答':>8}{'安全失败':>10}"
          f"{'答案正确率':>11}{'错答率':>9}")
    print("-" * 66)

    base = None
    all_rows = {}
    for title, k, gray in groups:
        t0 = time.time()
        rows = run_group(sess, in_name, images, mapping, k, gray)
        all_rows[title] = rows
        ok, wrong, safe, n = _stat(rows)
        if k == 1.0 and not gray:
            base = rows
        print(f"{title:<16}{ok:>8}{wrong:>8}{safe:>10}"
              f"{ok / n:>10.1%}{wrong / n:>10.1%}"
              f"   ({time.time() - t0:.1f}s)")

    # ---- 迁移分析：相对基线，每组「救回多少 / 弄坏多少」----
    if base is not None:
        base_ok = {r["name"] for r in base if r["verdict"] == OK}
        base_wrong = {r["name"] for r in base if r["verdict"] == WRONG}
        print("\n相对基线的迁移（正确→错答是红线，必须为 0 才可考虑）：")
        print(f"{'组':<16}{'救回(错答→对)':>14}{'救回(失败→对)':>14}"
              f"{'弄坏(对→错答)':>14}{'弄坏(对→失败)':>14}")
        print("-" * 74)
        for title, rows in all_rows.items():
            ok = {r["name"] for r in rows if r["verdict"] == OK}
            wrong = {r["name"] for r in rows if r["verdict"] == WRONG}
            fixed_w = len(base_wrong & ok)
            fixed_s = len({r["name"] for r in base if r["verdict"] == SAFE} & ok)
            broken = len(base_ok & wrong)
            lost = len(base_ok & (set(r["name"] for r in rows) - ok - wrong))
            print(f"{title:<16}{fixed_w:>14}{fixed_s:>14}{broken:>14}{lost:>14}")

    # ---- 错答清单（挑最优组展示）----
    print(f"\n各组的错答明细（{'C k=2.5' if 'C k=2.5' in all_rows else groups[0][0]}）：")
    show = all_rows.get("C k=2.5", all_rows[groups[0][0]])
    wrongs = [r for r in show if r["verdict"] == WRONG]
    for r in wrongs[:args.show_errors]:
        print(f"  {r['name']:<14}{r['gold']:<10}→ {r['pred'] or '(空)'}")
    if not wrongs:
        print("  （无错答）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
