# -*- coding: utf-8 -*-
"""
把训练好的 .pt 导出成 ONNX，供 onnxruntime 推理（项目已依赖 onnxruntime，
不额外引入 torch 到运行时）。

用法：
    python ml/scripts/export_onnx.py
产物：
    ml/models/crnn_math.onnx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

try:
    import torch
except ImportError:
    print("缺少 PyTorch（导出时才需要）", file=sys.stderr)
    raise SystemExit(1)

from train_crnn import CRNN  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="导出 ONNX")
    ap.add_argument("--ckpt", default=str(root / "models" / "crnn_math.pt"))
    ap.add_argument("--out", default=str(root / "models" / "crnn_math.onnx"))
    ap.add_argument("--opset", type=int, default=12)
    args = ap.parse_args(argv)

    ckpt_path = Path(args.ckpt)
    if not ckpt_path.exists():
        print(f"找不到权重：{ckpt_path}\n先跑 train_crnn.py", file=sys.stderr)
        return 2

    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    chars = ckpt.get("chars", C.CHARS)
    if chars != C.CHARS:
        print(f"警告：权重里的字符集 {chars!r} 与当前代码 {C.CHARS!r} 不一致，"
              f"推理会错位。请确认版本。", file=sys.stderr)

    model = CRNN(len(chars) + 1)
    model.load_state_dict(ckpt["model"])
    model.eval()

    dummy = torch.zeros(1, 1, C.IMG_H, C.IMG_W, dtype=torch.float32)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    torch.onnx.export(
        model, dummy, str(out_path),
        input_names=["input"], output_names=["logits"],
        dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=args.opset,
    )
    print(f"已导出 {out_path}  ({out_path.stat().st_size/1e6:.1f} MB)")

    # 导完立刻用 onnxruntime 复核一遍，避免"导出成功但推理结果不一致"
    try:
        import numpy as np
        import onnxruntime as ort
        sess = ort.InferenceSession(str(out_path), providers=["CPUExecutionProvider"])
        x = np.random.randn(1, 1, C.IMG_H, C.IMG_W).astype(np.float32)
        with torch.no_grad():
            ref = model(torch.from_numpy(x)).numpy()
        got = sess.run(None, {"input": x})[0]
        diff = float(np.abs(ref - got).max())
        print(f"一致性复核：最大绝对误差 {diff:.2e}"
              f"{'  ✓' if diff < 1e-3 else '  ✗ 差异过大，请检查 opset'}")
        if diff >= 1e-3:
            return 1
    except ImportError:
        print("（跳过一致性复核：没装 onnxruntime）")

    print(f"验证准确率（训练时记录）：{ckpt.get('acc', float('nan')):.1%}")
    print("下一步：跑 evaluate.py 验证最终答案正确率，再向用户报告")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
