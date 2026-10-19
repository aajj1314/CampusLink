#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
算式验证码采样器 —— 为后续训练专用识别模型收集素材。

**只管抓图，不登录、不提交任何表单。** 它做的事和浏览者刷新验证码一样。

为什么需要它：
    门户 2026-10 把验证码改成了算式（见 docs/design/08-验证码改版应对.md）。
    通用 OCR + 启发式的准确率只有 ~13%，要稳定 90%+ 必须用针对该门户
    样本训练的小模型。训练的第一步是攒够图。

用法：
    # 先抓 100 张看看（默认间隔 0.3s，别把校园网门户打崩）
    python tools/captcha_sampler.py --count 100 --out ./captcha_dataset

    # 抓完自己标注：把每张图对应的答案写进 labels.csv（一张一行：文件名,答案）
    # 标注可以半自动：先跑 python -m campuslink.cli dry-run 出候选，人工只纠错

合规提醒：
    验证码图片属于**运行时敏感物料**，程序在正常登录流程里绝不落盘。
    本工具产出的是人工发起、显式指定目录的训练素材，属于离线标注场景，
    与运行时的"验证码不持久化"约束不冲突 —— 但请不要把产物提交进仓库，
    也不要上传到公共位置。默认输出目录已加入 .gitignore 建议范围。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://10.6.6.6/ACaptcha.ashx"
DEFAULT_OUT = "captcha_dataset"

#: 合法 PNG/JPEG 的魔数。门户偶尔会在出错时返回 HTML 错误页，
#: 直接存下来会污染数据集，所以先校验再落盘。
_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")


def _looks_like_image(data: bytes) -> bool:
    return any(data.startswith(m) for m in _MAGIC)


def _fetch(url: str, timeout: float) -> bytes | None:
    req = urllib.request.Request(url, headers={
        # 带上 UA：某些门户对非浏览器 UA 会返回空图
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CampusLink-Sampler",
        "Referer": url.rsplit("/", 1)[0] + "/login",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"  ! 请求失败：{exc}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="抓取算式验证码样本（只抓图，不登录）")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"验证码地址，默认 {DEFAULT_URL}")
    ap.add_argument("--count", type=int, default=50, help="抓取张数，默认 50")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"输出目录，默认 {DEFAULT_OUT}")
    ap.add_argument("--interval", type=float, default=0.3,
                    help="每张之间的间隔秒数，默认 0.3")
    ap.add_argument("--timeout", type=float, default=8.0, help="单次请求超时")
    ap.add_argument("--skip", type=int, default=0,
                    help="文件名起始序号（避免覆盖已有文件）")
    args = ap.parse_args(argv)

    if args.count <= 0:
        print("--count 必须大于 0", file=sys.stderr)
        return 2
    if args.interval < 0.1:
        # 太快的请求既不稳也容易触发门户限流
        print("--interval 不得小于 0.1 秒", file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    ok = fail = 0
    dup = 0
    seen: set[bytes] = set()

    print(f"目标 {args.url}")
    print(f"输出 {os.path.abspath(args.out)}")
    print(f"计划抓取 {args.count} 张，间隔 {args.interval}s\n")

    for i in range(args.count):
        # 加随机参数，绕过可能的缓存/代理缓存
        url = f"{args.url}{'&' if '?' in args.url else '?'}_={int(time.time() * 1000)}{i}"
        data = _fetch(url, args.timeout)

        if not data or not _looks_like_image(data):
            fail += 1
            print(f"  [{i + 1}/{args.count}] 跳过（非图片或空响应，{len(data or b'')} 字节）")
            time.sleep(args.interval)
            continue

        # 同一张图重复保存会污染训练集（门户可能在时间粒度内返回同一张）
        if data in seen:
            dup += 1
            print(f"  [{i + 1}/{args.count}] 跳过（与已抓样本重复）")
            time.sleep(args.interval)
            continue
        seen.add(data)

        name = f"cap_{args.skip + i:04d}.png"
        path = os.path.join(args.out, name)
        with open(path, "wb") as fh:
            fh.write(data)
        ok += 1
        print(f"  [{i + 1}/{args.count}] {name}  {len(data)} 字节")
        time.sleep(args.interval)

    print(f"\n完成：成功 {ok} 张，失败 {fail}，重复 {dup}")
    print(f"下一步：给 {args.out}/ 里的每张图标注答案，写进 labels.csv（文件名,答案）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
