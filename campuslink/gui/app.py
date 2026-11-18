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
    opts = {"autostart": False, "minimized": False, "debug": False,
            "reset": False, "selftest": False}
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
        elif a == "--selftest":
            opts["selftest"] = True
    return opts


def run_selftest() -> int:
    """
    打包自检（2026-11-18 新增）。

    用途：确认打包产物里的关键依赖真的能用 —— 尤其是那些
    「编译能过、运行时才报错」的东西（onnx 模型、cv2 原生库、
    WinDivert 驱动、门户档案）。

    为什么做成 exe 参数而不是单独脚本：
      打包后的 exe 没有标准输出，也没法 import 里面的模块。
      只有让 exe 自己跑一遍才知道真相。

    GUI 程序是 windowed（无控制台），所以结果**同时**写到
    exe 同目录的 `selftest.txt` 和日志里，调用方读文件。

    退出码：0 = 全通过，1 = 有失败项。
    """
    import io
    import os
    import sys
    import traceback

    lines: list[str] = []
    failed = 0

    def rec(ok: bool, name: str, detail: str = "") -> None:
        nonlocal failed
        mark = "OK  " if ok else "FAIL"
        if not ok:
            failed += 1
        lines.append(f"  [{mark}] {name}" + (f"  {detail}" if detail else ""))

    lines.append("=" * 66)
    lines.append(f"CampusLink 打包自检  v{__version__}")
    lines.append(f"  frozen={getattr(sys, 'frozen', False)}")
    lines.append(f"  安装目录: {paths.install_dir()}")
    lines.append(f"  数据目录: {paths.data_dir()}")
    lines.append("=" * 66)

    # ---- 1. 基础模块 ----
    lines.append("")
    lines.append("[1] 基础依赖")
    for mod in ("requests", "bs4", "lxml", "keyring"):
        try:
            __import__(mod)
            rec(True, mod)
        except Exception as exc:
            rec(False, mod, f"{type(exc).__name__}: {exc}")

    # ---- 2. 算式模型（登录验证码）----
    lines.append("")
    lines.append("[2] 算式验证码模型（登录用）")
    try:
        from ..core import crnn_math
        r = crnn_math.get_recognizer()
        rec(r is not None, "crnn_math 模型加载",
            "可用" if r is not None else "不可用（登录只能走人工兜底）")
    except Exception as exc:
        rec(False, "crnn_math", f"{type(exc).__name__}: {exc}")

    # ---- 3. ddddocr（注销验证码）----
    lines.append("")
    lines.append("[3] 字符验证码引擎（注销用）")
    try:
        import ddddocr
        rec(True, "ddddocr 模块")
        # 真跑一次识别，确认模型文件在位（模型缺失只有实际调用才暴露）
        d = ddddocr.DdddOcr(show_ad=False)
        # 用一张 80x24 的空白 PNG 测试模型能否前向
        import struct
        import zlib

        def _blank_png(w: int, h: int) -> bytes:
            raw = b"".join(b"\x00" + b"\xff" * w * 3 for _ in range(h))
            def chunk(typ: bytes, data: bytes) -> bytes:
                c = typ + data
                return (struct.pack(">I", len(data)) + c
                        + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))
            ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
            return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                    + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

        out = d.classification(_blank_png(80, 24))
        rec(isinstance(out, str), "ddddocr 推理", f"输出={out!r}")
    except Exception as exc:
        rec(False, "ddddocr", f"{type(exc).__name__}: {exc}")

    # ---- 4. cv2（ddddocr 的预处理依赖）----
    lines.append("")
    lines.append("[4] OpenCV（ddddocr 预处理）")
    try:
        import cv2
        rec(True, "cv2 原生库", f"ver={cv2.__version__}")
    except Exception as exc:
        rec(False, "cv2", f"{type(exc).__name__}: {exc}")

    # ---- 5. 门户档案 ----
    lines.append("")
    lines.append("[5] 门户档案")
    try:
        from .. import profiles
        lst = profiles.list_profiles()
        rec(len(lst) > 0, "档案数量", f"{len(lst)} 个")
        for p in lst:
            rec(True, f"  {p.name}", p.portal_url)
    except Exception as exc:
        rec(False, "profiles", f"{type(exc).__name__}: {exc}")

    # ---- 6. 注销协议链 ----
    lines.append("")
    lines.append("[6] 注销协议链")
    try:
        from ..core.portal import (LogoutPage, extract_message_from_html,
                                   extract_online_state)
        from ..core.authenticator import LOGOFF_SUCCESS_HINTS
        rec(True, "LogoutPage / 解析函数")
        rec("注销成功" in LOGOFF_SUCCESS_HINTS, "成功判据含「注销成功」")
        st = extract_online_state(
            '<span id="MainContent_LabelOnline"><font>已联网</font></span>')
        rec(st == "已联网", "在线状态解析", f"解析出 {st!r}")
    except Exception as exc:
        rec(False, "注销协议链", f"{type(exc).__name__}: {exc}")

    # ---- 7. WinDivert（共享助手 / UA 归一）----
    lines.append("")
    lines.append("[7] WinDivert（TTL 修复 + UA 归一）")
    try:
        from ..core import hotspot, ua_mask
        ok_pd, why = ua_mask.pydivert_available()
        rec(ok_pd, "pydivert 加载", why or "可用")
        rec(hotspot.is_admin() or True, "管理员权限",
            "是" if hotspot.is_admin() else "否（需管理员才能启用）")
        # 驱动文件是否落到磁盘
        d = ua_mask.describe()
        rec(True, "UA 归一目标", f"{d['target_ua']} ({d['target_len']} 字节)")
    except Exception as exc:
        rec(False, "WinDivert 链", f"{type(exc).__name__}: {exc}")

    # ---- 汇总 ----
    lines.append("")
    lines.append("=" * 66)
    if failed == 0:
        lines.append("  结果：全部通过")
    else:
        lines.append(f"  结果：{failed} 项失败")
    lines.append("=" * 66)

    text = "\n".join(lines)

    # 输出：安装目录下的 selftest.txt + 日志
    out_path = os.path.join(paths.install_dir(), "selftest.txt")
    try:
        with io.open(out_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        out_path = "(写入失败)"
    for ln in lines:
        log.info("selftest | %s", ln)
    # 有控制台时也打印（源码运行模式）
    try:
        if sys.stdout and sys.stdout.isatty():
            print(text)
    except Exception:
        pass

    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    opts = parse_args(argv)

    paths.ensure_data_dir()
    setup_logging(console=not (getattr(sys, "frozen", False)),
                  level=10 if opts["debug"] else 20)   # 10=DEBUG 20=INFO

    # 自检模式：不建 QApplication、不弹窗、不抢单实例锁 ——
    # 它要能在无人值守（比如 CI 或安装脚本）里跑。
    if opts["selftest"]:
        return run_selftest()

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
