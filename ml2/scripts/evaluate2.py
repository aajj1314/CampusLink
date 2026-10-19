# -*- coding: utf-8 -*-
"""
评估 ml2（处理版图）模型 —— 用**同一套预处理**跑独立测试集。

这是本实验的关键对照口径：
    - ml2 模型只在「处理版」图上训练 → 评估也必须用「处理版」图（本脚本默认如此）
    - 拿 ml2 模型去评「原图」是分布不一致，会掉点，别这么比

指标口径与 `ml/scripts/contrast_ab.py` 一致，能直接和原图基线对比：

    正确      最终答案对（能提交的）
    错答      会提交一个**错误**答案 —— 最危险的指标
    安全失败  求不出答案 → 换图重试（无害）

用法：

    # 评 ml2 训练产物（.pt），预订参数与训练一致
    python ml2/scripts/evaluate2.py --pt ml2/models/crnn_math2.pt

    # 评导出的 ONNX
    python ml2/scripts/evaluate2.py --onnx ml2/models/crnn_math2.onnx

    # 与 ml/ 原图模型在**同一测试集、各自匹配预处理**下对照：
    python ml2/scripts/evaluate2.py --pt ml2/models/crnn_math2.pt      # 处理版
    python ml/scripts/evaluate.py   --raw ml/dataset/test_raw \
        --labels ml/dataset/test_labels.csv --onnx ml/models/crnn_math.onnx   # 原图基线
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

_THIS = Path(__file__).resolve()
_ML2 = _THIS.parents[1]
_ML_SCRIPTS = _THIS.parents[2] / "ml" / "scripts"
_REPO = _THIS.parents[2]
for _p in (str(_THIS.parent), str(_ML_SCRIPTS), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import common as C            # noqa: E402
import prep2 as P             # noqa: E402
from campuslink.core.captcha_math import solve  # noqa: E402

OK, WRONG, SAFE = "ok", "wrong", "safe"


def _verdict(gold: str, pred: str) -> str:
    """口径与 contrast_ab.py 一致：模型预测的算式与原式**答案**相同也算对。"""
    ga, pa = solve(gold), solve(pred)
    if pred == gold or (pa is not None and pa == ga):
        return OK
    return SAFE if pa is None else WRONG


def _make_predictor(args):
    """返回 predict(x) -> str 的闭包。x: [1,1,IMG_H,IMG_W] float32。"""
    if args.onnx:
        try:
            import onnxruntime as ort
        except ImportError:
            print("缺少 onnxruntime：pip install onnxruntime", file=sys.stderr)
            raise SystemExit(2)
        sess = ort.InferenceSession(str(args.onnx), providers=["CPUExecutionProvider"])
        in_name = sess.get_inputs()[0].name

        def predict(x):
            logits = sess.run(None, {in_name: x})[0]
            return C.ctc_greedy_decode(logits)[0]
        return predict

    try:
        import torch
    except ImportError:
        print("缺少 PyTorch；或改用 --onnx 走 onnxruntime。", file=sys.stderr)
        raise SystemExit(2)
    sys.path.insert(0, str(_THIS.parent))
    from train_crnn import CRNN  # 结构口径与 ml/ 完全一致

    ckpt = torch.load(str(args.pt), map_location="cpu")
    model = CRNN(C.NUM_CLASSES)
    model.load_state_dict(ckpt["model"])
    model.eval()
    if ckpt.get("contrast") is not None:
        print(f"权重记录的预处理：对比度 ×{ckpt.get('contrast')}，亮度 ×{ckpt.get('brightness')}")

    def predict(x):
        with torch.no_grad():
            logits = model(torch.from_numpy(x)).numpy()
        return C.ctc_greedy_decode(logits)[0]
    return predict


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="评估 ml2 处理版模型（匹配预处理）")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--pt", default=str(_ML2 / "models" / "crnn_math2.pt"),
                     help="ml2 训练权重 .pt（默认）")
    src.add_argument("--onnx", help="ml2 导出的 ONNX（给了就用它）")
    ap.add_argument("--raw", default=str(_REPO / "ml" / "dataset" / "test_raw"))
    ap.add_argument("--labels", default=str(_REPO / "ml" / "dataset" / "test_labels.csv"))
    ap.add_argument("--contrast", type=float, default=P.DEFAULT_CONTRAST)
    ap.add_argument("--brightness", type=float, default=P.DEFAULT_BRIGHTNESS)
    ap.add_argument("--show-errors", type=int, default=12)
    args = ap.parse_args(argv)

    mapping = C.read_labels(args.labels)
    images = [p for p in C.list_images(args.raw) if p.name in mapping]
    if not images:
        print("没有可评估样本", file=sys.stderr)
        return 2

    predict = _make_predictor(args)
    model_name = Path(args.onnx).name if args.onnx else Path(args.pt).name
    print(f"模型 {model_name}，样本 {len(images)} 张（独立测试集）")
    print(f"预处理：对比度 ×{args.contrast} → 亮度 ×{args.brightness}（与训练同此）\n")

    rows = []
    for p in images:
        x = P.load_processed(p, args.contrast, args.brightness)[None, :, :, :].astype(np.float32)
        pred = predict(x)
        gold = mapping[p.name]
        rows.append({"name": p.name, "gold": gold, "pred": pred,
                     "verdict": _verdict(gold, pred)})

    n = len(rows)
    ok = sum(r["verdict"] == OK for r in rows)
    wrong = sum(r["verdict"] == WRONG for r in rows)
    safe = sum(r["verdict"] == SAFE for r in rows)
    print(f"{'正确':>6}{'错答':>6}{'安全失败':>8}{'答案正确率':>11}{'错答率':>9}")
    print("-" * 44)
    print(f"{ok:>6}{wrong:>6}{safe:>8}{ok / n:>10.1%}{wrong / n:>9.1%}")

    print("\n错答明细：")
    wrongs = [r for r in rows if r["verdict"] == WRONG]
    for r in wrongs[:args.show_errors]:
        print(f"  {r['name']:<14}{r['gold']:<10}→ {r['pred'] or '(空)'}")
    if not wrongs:
        print("  （无错答）")

    print("\n对照基线（ml/ 原图模型，原图预处理）：正确 92.2% / 错答 6.9%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
