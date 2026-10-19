# -*- coding: utf-8 -*-
"""
置信度阈值分析 —— 为「错答率 ≤ 3%」找一个可用门限。

## 为什么需要这个

`ml/AGENT_BRIEF.md` 第 6 节记录的唯一遗留项：独立测试集上最终答案正确率
92.2% 已达标，但**错答率 6.9% > 3%** 未达标。

本项目的原则是「宁可不识别，也不能给错答案」：错答案会消耗一次登录尝试、
可能触发风控；不识别只是多换一张图。所以只要能把「确定没把握」的输出
变成空串，就算把错答转成了安全失败。

## 做法

CTC 输出每一帧都有 13 类的概率分布。对一个样本，取最终解码出的每个字符
在被选中那一帧的概率，可以算出三种口径的置信度：

    min   被选中字符里**最小**的那个概率（最严格，盯最弱环节）
    mean  被选中字符概率的**算术平均**
    seq   概率的**几何平均**（≈ 整条路径的联合概率，对长串更严厉）

然后扫描阈值：低于阈值的输出改判为空串（换图），统计
「仍提交的错答数」与「被保住的正样本数」。

## 关键：别只看一个测试集

116 张里错答只有 8 个，按 3% 只能留 3 个，统计噪声极大。
所以脚本同时报两个口径：

    A. 独立测试集（116 张，从未参与训练）—— 上线依据
    B. 训练留出集（461 张，与训练同分布但未参与训练）—— 样本更多，用来看阈值是否稳

**只有两个口径都成立的阈值才可信。** 只看 A 会过拟合这 116 张。

## 用法

    python ml/scripts/conf_threshold.py
    python ml/scripts/conf_threshold.py --target 0.03 --step 0.005
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

#: 一个样本的判定结果
OK = "ok"            # 答案正确
WRONG = "wrong"      # 会提交一个错误答案（要消灭的就是它）
SAFE = "safe"        # 算不出答案 / 空输出 → 换图，安全


def _softmax(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    x = x - x.max(axis=-1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=-1, keepdims=True)


def decode_with_conf(logits) -> tuple[str, dict[str, float]]:
    """
    CTC 贪心解码 + 三种置信度。

    置信度只统计**最终保留**的字符（被 CTC 去重/去 blank 后剩下的那些帧），
    因为那些才是真正决定输出的位置。
    """
    probs = _softmax(logits)
    if probs.ndim == 3:
        probs = probs[0]
    if probs.ndim != 2:
        return "", {"min": 0.0, "mean": 0.0, "seq": 0.0}

    idx = probs.argmax(axis=1)
    chars: list[str] = []
    confs: list[float] = []
    prev = -1
    for t, k in enumerate(idx):
        k = int(k)
        if k == C.BLANK_IDX or k == prev:
            prev = k
            continue
        ch = C.IDX_TO_CHAR.get(k, "")
        if ch:
            chars.append(ch)
            confs.append(float(probs[t, k]))
        prev = k

    text = "".join(chars)
    if not confs:
        # 全 blank：模型主动放弃。置信度记 0，任何阈值都会把它当"不识别"，
        # 这本来就是安全行为，不影响统计结论。
        return text, {"min": 0.0, "mean": 0.0, "seq": 0.0}

    arr = np.asarray(confs, dtype=np.float64)
    return text, {
        "min": float(arr.min()),
        "mean": float(arr.mean()),
        "seq": float(np.exp(np.log(arr + 1e-12).mean())),
    }


def classify(gold: str, pred: str) -> tuple[str, str | None, str | None]:
    """→ (判定, 真值答案, 预测答案)"""
    ga = solve(gold)
    pa = solve(pred)
    if pred == gold or (pa is not None and pa == ga):
        return OK, ga, pa
    if pa is None:
        return SAFE, ga, pa
    return WRONG, ga, pa


def collect(sess, in_name, images, mapping) -> list[dict]:
    rows: list[dict] = []
    for p in images:
        x = C.load_image(p)[None, :, :, :].astype(np.float32)
        logits = sess.run(None, {in_name: x})[0]
        pred, conf = decode_with_conf(logits)
        gold = mapping[p.name]
        verdict, ga, pa = classify(gold, pred)
        rows.append({"name": p.name, "gold": gold, "pred": pred,
                     "verdict": verdict, "conf": conf,
                     "ga": ga, "pa": pa})
    return rows


def _fmt_table(rows: list[dict], target: float, step: float, title: str) -> dict:
    n = len(rows)
    n_wrong = sum(r["verdict"] == WRONG for r in rows)
    n_ok = sum(r["verdict"] == OK for r in rows)
    n_safe = sum(r["verdict"] == SAFE for r in rows)

    print(f"\n{'=' * 78}")
    print(f"{title}")
    print(f"样本 {n} 张：正确 {n_ok}（{n_ok / n:.1%}）  "
          f"错答 {n_wrong}（{n_wrong / n:.1%}）  安全失败 {n_safe}（{n_safe / n:.1%}）")
    print(f"{'=' * 78}")

    best: dict | None = None
    print(f"\n{'阈值':>6} {'min口径':>22} {'mean口径':>22} {'seq口径':>22}")
    print(f"{'':>6} {'错答率':>7}{'识别率':>8}{'换图':>7} "
          f"{'错答率':>7}{'识别率':>8}{'换图':>7} "
          f"{'错答率':>7}{'识别率':>8}{'换图':>7}")

    thresholds = np.arange(0.10, 0.995, step)
    for thr in thresholds:
        cells = []
        for key in ("min", "mean", "seq"):
            w = o = 0
            for r in rows:
                if r["conf"][key] >= thr:
                    if r["verdict"] == WRONG:
                        w += 1
                    elif r["verdict"] == OK:
                        o += 1
            cells.append((w / n, o / n))
        w_min, w_mean, w_seq = (c[0] for c in cells)
        if w_min <= target and w_mean <= target and w_seq <= target and best is None:
            best = {"thr": float(thr),
                    "min": cells[0], "mean": cells[1], "seq": cells[2]}
        print(f"{thr:>6.3f} "
              f"{cells[0][0]:>7.1%}{cells[0][1]:>8.1%}{1 - cells[0][0] - cells[0][1]:>7.1%} "
              f"{cells[1][0]:>7.1%}{cells[1][1]:>8.1%}{1 - cells[1][0] - cells[1][1]:>7.1%} "
              f"{cells[2][0]:>7.1%}{cells[2][1]:>8.1%}{1 - cells[2][0] - cells[2][1]:>7.1%}")

    if best:
        print(f"\n三种口径同时满足 错答率 ≤ {target:.0%} 的最小阈值：{best['thr']:.3f}")
    else:
        print(f"\n⚠ 三种口径没有共同阈值能让错答率都 ≤ {target:.0%}")
    return {"n": n, "n_ok": n_ok, "n_wrong": n_wrong, "best": best}


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="置信度阈值分析")
    ap.add_argument("--onnx", default=str(root / "models" / "crnn_math.onnx"))
    ap.add_argument("--target", type=float, default=0.03, help="目标错答率上限")
    ap.add_argument("--step", type=float, default=0.01)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--val-ratio", type=float, default=0.15)
    args = ap.parse_args(argv)

    try:
        import onnxruntime as ort
    except ImportError:
        print("缺少 onnxruntime", file=sys.stderr)
        return 2

    model = Path(args.onnx)
    if not model.exists():
        print(f"找不到模型：{model}", file=sys.stderr)
        return 2

    sess = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    in_name = sess.get_inputs()[0].name

    # ---------------- 口径 A：独立测试集 ----------------
    test_raw = root / "dataset" / "test_raw"
    test_csv = root / "dataset" / "test_labels.csv"
    test_map = C.read_labels(test_csv)
    test_imgs = [p for p in C.list_images(test_raw) if p.name in test_map]

    # ---------------- 口径 B：训练留出集 ----------------
    train_csv = root / "dataset" / "labels" / C.LABEL_FILE
    train_map = C.read_labels(train_csv)
    train_imgs = [p for p in C.list_images(root / "dataset" / "raw")
                  if p.name in train_map]

    sessions = []
    if test_imgs:
        sessions.append((f"A. 独立测试集（{test_csv.name}，从未参与训练）",
                         test_imgs, test_map))
    if train_imgs:
        samples = [C.Sample(p, train_map[p.name]) for p in train_imgs]
        _random.seed(args.seed)
        _random.shuffle(samples)
        n_val = max(1, int(len(samples) * args.val_ratio))
        hold = [s.path for s in samples[:n_val]]
        sessions.append((f"B. 训练留出集（{len(hold)} 张，与训练同分布）",
                         hold, train_map))

    if not sessions:
        print("没有可用数据", file=sys.stderr)
        return 2

    results = []
    for title, imgs, mapping in sessions:
        rows = collect(sess, in_name, imgs, mapping)
        results.append((title, rows, _fmt_table(rows, args.target, args.step, title)))

    # ---------------- 结论 ----------------
    print(f"\n{'=' * 78}\n结论\n{'=' * 78}")
    bests = [r[2]["best"] for r in results]
    if all(b is not None for b in bests):
        thr = max(b["thr"] for b in bests)     # 取更严格的那个
        print(f"两个口径都达标，取更严格阈值：**{thr:.2f}**")
        for title, rows, stat in results:
            n = stat["n"]
            for key in ("min", "mean", "seq"):
                w = o = 0
                for r in rows:
                    if r["conf"][key] >= thr:
                        if r["verdict"] == WRONG:
                            w += 1
                        elif r["verdict"] == OK:
                            o += 1
                print(f"  {title[:22]:<24} {key:>4} 口径 → "
                      f"错答 {w}/{n} = {w / n:.1%}   自动识别成功 {o}/{n} = {o / n:.1%}")
    else:
        print("有口径找不到达标阈值 —— 不要急着上线，先补数据/改进模型。")

    # 错误样本的置信度，用来解释"为什么砍得掉"
    rows = results[0][1]
    wrongs = sorted((r for r in rows if r["verdict"] == WRONG),
                    key=lambda r: r["conf"]["min"])
    if wrongs:
        print(f"\n错答样本的置信度（{results[0][0]}）：")
        print(f"  {'文件':<14}{'真值':<10}{'识别':<10}{'min':>7}{'mean':>8}{'seq':>8}")
        for r in wrongs:
            print(f"  {r['name']:<14}{r['gold']:<10}{r['pred'] or '(空)':<10}"
                  f"{r['conf']['min']:>7.3f}{r['conf']['mean']:>8.3f}{r['conf']['seq']:>8.3f}")

    oks = [r["conf"]["min"] for r in rows if r["verdict"] == OK]
    if oks:
        arr = np.asarray(oks)
        print(f"\n正确样本 min 置信度分位："
              f"p1={np.percentile(arr, 1):.3f} p5={np.percentile(arr, 5):.3f} "
              f"p10={np.percentile(arr, 10):.3f} p50={np.percentile(arr, 50):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
