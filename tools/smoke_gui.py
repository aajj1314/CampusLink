# -*- coding: utf-8 -*-
"""
GUI 冒烟测试：在无显示环境下把窗口构造出来并跑一遍关键交互。

用 Qt 的 offscreen 平台插件，不需要真实桌面 ——
CI / 远程终端里也能验证「窗口能不能建起来、信号有没有接错」。

用法:
    .venv\\Scripts\\python.exe tools\\smoke_gui.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAIL = 0


def check(desc: str, cond: bool, extra: str = "") -> None:
    global FAIL
    if cond:
        print(f"  [OK]   {desc}")
    else:
        FAIL += 1
        print(f"  [FAIL] {desc} {extra}")


def main() -> int:
    print("=" * 64)
    print(" GUI 冒烟测试（offscreen）")
    print("=" * 64)
    print()

    from PySide6.QtWidgets import QApplication, QLineEdit

    from campuslink import paths
    from campuslink.gui import style
    from campuslink.gui.main_window import MainWindow
    from campuslink.core.models import NetworkState

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(style.QSS)

    print("[1] 构造主窗口")
    win = MainWindow()
    check("窗口创建成功", win is not None)
    check("窗口标题正确", "CampusLink" in win.windowTitle())
    print()

    print("[2] 任务书要求的界面要素是否齐全")
    check("学(工)号输入框", hasattr(win, "ed_student"))
    check("上网账号输入框", hasattr(win, "ed_account"))
    check("密码输入框", hasattr(win, "ed_password"))
    check("密码默认隐藏", win.ed_password.echoMode() == QLineEdit.Password)
    check("运营商选择", win.cb_operator.count() >= 4,
          f"实际 {win.cb_operator.count()} 项")
    check("保存账号信息开关", hasattr(win, "ck_save"))
    check("自动认证开关", hasattr(win, "ck_auto"))
    check("立即连接按钮", hasattr(win, "btn_connect"))
    check("网络状态显示", hasattr(win, "pill"))
    check("认证提示区", hasattr(win, "lb_status"))
    print()

    print("[3] 交互")
    win._toggle_password()
    check("切换后密码可见", win.ed_password.echoMode() == QLineEdit.Normal)
    win._toggle_password()
    check("再切回隐藏", win.ed_password.echoMode() == QLineEdit.Password)

    win.ed_student.setText("20240001")
    check("写入学工号", win.ed_student.text() == "20240001")
    check("上网账号留空时回退到学工号",
          win.current_credentials().account == "20240001")

    win.ed_password.setText("dummy-pass")
    check("凭据完整判定", win.current_credentials().is_complete())
    print()

    print("[4] 状态显示")
    for st in (NetworkState.ONLINE, NetworkState.NEED_LOGIN,
               NetworkState.OFFLINE, NetworkState.CHECKING):
        win.pill.set_state(st)
        assert win.pill.text().strip()
    check("四种状态都能渲染", True)

    win.set_status("测试提示", "err")
    check("错误提示生效", "测试提示" in win.lb_status.text())
    print()

    print("[5] 未填信息就连接 -> 应给出提示而不是崩")
    win.ed_student.clear()
    win.ed_account.clear()
    win.ed_password.clear()
    win.set_status("重置", "info")
    win.start_connect()
    txt = win.lb_status.text()
    check("提示缺少学(工)号", "学(工)号" in txt, f"实际: {txt}")
    check("没有启动认证线程", win._auth is None or not win._auth.isRunning())
    print()

    print("[6] 收尾")
    win._quitting = True
    win._shutdown()
    check("线程已停止", not (win._monitor and win._monitor.isRunning()))
    print()

    print("=" * 64)
    print(" 失败 %d 项" % FAIL if FAIL else " 全部通过")
    print("=" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
