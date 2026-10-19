# -*- coding: utf-8 -*-
"""
错答画像 —— 回答「到底该补什么数据」，而不是盲目加样本。

## 为什么需要

`AGENT_BRIEF.md` 第 6 节说错误"高度集中在两位数 A 的首位被漏掉"。
这个描述来自 8 个错答的人工观察，样本太小，且措辞不精确
（实测既有"首位漏"也有"第二位漏"，`1`/`7` 也常被误读成别的数字）。

要知道下一步该补什么数据，得先知道错误**按什么维度聚集**。
本脚本在独立测试集和训练留出集上，按四个维度切开统计错误率：

    · 运算符            加/减/乘/除 —— 哪类最容易错
    · A 的位数          1 位 vs 2 位 —— 两位数是不是真的更差
    · 是否含细笔画字符   真值里有没有 1 或 7 —— 细笔画是不是重灾区
    · B 的位数          1 位 vs 2 位 —— 减法两位 B 是否更差

## 用法

    python ml/scripts/error_profile.py
"""
from __future__ import annotations

import argparse
import random as _random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
from campuslink.core.captcha_math import solve  # noqa: E402

OK, WRONG, SAFE = "ok", "wrong", "safe"


def _split(label: str) -> tuple[str, str, str] | None:
    """`A<op>B` → (A, op, B)"""
    for i, ch in enumerate(label):
        if ch in "加减乘除":
            return label[:i], ch, label[i + 1:]
    return None


def _verdict(gold: str, pred: str) -> str:
    ga, pa = solve(gold), solve(pred)
    if pred == gold or (pa is not None and pa == ga):
        return OK
    return SAFE if pa is None else WRONG


def collect(sess, in_name, images, mapping) -> list[dict]:
    rows = []
    for p in images:
        x = C.load_image(p)[None, :, :, :].astype(np.float32)
        logits = sess.run(None, {in_name: x})[0]
        pred = C.ctc_greedy_decode(logits)[0]
        gold = mapping[p.name]
        parts = _split(gold)
        if parts is None:
            continue
        a, op, b = parts
        rows.append({
            "name": p.name, "gold": gold, "pred": pred,
            "verdict": _verdict(gold, pred),
            "op": op,
            "a_digits": len(a),
            "b_digits": len(b),
            "thin": ("1" in gold or "7" in gold),
            "a_two": len(a) == 2,
        })
    return rows


def _table(rows: list[dict], key_fn, order: list[str], title: str) -> None:
    groups: dict[str, list[dict]] = {k: [] for k in order}
    for r in rows:
        k = key_fn(r)
        groups.setdefault(k, []).append(r)

    print(f"\n{title}")
    print(f"  {'分组':<16}{'样本':>6}{'正确':>8}{'错答':>8}{'安全失败':>10}{'错答率':>9}")
    for k in order:
        g = groups.get(k) or []
        if not g:
            continue
        n = len(g)
        w = sum(r["verdict"] == WRONG for r in g)
        o = sum(r["verdict"] == OK for r in g)
        s = sum(r["verdict"] == SAFE for r in g)
        print(f"  {k:<16}{n:>6}{o:>7} {o / n:>6.1%}{w:>7} {w / n:>6.1%}"
              f"{s:>7} {s / n:>6.1%}{w / n:>8.1%}")


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="错答画像统计")
    ap.add_argument("--onnx", default=str(root / "models" / "crnn_math.onnx"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--val-ratio", type=float, default=0.15)
    args = ap.parse_args(argv)

    try:
        import onnxruntime as ort
    except ImportError:
        print("缺少 onnxruntime", file=sys.stderr)
        return 2

    sess = ort.InferenceSession(str(args.onnx), providers=["CPUExecutionProvider"])
    in_name = sess.get_inputs()[0].name

    test_map = C.read_labels(root / "dataset" / "test_labels.csv")
    test_imgs = [p for p in C.list_images(root / "dataset" / "test_raw")
                 if p.name in test_map]

    train_map = C.read_labels(root / "dataset" / "labels" / C.LABEL_FILE)
    train_imgs = [p for p in C.list_images(root / "dataset" / "raw")
                  if p.name in train_map]
    samples = [C.Sample(p, train_map[p.name]) for p in train_imgs]
    _random.seed(args.seed)
    _random.shuffle(samples)
    hold = [s.path for s in samples[:max(1, int(len(samples) * args.val_ratio))]]

    for title, imgs, mapping in (
        ("A. 独立测试集", test_imgs, test_map),
        (f"B. 训练留出集（{len(hold)} 张）", hold, train_map),
    ):
        if not imgs:
            continue
        rows = collect(sess, in_name, imgs, mapping)
        n = len(rows)
        w = sum(r["verdict"] == WRONG for r in rows)
        print(f"\n{'=' * 74}")
        print(f"{title}：{n} 张，答案正确 "
              f"{sum(r['verdict'] == OK for r in rows) / n:.1%}，错答 {w / n:.1%}")
        print(f"{'=' * 74}")

        _table(rows, lambda r: r["op"], ["加", "乘", "减", "除"], "按运算符")
        _table(rows, lambda r: f"A {r['a_digits']} 位", ["A 1 位", "A 2 位"], "按 A 的位数")
        _table(rows, lambda r: f"B {r['b_digits']} 位", ["B 1 位", "B 2 位"], "按 B 的位数")
        _table(rows, lambda r: "含 1 或 7" if r["thin"] else "不含 1/7",
               ["含 1 或 7", "不含 1/7"], "按是否含细笔画字符（1/7）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
