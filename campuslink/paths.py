# -*- coding: utf-8 -*-
r"""
统一的路径解析。

和上一版（CLI 工具把数据放在 exe 旁边）不同，CampusLink 是**装在系统里的 GUI**，
安装目录通常是 `C:\Program Files\CampusLink`，那里**普通用户没有写权限**。
所以用户数据一律放用户目录：

    Windows : %APPDATA%\\CampusLink
    Linux   : $XDG_CONFIG_HOME/CampusLink  （默认 ~/.config/CampusLink）
    macOS   : ~/Library/Application Support/CampusLink

这样卸载后数据可以保留（也可以由安装程序询问是否删除），
而且多用户共用一台电脑时各自有各自的配置。

程序自带的只读资源（门户档案模板、图标）则从安装目录读。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "CampusLink"


def is_frozen() -> bool:
    """是不是 PyInstaller 打出来的 exe。"""
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """
    程序文件所在目录（只读）。

    打包后 = exe 所在目录；源码运行时 = 项目根目录。
    注意 PyInstaller 冻结态下 `__file__` 指向 %TEMP%\\_MEIxxxx，
    不能用它定位 exe 旁边的文件。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """
    打包进 exe 里的只读资源目录（图标、内置门户档案）。

    onefile 模式下 PyInstaller 会把它们解到 sys._MEIPASS，
    onefile/onedir 两种模式都得兼容。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return install_dir()


def data_dir() -> Path:
    """用户可写的数据目录。"""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        p = Path(base) / APP_NAME
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config")
        p = Path(base) / APP_NAME
    return p


def ensure_data_dir() -> Path:
    d = data_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


# ---- 常用文件 ----
CONFIG_FILE = "config.json"
LOG_DIR = "logs"


def config_file() -> Path:
    return data_dir() / CONFIG_FILE


def log_dir() -> Path:
    d = data_dir() / LOG_DIR
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def profiles_dir() -> Path:
    """用户自定义的门户档案（换学校时用）。"""
    d = data_dir() / "profiles"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def builtin_profiles_dir() -> Path:
    """
    随程序分发的内置门户档案。

    刻意放在 **包内部**（campuslink/profiles/）而不是程序根目录：
    PyInstaller 冻结后，包的 __file__ 会指向 _MEIPASS 里的对应位置，
    所以源码运行和打包运行能用同一句代码定位，不需要分支判断。
    """
    return Path(__file__).resolve().parent / "profiles"


def describe() -> str:
    kind = "已打包" if is_frozen() else "源码运行"
    return f"{kind} | 程序目录 {install_dir()} | 数据目录 {data_dir()}"
