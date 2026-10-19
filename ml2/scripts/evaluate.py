# -*- coding: utf-8 -*-
"""
ml2 评估脚本 —— 在「处理版」独立测试集上评估 ml2 模型。自包含，不 import ml/。

口径与 ml/ 的对比工具一致，能直接和原图基线对照：

    正确      最终答案对（能提交的）
    错答      会提交一个**错误**答案 —— 最危险的指标
    安全失败  求不出答案 → 换图重试（无害）

判「答案对不对」用的是 `campuslink.core.captcha_math.solve`（客户端模块，
非 ml 训练代码）——这样两条线用的是同一个算术口径。

用法（仓库根目录）：

    python ml2/scripts/evaluate.py --pt ml2/models/crnn_proc.pt
    python ml2/scripts/evaluate.py --onnx ml2/models/crnn_proc.onnx
    python ml2/scripts/evaluate.py --pt ... --data ml2/dataset_k25   # 换处理档的数据集
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core as K  # noqa: E402

sys.path.insert(0, str(K.REPO_ROOT))
from campuslink.core.captcha_math import solve  # noqa: E402

OK, WRONG, SAFE = "ok", "wrong", "safe"

#: 原图基线（ml/ 模型 + 原图，独立测试集 116 张）
BASELINE = "92.2% 正确 / 6.9% 错答"


def verdict(gold: str, pred: str) -> str:
    """预测式与原式**答案**相同也算对（与 contrast_ab.py 同口径）。"""
    ga, pa = solve(gold), solve(pred)
    if pred == gold or (pa is not None and pa == ga):
        return OK
    return SAFE if pa is None else WRONG


def make_predictor(args):
    """返回 predict(x)->str；x 为 [1,1,IMG_H,IMG_W] float32。"""
    if args.onnx:
        try:
            import onnxruntime as ort
        except ImportError:
            print("缺少 onnxruntime：pip install onnxruntime", file=sys.stderr)
            raise SystemExit(2)
        sess = ort.InferenceSession(str(args.onnx), providers=["CPUExecutionProvider"])
        in_name = sess.get_inputs()[0].name

        def predict(x):
            return K.ctc_greedy_decode(sess.run(None, {in_name: x})[0])[0]
        return predict

    try:
        import torch
    except ImportError:
        print("缺少 PyTorch；或改用 --onnx 走 onnxruntime。", file=sys.stderr)
        raise SystemExit(2)
    from train_crnn import CRNN  # 同目录，ml2 自己的模型定义

    ckpt = torch.load(str(args.pt), map_location="cpu", weights_only=False)
    model = CRNN(K.NUM_CLASSES)
    model.load_state_dict(ckpt["model"])
    model.eval()

    def predict(x):
        with torch.no_grad():
            return K.ctc_greedy_decode(model(torch.from_numpy(x)).numpy())[0]
    return predict


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ml2：评估处理版模型（同处理口径）")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--pt", default=str(K.ML2_ROOT / "models" / "crnn_proc.pt"),
                     help="ml2 训练权重（默认）")
    src.add_argument("--onnx", help="ml2 导出的 ONNX，给了就用它")
    ap.add_argument("--data", default=str(K.DATA_DIR), help="处理版数据集目录")
    ap.add_argument("--show-errors", type=int, default=12)
    args = ap.parse_args(argv)

    data = Path(args.data)
    test_dir, test_labels = data / "test", data / "test_labels.csv"
    samples = K.build_samples(test_dir, test_labels)
    if not samples:
        print(f"没有可评估样本：{test_dir}\n先跑 python ml2/scripts/build_dataset.py",
              file=sys.stderr)
        return 2

    predict = make_predictor(args)
    name = Path(args.onnx).name if args.onnx else Path(args.pt).name
    print(f"模型 {name}  测试集 {len(samples)} 张（处理版，独立于训练）\n")

    rows = []
    for s in samples:
        x = K.load_input(s.path)[None, :, :, :].astype(np.float32)
        pred = predict(x)
        rows.append({"name": s.path.name, "gold": s.label, "pred": pred,
                     "verdict": verdict(s.label, pred)})

    n = len(rows)
    ok = sum(r["verdict"] == OK for r in rows)
    wrong = sum(r["verdict"] == WRONG for r in rows)
    safe = sum(r["verdict"] == SAFE for r in rows)
    print(f"{'正确':>6}{'错答':>6}{'安全失败':>8}{'答案正确率':>11}{'错答率':>9}")
    print("-" * 44)
    print(f"{ok:>6}{wrong:>6}{safe:>8}{ok / n:>10.1%}{wrong / n:>9.1%}")

    wrongs = [r for r in rows if r["verdict"] == WRONG]
    print("\n错答明细：")
    for r in wrongs[:args.show_errors]:
        print(f"  {r['name']:<14}{r['gold']:<10}→ {r['pred'] or '(空)'}")
    if not wrongs:
        print("  （无错答）")

    print(f"\n对照原图基线（ml/ 模型）：{BASELINE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
