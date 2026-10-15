# -*- coding: utf-8 -*-
"""
GUI 入口，供 `python -m campuslink.gui` 和 PyInstaller 使用。

开机启动的注册表项会指向这个文件（源码运行方式），
打包后则指向 exe 本身。
"""
from __future__ import annotations

import os
import sys

# 直接从源码目录运行时，把项目根加进 sys.path，
# 这样 `python campuslink/gui/__main__.py` 也能跑起来。
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))

from campuslink.gui.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
