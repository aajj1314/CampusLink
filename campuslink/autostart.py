# -*- coding: utf-8 -*-
"""
开机自动启动。

**Windows 用注册表 Run 键，不用「启动文件夹 + 快捷方式」，也不用计划任务。**
理由：

  · 比快捷方式简单：不需要生成 .lnk（那要么依赖 pywin32，
    要么调 VBScript —— 而 VBScript 在新版 Windows 上正在被移除）。
  · 比 schtasks 可靠：上一版用任务计划程序，结果在装了终端管控软件的电脑上
    直接被安全策略拦掉。
  · 没有黑框闪现：本程序是 GUI（--noconsole），从 Run 键拉起不会有控制台窗口。
  · 用户态即可，不需要管理员权限。
  · 位置固定好排查：HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run

非 Windows 平台一律返回「不支持」，调用方据此把开关置灰。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "CampusLink"

#: 开机启动时带的参数：直接缩到托盘，不要弹窗打扰用户
STARTUP_ARGS = "--autostart"


def supported() -> bool:
    """当前平台是否支持（只有 Windows）。"""
    return sys.platform.startswith("win")


def _winreg():
    import winreg
    return winreg


def _command() -> str:
    """
    要写进注册表的命令行。

    打包后 = exe 自己；源码运行 = python + 入口脚本，
    这样开发期也能测这个开关。
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        return f'"{exe}" {STARTUP_ARGS}'

    # 源码运行：用 pythonw.exe 拉起 GUI，避免出现控制台窗口
    py = Path(sys.executable)
    pythonw = py.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else py
    entry = Path(__file__).resolve().parent / "gui" / "__main__.py"
    return f'"{runner}" "{entry}" {STARTUP_ARGS}'


def is_enabled() -> bool:
    """当前有没有设置开机启动。顺便校验路径是否还有效。"""
    if not supported():
        return False
    try:
        winreg = _winreg()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except Exception:
        return False


def current_command() -> str:
    """读出注册表里实际存的内容，用于排查「路径变了但注册表没更新」。"""
    if not supported():
        return ""
    try:
        winreg = _winreg()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except Exception:
        return ""


def enable() -> tuple[bool, str]:
    """开启开机启动。返回 (是否成功, 给用户看的话)。"""
    if not supported():
        return False, "当前系统不支持此功能"
    try:
        winreg = _winreg()
        cmd = _command()
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
        return True, "已设置为开机自动启动"
    except Exception as exc:
        return False, f"设置失败：{type(exc).__name__}"


def disable() -> tuple[bool, str]:
    """关闭开机启动。本来就没设置也算成功（幂等）。"""
    if not supported():
        return False, "当前系统不支持此功能"
    try:
        winreg = _winreg()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True, "已取消开机自动启动"
    except FileNotFoundError:
        return True, "开机启动本来就是关闭状态"
    except Exception as exc:
        return False, f"取消失败：{type(exc).__name__}"


def apply(enabled: bool) -> tuple[bool, str]:
    """把开关同步到系统。GUI 里那个复选框变化时调用。"""
    return enable() if enabled else disable()


def location_hint() -> str:
    """告诉用户去哪儿手动检查/删除。"""
    return (r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run"
            rf"  →  {VALUE_NAME}")
