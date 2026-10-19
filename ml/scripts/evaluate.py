# -*- coding: utf-8 -*-
"""
评估模型 —— 只看**最终答案对不对**（这才是上线关心的指标）。

序列全对不等于答案对（`7加9` 认成 `1加9` 序列错、答案也错；但 `7加9` 认成
`7加06` 序列错、答案反而对）。所以这里同时报两个指标，以答案准确率为准。

只依赖 onnxruntime，不需要 torch。

用法：
    python ml/scripts/evaluate.py                       # 用验证集
    python ml/scripts/evaluate.py --onnx path/to.onnx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

# 计算答案复用项目里的权威实现，保证评估口径和线上一致
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
from campuslink.core.captcha_math import solve  # noqa: E402


def _answer_of(label: str) -> str | None:
    """
    把 `A<op>B` 算成答案。

    **这里不传 max_b_digits**：那是给传统启发式兜 `=?` 污染用的
    （`8加4=?` → '8加42' 需要截断）。而模型输出的就是干净的 `A<op>B`，
    真出现 `6乘12` 就是真的 2 位操作数，截断会把 72 算成 6。
    """
    return solve(label)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="评估算式验证码模型")
    ap.add_argument("--onnx", default=str(root / "models" / "crnn_math.onnx"))
    ap.add_argument("--raw", default=str(root / "dataset" / "raw"))
    ap.add_argument("--labels", default=str(root / "dataset" / "labels" / C.LABEL_FILE))
    ap.add_argument("--show-errors", type=int, default=15, help="打印多少个错误样例")
    ap.add_argument("--holdout", action="store_true",
                    help="只评估训练时留出的验证集（复现 train_crnn.py 的划分），"
                         "这才是没见过的样本，不加此选项会混入训练图导致虚高")
    ap.add_argument("--seed", type=int, default=42, help="与训练一致的随机种子")
    ap.add_argument("--val-ratio", type=float, default=0.15, help="与训练一致的留出比例")
    args = ap.parse_args(argv)

    model_path = Path(args.onnx)
    if not model_path.exists():
        print(f"找不到模型：{model_path}\n先跑 train_crnn.py + export_onnx.py", file=sys.stderr)
        return 2

    try:
        import onnxruntime as ort
    except ImportError:
        print("缺少 onnxruntime：pip install onnxruntime", file=sys.stderr)
        return 2

    sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    in_name = sess.get_inputs()[0].name

    mapping = C.read_labels(args.labels)
    images = [p for p in C.list_images(args.raw) if p.name in mapping]
    if not images:
        print("没有可评估的已标注样本", file=sys.stderr)
        return 2

    if args.holdout:
        # 复现 train_crnn.py 的划分：同样的样本顺序 + 同样的 seed + 同样的 shuffle
        import random as _random
        samples = [C.Sample(p, mapping[p.name]) for p in images]
        _random.seed(args.seed)
        _random.shuffle(samples)
        n_val = max(1, int(len(samples) * args.val_ratio))
        images = [s.path for s in samples[:n_val]]
        print(f"只评估留出验证集：{len(images)} 张（seed={args.seed}, "
              f"val_ratio={args.val_ratio}）")

    n = len(images)
    seq_ok = ans_ok = 0
    errors: list[tuple[str, str, str, str]] = []

    import time
    t0 = time.time()
    for idx, p in enumerate(images, 1):
        x = C.load_image(p)[None, :, :, :]              # [1,1,H,W]
        logits = sess.run(None, {in_name: x.astype(np.float32)})[0]
        pred = C.ctc_greedy_decode(logits)[0]
        gold = mapping[p.name]

        if pred == gold:
            seq_ok += 1

        pa = _answer_of(pred)
        ga = _answer_of(gold)
        # 认对了就算答案对：不整除的题 solve() 返回 None，那不是识别失败。
        # 但空串预测也会得到 None，所以必须靠 pred == gold 这个条件兜住。
        if pred == gold or (pa is not None and pa == ga):
            ans_ok += 1
        elif len(errors) < args.show_errors:
            errors.append((p.name, gold, pred, f"{ga} vs {pa}"))

        if idx % 25 == 0 or idx == n:
            elapsed = time.time() - t0
            eta = elapsed / idx * (n - idx)
            sys.stdout.write(
                f"\r  进度 {idx}/{n} ({idx/n:5.1%})  "
                f"已命中 {seq_ok}/{idx} ({seq_ok/idx:.1%})  "
                f"剩余约 {eta:.0f}s   ")
            sys.stdout.flush()

    sys.stdout.write("\n\n")

    print(f"样本 {n} 张")
    print(f"  序列完全正确 : {seq_ok}/{n} = {seq_ok/n:.1%}")
    print(f"  最终答案正确 : {ans_ok}/{n} = {ans_ok/n:.1%}   ← 上线看这个")

    if errors:
        print(f"\n错误样例（前 {len(errors)} 个）：")
        print(f"  {'文件':<14}{'真值':<10}{'识别':<10}{'答案'}")
        for name, gold, pred, ans in errors:
            print(f"  {name:<14}{gold:<10}{pred or '(空)':<10}{ans}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
