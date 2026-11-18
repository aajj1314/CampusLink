# -*- coding: utf-8 -*-
"""
静默执行外部命令（Windows windowed 程序的必备封装）。

## 为什么需要

GUI 打包成 windowed（console=False）后，程序本身没有控制台。
此时 `subprocess.run(["ipconfig"])` 会让 Windows 给这个**控制台子程序
分配一个新控制台窗口** —— 用户看到的是「黑窗一闪而过」。

2026-11-18 实测踩坑：共享状态每 3 秒轮询一次 ipconfig，界面持续
「一闪一闪」；同一调用还跑在 UI 主线程上，导致界面卡死。

## 用法

    from .proc import run_quiet
    out = run_quiet(["ipconfig"], timeout=10)
    text = out.decode("ascii", "replace")

Windows 上自动带 CREATE_NO_WINDOW；其他平台原样透传。
"""
from __future__ import annotations

import subprocess
import sys

#: Windows 下禁止子进程创建控制台窗口；其他平台为空
_NO_WINDOW_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) \
    if sys.platform.startswith("win") else 0


def run_quiet(cmd: list[str], timeout: float = 10.0) -> bytes:
    """
    跑一条命令并返回 stdout 字节串（失败返回空串，不抛异常）。

    调用方只需要「拿文档解释输出」，不需要区分失败原因。
    需要非零退出码判断的场景请直接用 subprocess（记得带 flags）。
    """
    try:
        r = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            creationflags=_NO_WINDOW_FLAGS,
        )
        return r.stdout
    except Exception:
        return b""
