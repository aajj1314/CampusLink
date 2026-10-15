#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 requests / urllib3 / bs4（及其纯 Python 传递依赖）vendor 进
`campuslink/router/_vendor/packages/`。

用途：OpenWrt 上不跑 pip（python3-pip 约 6 MB 且 mipsel 轮子常缺），
把这几包纯 Python 源码直接拷进源码树，随 `.ipk` / `/opt` 目录一起部署。
`lxml` 是 C 扩展，不在此列（走 OpenWrt feed 包；缺了 `_vendor.install()`
会自动把 bs4 的 "lxml" 退到 html.parser）。

用法（在开发机 / 构建机上执行一次）：
    python tools/vendor_deps.py

只拷贝纯 Python（跳过 .pyd/.so/.dll/__pycache__），不污染 requirements.txt。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "campuslink" / "router" / "_vendor" / "packages"

# requests 及 bs4 的纯 Python 传递依赖闭包。
# 注意：bs4 4.15 依赖 typing_extensions（不是可选项），漏了它在 OpenWrt 上
# 会在 import bs4 时报 "No module named 'typing_extensions'"。
PACKAGES = [
    "requests",
    "urllib3",
    "bs4",
    "soupsieve",
    "certifi",
    "idna",
    "charset_normalizer",
    "typing_extensions",
]

# 编译产物 / 缓存，直接跳过（纯 Python 兜底由各包自身提供）
_SKIP_SUFFIXES = (".pyd", ".so", ".dylib", ".dll", ".pyc", ".pyo")


def _find_dist(pkg: str) -> Path | None:
    spec = importlib.util.find_spec(pkg)
    if spec is None or spec.origin is None:
        return None
    if spec.submodule_search_locations:
        return Path(spec.submodule_search_locations[0])   # 包（目录）
    return Path(spec.origin)                              # 单文件模块（.py 本体）


def _version(pkg: str) -> str:
    try:
        import importlib.metadata as md
        return md.version(pkg)
    except Exception:
        return "?"


def main() -> int:
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True, exist_ok=True)

    manifest = {"generated_by": "tools/vendor_deps.py", "packages": {}}
    missing: list[str] = []
    for pkg in PACKAGES:
        src = _find_dist(pkg)
        if src is None or not src.exists():
            missing.append(pkg)
            print(f"  [跳过] {pkg}: 当前环境未安装")
            continue
        dst = DEST / pkg
        # 顶层包目录直接拷；.py 单文件模块按 <pkg>.py 拷（这样 import 才找得到）
        if src.is_dir():
            shutil.copytree(
                src, dst,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo",
                                              "*.pyd", "*.so", "*.dylib", "*.dll"),
            )
        else:
            dst = DEST / (pkg + ".py")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        manifest["packages"][pkg] = _version(pkg)
        print(f"  [已拷贝] {pkg} {_version(pkg)}")

    if missing:
        print("\n以下包缺失，请先在开发环境安装后再跑（OpenWrt 部署需它们）：")
        for m in missing:
            print(f"  - {m}")
        print("（PC 上跑 router_sim 用的是系统已装的 requests/bs4，不受影响）")
        return 1

    (DEST / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成。vendored 依赖已写入 {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
