# -*- coding: utf-8 -*-
"""
PyInstaller 的入口脚本。

单独放一个入口文件而不是直接指向 campuslink/gui/__main__.py，原因有两个：
  1. PyInstaller 的 Analysis 需要一个「脚本」起点，指向包内文件时
     __package__ 会是空值，相对导入容易出问题；
  2. 开机启动的注册表项在源码运行时也指向这里，方便开发期测试。

打包后由 PyInstaller 调用；源码运行时等价于 `python -m campuslink.gui`。
"""
from __future__ import annotations

import multiprocessing
import sys

if __name__ == "__main__":
    # 打包成 exe 之后，如果将来引入多进程，必须调这一句，
    # 否则子进程会重新执行整个 GUI 启动流程，出现多个窗口。
    multiprocessing.freeze_support()

    from campuslink.gui.app import main

    sys.exit(main())
