"""开机自启：写入当前用户的 Run 注册表项。"""

from __future__ import annotations

import subprocess
import sys
import winreg
from pathlib import Path

from .config import DATA_DIR

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "CampusLinkSilent"
PID_FILE = DATA_DIR / "logs" / "app.pid"


def _pythonw() -> Path:
    executable = Path(sys.executable)
    candidate = executable.with_name("pythonw.exe")
    return candidate if candidate.exists() else executable


def _command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    return f'"{_pythonw()}" "{DATA_DIR / "run.pyw"}"'


def install() -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())


def uninstall() -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass
    _kill_running()


def is_installed() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


def _kill_running() -> None:
    if getattr(sys, "frozen", False):
        subprocess.run(
            ["taskkill", "/IM", Path(sys.executable).name, "/F"],
            capture_output=True,
            check=False,
        )
    else:
        # 按命令行特征结束本项目的后台进程，避免 PID 复用误杀
        command = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*run.pyw*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            check=False,
        )
    try:
        PID_FILE.unlink()
    except OSError:
        pass


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "install"
    if action == "install":
        install()
        print("已设置开机自启")
    elif action == "uninstall":
        uninstall()
        print("已取消开机自启并停止后台服务")
    elif action == "status":
        print("已设置开机自启" if is_installed() else "未设置开机自启")
    else:
        print("用法: python -m campusnet.autostart [install|uninstall|status]")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
