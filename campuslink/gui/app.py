# -*- coding: utf-8 -*-
"""
程序入口：初始化 Qt 应用、单实例检测、创建主窗口。

单实例为什么必要：
  开机启动（--autostart）和用户手动双击可能同时发生，
  两个实例会各自去认证同一个账号 —— 该门户对单账号有并发终端数限制，
  互相抢登录只会把两边都搞失败。所以第二个实例必须退出，
  并且把已存在的那个窗口叫出来。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import APP_NAME, APP_TITLE, ORG_NAME, __version__, paths
from ..logging_setup import get_logger, setup as setup_logging
from . import style
from .main_window import MainWindow

log = get_logger("app")

#: 单实例用的本地套接字名字
IPC_KEY = "CampusLink-SingleInstance-v1"


class SingleInstanceGuard:
    """
    用 QLocalServer 做单实例锁。

    比写锁文件好：进程崩溃后操作系统会自动释放套接字名，
    不会像锁文件那样留下一个「假死」的锁把用户挡在外面。
    """

    def __init__(self, key: str = IPC_KEY):
        self.key = key
        self.server: QLocalServer | None = None

    def try_acquire(self) -> bool:
        """拿到锁返回 True；已被占用则顺便通知对方把窗口显示出来。"""
        sock = QLocalSocket()
        sock.connectToServer(self.key)
        if sock.waitForConnected(300):
            # 已有实例在跑 —— 告诉它「用户又想打开了」，然后本进程退出
            sock.write(b"show\n")
            sock.flush()
            sock.waitForBytesWritten(300)
            sock.disconnectFromServer()
            return False

        # 上一次异常退出可能残留名字，先清掉再监听
        QLocalServer.removeServer(self.key)
        self.server = QLocalServer()
        self.server.listen(self.key)
        return True

    def listen_for_show(self, on_show) -> None:
        """有新实例尝试启动时，把已有窗口叫到前台。"""
        if not self.server:
            return

        def _handler():
            conn = self.server.nextPendingConnection()
            if conn is None:
                return
            conn.readyRead.connect(lambda: conn.readAll())
            conn.disconnected.connect(conn.deleteLater)
            on_show()

        self.server.newConnection.connect(_handler)


def build_app(argv: list[str] | None = None) -> QApplication:
    """创建并配置 QApplication。"""
    argv = list(argv if argv is not None else sys.argv)

    QGuiApplication.setApplicationName(APP_NAME)
    QGuiApplication.setApplicationDisplayName(APP_TITLE)
    QGuiApplication.setOrganizationName(ORG_NAME)
    QGuiApplication.setApplicationVersion(__version__)

    app = QApplication(argv)
    app.setWindowIcon(style.app_icon(256))
    app.setFont(style.app_font(10))
    app.setStyleSheet(style.QSS)
    # 关掉「最后一个窗口关闭就退出」——本程序常驻托盘
    app.setQuitOnLastWindowClosed(False)
    return app


def parse_args(argv: list[str]) -> dict:
    """极简参数解析。不引 argparse 是为了避免 noconsole 下它往 stderr 写东西。"""
    opts = {"autostart": False, "minimized": False, "debug": False, "reset": False}
    for a in argv[1:]:
        a = a.strip().lower()
        if a == "--autostart":
            opts["autostart"] = True
        elif a in ("--minimized", "-m"):
            opts["minimized"] = True
        elif a in ("--debug", "-d"):
            opts["debug"] = True
        elif a == "--reset":
            opts["reset"] = True
    return opts


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    opts = parse_args(argv)

    paths.ensure_data_dir()
    setup_logging(console=not (getattr(sys, "frozen", False)),
                  level=10 if opts["debug"] else 20)   # 10=DEBUG 20=INFO
    log.info("CampusLink %s 启动 | %s", __version__, paths.describe())

    app = build_app(argv)      # 必须先有 QApplication 才能用 QLocalSocket

    guard = SingleInstanceGuard()
    if not guard.try_acquire():
        log.info("已有实例在运行，本进程退出")
        return 0

    if opts["reset"]:
        # 给「忘记密码/配置乱了」留个逃生口，不需要用户去翻 AppData
        try:
            paths.config_file().unlink(missing_ok=True)
            log.info("已重置配置文件")
        except Exception:
            pass

    win = MainWindow(start_minimized=opts["autostart"] or opts["minimized"])
    guard.listen_for_show(win.show_from_tray)

    if not (opts["autostart"] or opts["minimized"]):
        win.show()
    else:
        # 开机启动时不弹窗口，但要保证托盘图标在
        QTimer.singleShot(0, win.hide_to_tray)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
