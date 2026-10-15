# -*- coding: utf-8 -*-
"""
系统托盘图标与菜单。

要点：
  · 有些环境没有托盘（远程桌面、精简版系统），所以 available() 要做判断，
    没有托盘时主窗口的「关闭即最小化」行为必须退化成真正退出，
    否则用户会觉得程序关不掉。
  · 托盘菜单里的「断线自动重新认证」是个可勾选项，状态和主窗口里的复选框
    双向同步。
  · 托盘通知用 showMessage，不弹模态框 —— 后台重连成功不该打断用户。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .. import APP_TITLE
from . import style


class TrayIcon(QObject):
    """托盘图标封装。所有交互都通过信号抛给主窗口处理。"""

    activated_show = Signal()
    request_connect = Signal()
    request_quit = Signal()
    auto_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent = parent
        self._auto_state = True

        self.icon = QSystemTrayIcon(style.app_icon(64), parent)
        self.icon.setToolTip(f"{APP_TITLE}　·　未连接")

        menu = QMenu()
        self.act_show = QAction("打开主界面", menu)
        self.act_connect = QAction("立即连接", menu)
        self.act_auto = QAction("断线自动重新认证", menu)
        self.act_auto.setCheckable(True)
        self.act_auto.setChecked(True)
        self.act_quit = QAction("退出", menu)

        menu.addAction(self.act_show)
        menu.addAction(self.act_connect)
        menu.addSeparator()
        menu.addAction(self.act_auto)
        menu.addSeparator()
        menu.addAction(self.act_quit)
        self._menu = menu
        self.icon.setContextMenu(menu)

        self.act_show.triggered.connect(lambda: self.activated_show.emit())
        self.act_connect.triggered.connect(lambda: self.request_connect.emit())
        self.act_auto.toggled.connect(self._on_auto_toggled)
        self.act_quit.triggered.connect(lambda: self.request_quit.emit())
        self.icon.activated.connect(self._on_activated)

    # ---------------------------------------------------------------- 交互
    def _on_activated(self, reason) -> None:
        # 双击或单击都打开主界面（Windows 上习惯是双击，但单击也顺手）
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.activated_show.emit()

    def _on_auto_toggled(self, checked: bool) -> None:
        if checked != self._auto_state:
            self._auto_state = checked
            self.auto_toggled.emit(checked)

    # ---------------------------------------------------------------- 状态
    def set_auto(self, on: bool) -> None:
        """由主窗口同步过来，不触发反向信号。"""
        self._auto_state = on
        self.act_auto.blockSignals(True)
        self.act_auto.setChecked(on)
        self.act_auto.blockSignals(False)

    def set_state_text(self, text: str) -> None:
        self.icon.setToolTip(f"{APP_TITLE}　·　{text}")

    def refresh(self) -> None:
        """让菜单文字重新适应（例如语言/版本变化后的占位）。"""
        self.icon.setToolTip(self.icon.toolTip())

    # ---------------------------------------------------------------- 显示
    def available(self) -> bool:
        try:
            return QSystemTrayIcon.isSystemTrayAvailable()
        except Exception:
            return False

    def show(self) -> None:
        if self.available():
            try:
                self.icon.show()
            except Exception:
                pass

    def hide(self) -> None:
        try:
            self.icon.hide()
        except Exception:
            pass

    def notify(self, title: str, message: str) -> None:
        """气泡提示。没有托盘或系统不支持时静默跳过。"""
        if not self.available():
            return
        try:
            self.icon.showMessage(title, message, style.app_icon(64), 4000)
        except Exception:
            pass
