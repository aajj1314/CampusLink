# -*- coding: utf-8 -*-
"""
预标注：用现有的「传统 CV + ddddocr 限定字符集」跑一遍，把候选写进
`labels_auto.csv`，供 label_tool.py 预填。

**它不是最终答案，只是让人工少打字。** 实测这套启发式准确率约 40%，
所以 label_tool 里它只是"预填 + 全选"，错了直接覆盖重打即可 ——
比从空白开始敲要快得多。

用法：
    python ml/scripts/autolabel.py                 # 标全部未确认的
    python ml/scripts/autolabel.py --limit 200     # 只标前 200 张
"""
from __future__ import annotations

import argparse
import collections
import io
import re
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

#: 预标注阶段的字符集（比训练字符集宽一点：也接受符号形式，后面归一）
_PROBE_CHARSET = "0123456789加减乘除÷×+-"
_OP_CHARS = "加减乘除"
_PATTERN = re.compile(r"(\d{1,2})([" + _OP_CHARS + r"])(\d{1,2})")


def _foreground(arr: np.ndarray, dist_thr: int, blur: int = 0) -> np.ndarray:
    """到白色点的欧氏距离做前景，输出白底黑字的 ddddocr 友好图。"""
    a = cv2.medianBlur(arr, blur) if blur else arr
    diff = 255.0 - a.astype(np.float32)
    dist = np.sqrt((diff ** 2).sum(axis=2))
    mask = (dist > dist_thr).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return np.stack([(1 - mask) * 255] * 3, axis=2).astype(np.uint8)


def _png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


def _extract(text: str) -> str | None:
    """
    从识别文本里抠出 `A<op>B`。

    第二个操作数**恒为 1 位**（该门户的样本规律），而尾部 `=?` 经常被
    认成数字粘在后面（`8加4=?` → '8加42'），所以这里截到 1 位。
    """
    norm = C.normalize_text(text)
    m = _PATTERN.search(norm)
    if not m:
        return None
    a, op, b = m.group(1), m.group(2), m.group(3)
    if len(b) > 1:
        b = b[0]
    if int(a) > 99:
        return None
    return f"{int(a)}{op}{int(b)}"


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="算式验证码预标注")
    ap.add_argument("--raw", default=str(root / "dataset" / "raw"))
    ap.add_argument("--out", default=str(root / "dataset" / "labels" / "labels_auto.csv"))
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少张，0 = 全部")
    ap.add_argument("--skip-confirmed", action="store_true", default=True,
                    help="跳过 labels.csv 里已人工确认的（默认开）")
    args = ap.parse_args(argv)

    try:
        import ddddocr
    except ImportError:
        print("缺少 ddddocr：pip install ddddocr", file=sys.stderr)
        return 2

    raw_dir = Path(args.raw)
    images = C.list_images(raw_dir)
    if not images:
        print(f"没找到图片：{raw_dir}", file=sys.stderr)
        return 2

    confirmed = C.read_labels(root / "dataset" / "labels" / C.LABEL_FILE)
    todo = [p for p in images if p.name not in confirmed]
    if args.limit > 0:
        todo = todo[:args.limit]

    print(f"共 {len(images)} 张，已确认 {len(confirmed)}，待预标 {len(todo)}")

    eng = ddddocr.DdddOcr(show_ad=False)
    eng.set_ranges(_PROBE_CHARSET)

    variants = [(140, 0), (152, 0), (165, 0), (152, 3)]
    out: dict[str, str] = {}
    hit = 0
    for i, p in enumerate(todo, 1):
        try:
            arr = np.array(Image.open(p).convert("RGB"))
        except Exception:
            continue
        votes: collections.Counter[str] = collections.Counter()
        for d, blur in variants:
            try:
                txt = eng.classification(_png(_foreground(arr, d, blur))) or ""
            except Exception:
                continue
            got = _extract(txt)
            if got:
                votes[got] += 1
        if votes:
            best, n = votes.most_common(1)[0]
            # 只采纳至少两个变体一致的，减少把噪声喂给人工
            if n >= 2:
                out[p.name] = best
                hit += 1
        if i % 50 == 0:
            print(f"  {i}/{len(todo)}  已出候选 {hit}")

    C.write_labels(args.out, out)
    print(f"\n预标注 {len(out)} 张（{len(out)/max(len(todo),1):.0%}），写入 {args.out}")
    print("下一步：python ml/scripts/label_tool.py  （回车确认，错了直接改）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
