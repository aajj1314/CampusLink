# -*- coding: utf-8 -*-
"""
把本地采好的数据集打包成 zip，方便传到训练机。

采样受网络位置限制（必须能访问校园网门户 `10.6.6.6`），
而训练机往往在别处 —— 所以先把图在能上校园网的机器上采好，
打包带过去，省一次折腾。

用法：
    python ml/scripts/pack_dataset.py                    # 打包 dataset → ml/dataset.zip
    python ml/scripts/pack_dataset.py --out /tmp/ds.zip  # 指定输出
    python ml/scripts/pack_dataset.py --only-raw         # 只带图，不带标注

产物结构（解开后可直接覆盖到目标机的 ml/ 下）：
    dataset/raw/*.png
    dataset/labels/labels.csv        （若有）
    dataset/labels/labels_auto.csv   （若有）
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="打包数据集用于传输")
    ap.add_argument("--raw", default=str(root / "dataset" / "raw"))
    ap.add_argument("--labels-dir", default=str(root / "dataset" / "labels"))
    ap.add_argument("--out", default=str(root / "dataset.zip"))
    ap.add_argument("--only-raw", action="store_true", help="只打包图片")
    args = ap.parse_args(argv)

    raw_dir = Path(args.raw)
    images = C.list_images(raw_dir)
    if not images:
        print(f"没找到图片：{raw_dir}", file=sys.stderr)
        return 2

    labels_dir = Path(args.labels_dir)
    label_files = [] if args.only_raw else [
        labels_dir / C.LABEL_FILE, labels_dir / "labels_auto.csv"]
    label_files = [p for p in label_files if p.is_file()]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in images:
            zf.write(p, arcname=f"dataset/raw/{p.name}")
        for p in label_files:
            zf.write(p, arcname=f"dataset/labels/{p.name}")

    size_mb = out.stat().st_size / 1e6
    print(f"已打包 {len(images)} 张图 + {len(label_files)} 个标注文件")
    print(f"  → {out}  ({size_mb:.1f} MB)")
    print("\n传到训练机后：")
    print(f"  unzip {out.name} -d /path/to/CampusLink/ml/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
