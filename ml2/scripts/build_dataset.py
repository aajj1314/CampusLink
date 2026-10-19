# -*- coding: utf-8 -*-
"""
把原图数据集按固定变换处理成「处理版图」，独立落盘到 ml2/dataset/。

- 图片：逐张做 core.process_image（对比度 ×k + 整体降亮 ×b）后另存，
  **只读** ml/dataset 的原图，绝不改动它。
- 标注：直接复用 ml/ 的 labels.csv（字符集契约一致），过滤到实际存在的图片后
  另存一份到 ml2/dataset/*.csv。不重新标注。
- 产物目录（默认 ml2/dataset/）：

      train/               处理版训练图（对应 ml/dataset/raw）
      train_labels.csv     复用的训练标注
      test/                处理版测试图（对应 ml/dataset/test_raw）
      test_labels.csv      复用的测试标注
      processing.json      本次处理的参数与计数（可追溯）

ml2 的模型只吃这里的图；训练/评估脚本都从本目录读，不再做在线变换。

用法（仓库根目录）：

    python ml2/scripts/build_dataset.py
    python ml2/scripts/build_dataset.py --contrast 2.5 --brightness 1.0 --out ml2/dataset_k25
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import core as K  # noqa: E402  （同目录）


def _process_dir(src_dir: Path, dst_dir: Path,
                 contrast: float, brightness: float,
                 overwrite: bool, label: str) -> tuple[int, int]:
    """处理一个目录里的全部图片。返回 (成功数, 跳过数)。"""
    from PIL import Image

    files = K.list_images(src_dir)
    if not files:
        print(f"  [{label}] 源目录没有图片：{src_dir}", file=sys.stderr)
        return 0, 0
    dst_dir.mkdir(parents=True, exist_ok=True)
    ok = skipped = 0
    t0 = time.time()
    for i, p in enumerate(files, 1):
        dst = dst_dir / p.name
        if dst.exists() and not overwrite:
            skipped += 1
            continue
        try:
            with Image.open(p) as im:
                K.process_image(im, contrast, brightness).save(dst)
            ok += 1
        except Exception as exc:                       # noqa: BLE001
            print(f"  ! {p.name} 处理失败：{exc}", file=sys.stderr)
        if i % 500 == 0 or i == len(files):
            print(f"  [{label}] {i}/{len(files)}  {time.time() - t0:.0f}s")
    return ok, skipped


def _reuse_labels(src_csv: Path, dst_csv: Path, images_dir: Path) -> int:
    """复用标注：读源表 → 过滤到 images_dir 里实际存在的图 → 另存。返回条数。"""
    mapping = K.read_labels(src_csv)
    present = {p.name for p in K.list_images(images_dir)}
    kept = {name: lab for name, lab in mapping.items() if name in present}
    K.write_labels(dst_csv, kept)
    return len(kept)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="生成 ml2 的「处理版」数据集（图片重存 + 标注复用）")
    ap.add_argument("--raw", default=str(K.SRC_RAW), help="源训练原图目录")
    ap.add_argument("--labels", default=str(K.SRC_LABELS), help="源训练标注 CSV")
    ap.add_argument("--test-raw", default=str(K.SRC_TEST_RAW), help="源测试原图目录")
    ap.add_argument("--test-labels", default=str(K.SRC_TEST_LABELS), help="源测试标注 CSV")
    ap.add_argument("--out", default=str(K.DATA_DIR), help="输出目录（默认 ml2/dataset）")
    ap.add_argument("--contrast", type=float, default=K.DEFAULT_CONTRAST)
    ap.add_argument("--brightness", type=float, default=K.DEFAULT_BRIGHTNESS)
    ap.add_argument("--overwrite", action="store_true", help="已存在的处理版图也重算")
    ap.add_argument("--skip-test", action="store_true", help="只生成训练集，不处理测试集")
    args = ap.parse_args(argv)

    out = Path(args.out)
    train_dir = out / "train"
    test_dir = out / "test"

    print(f"处理变换：对比度 ×{args.contrast} → 亮度 ×{args.brightness}")
    print(f"输出目录：{out}\n")

    print("训练集：")
    ok_tr, sk_tr = _process_dir(Path(args.raw), train_dir,
                                args.contrast, args.brightness, args.overwrite, "train")
    n_lab_tr = _reuse_labels(Path(args.labels), out / "train_labels.csv", train_dir)

    n_img_te = n_lab_te = 0
    if not args.skip_test:
        print("\n测试集：")
        ok_te, sk_te = _process_dir(Path(args.test_raw), test_dir,
                                    args.contrast, args.brightness, args.overwrite, "test")
        n_img_te = ok_te + sk_te
        n_lab_te = _reuse_labels(Path(args.test_labels), out / "test_labels.csv", test_dir)

    meta = {
        "contrast": args.contrast,
        "brightness": args.brightness,
        "transform": "clip((v-128)*contrast+128,0,255) -> clip(*brightness,0,255)",
        "sources": {"raw": str(args.raw), "labels": str(args.labels),
                    "test_raw": str(args.test_raw), "test_labels": str(args.test_labels)},
        "counts": {"train_images": ok_tr + sk_tr, "train_images_new": ok_tr,
                   "train_labels": n_lab_tr, "test_images": n_img_te, "test_labels": n_lab_te},
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "processing.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== 完成 =====")
    print(f"train/ 图片 {ok_tr + sk_tr}"
          f"（新处理 {ok_tr}，已存在跳过 {sk_tr}）  标注 {n_lab_tr}")
    if not args.skip_test:
        print(f"test/  图片 {n_img_te}  标注 {n_lab_te}")
    print(f"参数记录：{out / 'processing.json'}")
    return 0 if ok_tr or sk_tr else 2


if __name__ == "__main__":
    raise SystemExit(main())
