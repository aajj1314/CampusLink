# -*- coding: utf-8 -*-
"""
算式验证码识别工具 —— 用训练好的 CRNN 模型直接识别验证码图片。

独立于 `campuslink/` 主链路（模型版单独开发），模型文件通过 --onnx 指定，
将来训练出更好的模型直接换文件即可，这个工具不用改。

## 用法

    # 识别本地图片（可多个）
    python tools/captcha_read.py ml/dataset/test_raw/cap_0000.png

    # 识别整个目录
    python tools/captcha_read.py ml/dataset/test_raw/ --limit 20

    # 直接从门户拉一张（需要校园网环境）
    python tools/captcha_read.py --url http://10.6.6.6/ACaptcha.ashx

    # 指定别的模型
    python tools/captcha_read.py xxx.png --onnx ml/models/crnn_math_835.onnx

输出：算式原文 + 计算结果。识别失败会明确说「未识别」，绝不猜一个答案。

## 关于对比度增强

`ml/dataset/发现-对比度增强预处理.md` 曾建议识别前做 ×2.5 对比度拉伸。
经 `ml/scripts/contrast_ab.py` A/B 实测：**增强后全面变差**（92.2%→82.8%，
错答率 6.9%→15.5%），原因是训练时模型只见过 ±20% 的对比度抖动，强增强
超出训练分布。所以本工具默认 **k=1.0（不增强）**，保留 --contrast 参数
只为将来「训练时加了强对比度增强的新模型」复核用。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml" / "scripts"))
sys.path.insert(0, str(ROOT))

import common as C  # noqa: E402
from campuslink.core.captcha_math import solve  # noqa: E402


def load_model(path: str):
    try:
        import onnxruntime as ort
    except ImportError:
        print("缺少 onnxruntime：pip install onnxruntime", file=sys.stderr)
        sys.exit(2)
    p = Path(path)
    if not p.is_file():
        print(f"找不到模型：{p}", file=sys.stderr)
        sys.exit(2)
    sess = ort.InferenceSession(str(p), providers=["CPUExecutionProvider"])
    return sess, sess.get_inputs()[0].name


def recognize(sess, in_name, img_bytes: bytes, contrast: float = 1.0) -> dict:
    """
    识别一张验证码 → {text, answer, conf, elapsed_ms}。

    识别失败（模型没认出 / 算式不合法）answer 为 None —— 和线上语义一致：
    宁可说「没认出来」，不猜。
    """
    from io import BytesIO

    from PIL import Image

    t0 = time.monotonic()
    try:
        img = Image.open(BytesIO(img_bytes))
        img.load()
    except Exception:
        return {"text": "", "answer": None, "conf": 0.0,
                "elapsed_ms": 0, "error": "图片无法解码"}

    if contrast and contrast != 1.0:
        from PIL import ImageEnhance
        img = ImageEnhance.Contrast(img).enhance(contrast)

    x = C.preprocess_pil(img)[None, :, :, :].astype(np.float32)
    logits = sess.run(None, {in_name: x})[0]
    text = C.ctc_greedy_decode(logits)[0]
    elapsed = int((time.monotonic() - t0) * 1000)

    # 置信度：被选中字符概率的最小值（与 conf_threshold.py 同口径）
    conf = 0.0
    if text:
        e = np.asarray(logits, dtype=np.float32)
        e = e - e.max(axis=-1, keepdims=True)
        p = np.exp(e) / np.exp(e).sum(axis=-1, keepdims=True)
        if p.ndim == 3:
            p = p[0]
        idx = p.argmax(axis=1)
        prev, confs = -1, []
        for t_, k_ in enumerate(idx):
            k_ = int(k_)
            if k_ != 0 and k_ != prev:
                confs.append(float(p[t_, k_]))
            prev = k_
        conf = min(confs) if confs else 0.0

    return {"text": text, "answer": solve(text) if text else None,
            "conf": conf, "elapsed_ms": elapsed, "error": None}


def _iter_sources(args):
    """把 --url / 文件 / 目录 统一成 (显示名, 图片字节) 迭代器。"""
    import requests

    if args.url:
        resp = requests.get(args.url, timeout=10, headers={"User-Agent":
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        resp.raise_for_status()
        sig = hashlib.md5(resp.content).hexdigest()[:8]
        yield f"URL[{sig}]", resp.content
        return

    paths: list[Path] = []
    for s in args.images:
        p = Path(s)
        if p.is_dir():
            paths.extend(C.list_images(p))
        elif p.is_file():
            paths.append(p)
        else:
            print(f"跳过不存在的路径：{s}", file=sys.stderr)
    if args.limit:
        paths = paths[:args.limit]
    for p in paths:
        yield p.name, p.read_bytes()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="算式验证码识别（CRNN 模型）")
    ap.add_argument("images", nargs="*", help="图片路径或目录")
    ap.add_argument("--url", help="直接从门户 URL 拉一张（校园网内）")
    ap.add_argument("--onnx", default=str(ROOT / "ml" / "models" / "crnn_math.onnx"))
    ap.add_argument("--contrast", type=float, default=1.0,
                    help="对比度倍数，默认 1.0（不增强，实测增强会降低准确率）")
    ap.add_argument("--limit", type=int, default=0, help="目录模式下最多识别几张")
    args = ap.parse_args(argv)

    if not args.images and not args.url:
        ap.error("需要至少一个图片路径/目录，或 --url")

    sess, in_name = load_model(args.onnx)
    count = ok = 0
    for name, data in _iter_sources(args):
        r = recognize(sess, in_name, data, args.contrast)
        count += 1
        if r["error"]:
            print(f"{name:<20} ✗ {r['error']}")
            continue
        if not r["text"]:
            print(f"{name:<20} 未识别（模型输出为空）")
            continue
        if r["answer"] is None:
            print(f"{name:<20} 算式 {r['text']:<10} 但算不出答案（疑似认错），"
                  f"置信度 {r['conf']:.2f}")
            continue
        ok += 1
        print(f"{name:<20} {r['text']:<10} = {r['answer']:>4}   "
              f"置信度 {r['conf']:.2f}  {r['elapsed_ms']}ms")

    if count > 3:
        print(f"\n共 {count} 张，成功给出答案 {ok} 张")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
