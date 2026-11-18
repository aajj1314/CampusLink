# -*- coding: utf-8 -*-
"""
主窗口。

界面取向是「给完全不懂电脑的人用」：一屏放得下、顺序就是操作顺序、
状态用颜色和大字一眼可辨、出错时给的是人话而不是错误码。

线程模型：
    主线程           本文件，只管画界面和收信号
    AuthWorker       执行一次认证（可取消）
    MonitorWorker    常驻，周期检测网络、必要时触发自动重连
    WarmupWorker     后台预热验证码模型
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .. import APP_TITLE, __version__, autostart, paths, profiles
from ..config import AppConfig
from ..core import hotspot
from ..core.authenticator import Authenticator
from ..core.models import Credentials, LoginResult, NetworkState, Operator
from ..logging_setup import get_logger, register_secret
from .. import secrets
from ..visionnode import NodeClient, NodeError, RouterWatcher, VisionNode
from . import style
from .captcha_dialog import DEFAULT_TIMEOUT, CaptchaDialog
from .tray import TrayIcon
from .worker import AuthWorker, LogoutWorker, MonitorWorker, WarmupWorker

log = get_logger("gui")


class StatusPill(QLabel):
    """左上角那个彩色状态标签。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusBar")
        self.set_state(NetworkState.UNKNOWN)

    def set_state(self, state: NetworkState, extra: str = "") -> None:
        fg, bg, text = style.status_colors(state.value)
        if extra:
            text = f"{text} · {extra}"
        self.setText(f"  ●  {text}")
        self.setStyleSheet(
            f"#StatusBar {{ color: {fg}; background: {bg};"
            f" border: 1px solid {fg}33; border-radius: 8px;"
            f" padding: 10px 14px; font-size: 14px; font-weight: 600; }}")


class MainWindow(QMainWindow):
    """主窗口。"""

    #: 供托盘菜单调用（信号跨线程安全）
    requestConnect = Signal()
    #: visionnode 线程回调 → 主线程（跨线程安全）
    _routerReachable = Signal(bool)
    _routerEvent = Signal(str, object)
    #: 共享状态后台采集完成 → 主线程更新 UI（ipconfig 不能跑在 UI 线程）
    _hotspotStatusReady = Signal(object)

    def __init__(self, start_minimized: bool = False):
        super().__init__()
        self.cfg = AppConfig.load()
        self.profile = profiles.load_profile(self.cfg.profile_name)
        self.authenticator = Authenticator(self.profile, verbose=True)

        self._auth: AuthWorker | None = None
        self._monitor: MonitorWorker | None = None
        self._logout: LogoutWorker | None = None      # 2026-11-18 新增
        self._warmup: WarmupWorker | None = None
        self._loading = True          # 装载配置期间不要触发保存
        self._quitting = False
        self._tray_hint_shown = False

        # ---- Router 视觉节点状态 ----
        self._vision_client: NodeClient | None = None
        self._vision_node: VisionNode | None = None
        self._router_watcher: RouterWatcher | None = None
        self._mode = "independent"    # independent | managed | degraded
        self._managed = False

        # ---- 校园网共享（TTL 修复）----
        self._fixer = hotspot.TtlFixer()
        self._hotspot_busy = False

        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(style.app_icon(128))
        self.setMinimumSize(520, 640)
        self.resize(560, 720)

        self._build_ui()
        self._wire()
        self._load_into_ui()
        self._loading = False

        # 上次开启过视觉节点：恢复监测（加载阶段不启动后台线程，这里补上）
        if self.cfg.vision_node_enabled:
            self._start_watcher()
            self._refresh_vision_state()

        self._start_monitor()
        self._start_warmup()

        if start_minimized or (self.cfg.start_minimized and self.cfg.autostart):
            QTimer.singleShot(0, self.hide_to_tray)

    # ================================================================ 界面
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 内容可能比窗口高，套一层滚动区，小屏 / 缩放 150% 也不会挤坏
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        page = QWidget()
        scroll.setWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(14)

        lay.addLayout(self._build_header())
        lay.addWidget(self._build_form_card())
        lay.addWidget(self._build_option_card())
        lay.addWidget(self._build_router_card())
        lay.addWidget(self._build_hotspot_card())
        lay.addLayout(self._build_actions())
        lay.addWidget(self._build_status_area())
        lay.addStretch(1)
        lay.addWidget(self._build_footer())

    # ---------------------------------------------------------------- 头部
    def _build_header(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(style.app_icon(40).pixmap(40, 40))
        row.addWidget(icon)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        t = QLabel(APP_TITLE)
        t.setObjectName("AppTitle")
        s = QLabel("登录校园网，自动重连，无需每次手动输入")
        s.setObjectName("AppSubtitle")
        titles.addWidget(t)
        titles.addWidget(s)
        row.addLayout(titles)
        row.addStretch(1)

        box.addLayout(row)

        self.pill = StatusPill()
        box.addWidget(self.pill)
        return box

    # ---------------------------------------------------------------- 表单
    def _build_form_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        grid = QGridLayout(card)
        grid.setContentsMargins(18, 16, 18, 16)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(11)

        title = QLabel("上网账号信息")
        title.setObjectName("SectionTitle")
        grid.addWidget(title, 0, 0, 1, 3)

        # 学工号
        grid.addWidget(self._label("学(工)号"), 1, 0)
        self.ed_student = QLineEdit()
        self.ed_student.setPlaceholderText("请输入学号或工号")
        self.ed_student.setClearButtonEnabled(True)
        grid.addWidget(self.ed_student, 1, 1, 1, 2)

        # 上网账号
        grid.addWidget(self._label("上网账号"), 2, 0)
        self.ed_account = QLineEdit()
        self.ed_account.setPlaceholderText("留空则与学(工)号相同")
        self.ed_account.setClearButtonEnabled(True)
        grid.addWidget(self.ed_account, 2, 1, 1, 2)

        # 密码 + 眼睛
        grid.addWidget(self._label("密码"), 3, 0)
        self.ed_password = QLineEdit()
        self.ed_password.setEchoMode(QLineEdit.Password)
        self.ed_password.setPlaceholderText("请输入上网密码")
        grid.addWidget(self.ed_password, 3, 1)

        self.btn_eye = QPushButton("显示")
        self.btn_eye.setObjectName("Eye")
        self.btn_eye.setFixedWidth(52)
        self.btn_eye.setCursor(Qt.PointingHandCursor)
        self.btn_eye.setToolTip("按住或点击可查看密码")
        grid.addWidget(self.btn_eye, 3, 2)

        # 运营商
        grid.addWidget(self._label("运营商"), 4, 0)
        self.cb_operator = QComboBox()
        for op in Operator:
            self.cb_operator.addItem(op.value)
        grid.addWidget(self.cb_operator, 4, 1, 1, 2)

        hint = QLabel("账号由运营商提供；实名登记、改密码请带证件到信息中心办理")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        grid.addWidget(hint, 5, 0, 1, 3)
        return card

    @staticmethod
    def _label(text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("FieldLabel")
        lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lb.setMinimumWidth(72)
        return lb

    # ---------------------------------------------------------------- 选项
    def _build_option_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(6)

        title = QLabel("选项")
        title.setObjectName("SectionTitle")
        box.addWidget(title)

        self.ck_save = QCheckBox("保存账号信息（密码存进系统凭据管理器，不写明文）")
        self.ck_auto = QCheckBox("断线自动重新认证")
        self.ck_startup = QCheckBox("开机自动启动")
        self.ck_minimized = QCheckBox("启动时最小化到托盘")
        self.ck_manual = QCheckBox("验证码人工兜底（自动识别没把握时弹出手工填写）")
        self.ck_manual.setToolTip(
            "2.0 默认用训练好的模型自动识别算式验证码；把握不足的结果会换图重试。\n"
            "勾选后，遇到模型没把握的图会弹窗让你手工填答案（比反复换图更快）。\n"
            "不勾则完全自动，直到重试次数用完。")

        for c in (self.ck_save, self.ck_auto, self.ck_startup, self.ck_minimized,
                  self.ck_manual):
            box.addWidget(c)

        self.lb_secret_status = QLabel(secrets.status_text())
        self.lb_secret_status.setObjectName("Hint")
        self.lb_secret_status.setWordWrap(True)
        box.addWidget(self.lb_secret_status)

        if not autostart.supported():
            self.ck_startup.setEnabled(False)
            self.ck_startup.setToolTip("仅 Windows 支持")
        return card

    # ---------------------------------------------------------------- Router 视觉节点
    def _build_router_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(6)

        title = QLabel("Router 视觉节点")
        title.setObjectName("SectionTitle")
        box.addWidget(title)

        self.ck_vision = QCheckBox("作为视觉节点连接 Router（由 Router 托管认证）")
        box.addWidget(self.ck_vision)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.ed_router = QLineEdit()
        self.ed_router.setPlaceholderText("Router 地址，如 http://192.168.1.1:7700")
        row.addWidget(self.ed_router, 1)
        self.btn_pair = QPushButton("配对")
        self.btn_pair.setObjectName("Ghost")
        self.btn_pair.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.btn_pair)
        box.addLayout(row)

        self.lb_router_status = QLabel("")
        self.lb_router_status.setObjectName("Hint")
        self.lb_router_status.setWordWrap(True)
        box.addWidget(self.lb_router_status)

        # 默认关闭：地址框和配对按钮禁用（勾选开关后才可用）
        self.ed_router.setEnabled(False)
        self.btn_pair.setEnabled(False)
        return card

    # ---------------------------------------------------------------- 校园网共享
    def _build_hotspot_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(6)

        title = QLabel("校园网共享（手机/平板连热点）")
        title.setObjectName("SectionTitle")
        box.addWidget(title)

        self.ck_hotspot = QCheckBox("启用共享助手（修复 TTL，热点连上才能上网）")
        self.ck_hotspot.setToolTip(
            "校园网出口的代理回包 TTL=1，Windows 转发时递减到 0 会直接丢弃——\n"
            "不修就是「手机连上热点、网页全打不开」。\n"
            "助手用 WinDivert 把这类包救回来，并把转发的包 TTL 统一为 64。\n"
            "需要管理员权限（WinDivert 要装驱动）；热点本身请用下面的按钮打开设置开启。")
        box.addWidget(self.ck_hotspot)

        self.lb_hotspot = QLabel("")
        self.lb_hotspot.setObjectName("Hint")
        self.lb_hotspot.setWordWrap(True)
        box.addWidget(self.lb_hotspot)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_admin = QPushButton("以管理员重启")
        self.btn_admin.setObjectName("Ghost")
        self.btn_admin.setCursor(Qt.PointingHandCursor)
        self.btn_admin.setVisible(False)
        row.addWidget(self.btn_admin)
        self.btn_hotspot_settings = QPushButton("打开移动热点设置")
        self.btn_hotspot_settings.setObjectName("Ghost")
        self.btn_hotspot_settings.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.btn_hotspot_settings)
        row.addStretch(1)
        box.addLayout(row)
        return card

    # ---------------------------------------------------------------- 按钮
    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        self.btn_connect = QPushButton("立即连接")
        self.btn_connect.setObjectName("Primary")
        self.btn_connect.setMinimumHeight(46)
        self.btn_connect.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.btn_connect, 3)

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("Ghost")
        self.btn_cancel.setMinimumHeight(46)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.btn_cancel, 1)

        #: 注销按钮（2026-11-18 新增）。
        #: 用途：腾出会话名额 —— 校园网限制一台设备在线，
        #: 想在手机上用时，先在电脑上注销。
        #: 注销后**不自动重连**，这是设计意图，不是 bug。
        self.btn_logout = QPushButton("注销")
        self.btn_logout.setObjectName("Ghost")
        self.btn_logout.setMinimumHeight(46)
        self.btn_logout.setCursor(Qt.PointingHandCursor)
        self.btn_logout.setToolTip(
            "断开当前校园网连接，腾出会话名额（比如给手机用）")
        row.addWidget(self.btn_logout, 1)

        self.btn_check = QPushButton("检测网络")
        self.btn_check.setObjectName("Ghost")
        self.btn_check.setMinimumHeight(46)
        self.btn_check.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.btn_check, 1)
        return row

    # ---------------------------------------------------------------- 状态区
    def _build_status_area(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("运行状态")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        head.addStretch(1)

        self.btn_logs = QPushButton("查看日志")
        self.btn_logs.setObjectName("LinkBtn")
        self.btn_logs.setCursor(Qt.PointingHandCursor)
        head.addWidget(self.btn_logs)
        box.addLayout(head)

        self.lb_status = QLabel("准备就绪")
        self.lb_status.setWordWrap(True)
        self.lb_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lb_status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        box.addWidget(self.lb_status)

        self.lb_mode = QLabel("模式：独立")
        self.lb_mode.setObjectName("Hint")
        box.addWidget(self.lb_mode)
        return card

    def _build_footer(self) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)

        lb = QLabel(f"版本 {__version__}　·　{secrets.status_text()}")
        lb.setObjectName("Hint")
        row.addWidget(lb)
        row.addStretch(1)

        btn_about = QPushButton("关于")
        btn_about.setObjectName("LinkBtn")
        btn_about.setCursor(Qt.PointingHandCursor)
        row.addWidget(btn_about)
        self.btn_about = btn_about
        return w

    # ================================================================ 信号
    def _wire(self) -> None:
        self.btn_connect.clicked.connect(self.start_connect)
        self.btn_cancel.clicked.connect(self.cancel_connect)
        self.btn_check.clicked.connect(self.check_now)
        self.btn_logout.clicked.connect(self.start_logout)      # 2026-11-18 新增
        self.btn_logs.clicked.connect(self.open_logs)
        self.btn_about.clicked.connect(self.show_about)
        self.btn_eye.clicked.connect(self._toggle_password)
        self.requestConnect.connect(self.start_connect)

        # 输入变化即保存（有 400ms 去抖，避免每敲一个字写一次磁盘）
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._persist)
        for w in (self.ed_student, self.ed_account):
            w.textChanged.connect(self._schedule_save)
        self.ed_password.textChanged.connect(self._schedule_save)
        self.cb_operator.currentIndexChanged.connect(self._schedule_save)
        self.ck_save.toggled.connect(self._on_save_toggled)
        self.ck_auto.toggled.connect(self._schedule_save)
        self.ck_minimized.toggled.connect(self._schedule_save)
        self.ck_manual.toggled.connect(self._schedule_save)
        self.ck_startup.toggled.connect(self._on_startup_toggled)

        # ---- Router 视觉节点 ----
        self.ck_vision.toggled.connect(self._on_vision_toggled)
        self.ck_hotspot.toggled.connect(self._on_hotspot_toggled)
        self.btn_admin.clicked.connect(self._on_admin_restart)
        self.btn_hotspot_settings.clicked.connect(hotspot.open_hotspot_settings)

        # 共享状态每 3 秒刷新（后台线程采集，避免 ipconfig 卡住 UI）
        self._hotspotStatusReady.connect(self._apply_hotspot_status)
        self._hotspot_timer = QTimer(self)
        self._hotspot_timer.setInterval(3000)
        self._hotspot_timer.timeout.connect(self._refresh_hotspot)
        self._hotspot_timer.start()
        self.btn_pair.clicked.connect(self._on_pair_clicked)
        self.ed_router.textChanged.connect(self._schedule_save)
        self._routerReachable.connect(self._on_router_reachable)
        self._routerEvent.connect(self._on_router_event)
        # 恢复托管前的防抖（10s，避免 Router 抖动导致频繁切换）
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.setInterval(10000)
        self._resume_timer.timeout.connect(self._on_resume_timeout)

        # 托盘
        self.tray = TrayIcon(self)
        self.tray.activated_show.connect(self.show_from_tray)
        self.tray.request_connect.connect(self.start_connect)
        self.tray.request_quit.connect(self.quit_app)
        self.tray.auto_toggled.connect(self._on_tray_auto_toggled)

    # ================================================================ 配置
    def _load_into_ui(self) -> None:
        c = self.cfg
        self.ed_student.setText(c.student_id)
        self.ed_account.setText(c.account)
        idx = self.cb_operator.findText(c.operator)
        self.cb_operator.setCurrentIndex(idx if idx >= 0 else 0)

        pwd = c.get_password()
        if pwd:
            self.ed_password.setText(pwd)
            register_secret(pwd)      # 登记脱敏，之后日志里不会再出现它

        self.ck_save.setChecked(bool(pwd))
        self.ck_auto.setChecked(c.auto_connect)
        self.ck_minimized.setChecked(c.start_minimized)
        self.ck_manual.setChecked(c.captcha_manual)

        self.ck_vision.setChecked(c.vision_node_enabled)
        self.ed_router.setText(c.router_url)
        self.ed_router.setEnabled(c.vision_node_enabled)
        self.btn_pair.setEnabled(c.vision_node_enabled)

        # 上次开启过共享助手：管理员身份下自动拉起
        self.ck_hotspot.setChecked(c.hotspot_ttl)
        if c.hotspot_ttl:
            QTimer.singleShot(300, self._on_hotspot_toggled_restore)

        # 开机启动以注册表实际状态为准，不信配置文件里的值
        if autostart.supported():
            real = autostart.is_enabled()
            self.ck_startup.setChecked(real)
            self.cfg.autostart = real

        if not c.first_run_done:
            if not (c.student_id and pwd):
                self.set_status("第一次使用：请填写学(工)号和密码，然后点「立即连接」", "info")
            self.cfg.first_run_done = True
            self._persist()

    def _schedule_save(self) -> None:
        if self._loading:
            return
        self._save_timer.start()

    def _persist(self) -> None:
        if self._loading:
            return
        c = self.cfg
        c.student_id = self.ed_student.text().strip()
        c.account = self.ed_account.text().strip()
        c.operator = self.cb_operator.currentText()
        c.auto_connect = self.ck_auto.isChecked()
        c.start_minimized = self.ck_minimized.isChecked()
        c.captcha_manual = self.ck_manual.isChecked()
        c.vision_node_enabled = self.ck_vision.isChecked()
        c.router_url = self.ed_router.text().strip()
        c.hotspot_ttl = self.ck_hotspot.isChecked()
        c.profile_name = self.profile.name
        c.save()

    def _on_save_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        if checked:
            if not secrets.available():
                self.ck_save.setChecked(False)
                QMessageBox.warning(
                    self, "无法保存密码",
                    "这台电脑上的系统凭据管理器不可用，出于安全考虑本程序\n"
                    "不会把密码保存成明文文件。\n\n"
                    "你仍然可以正常使用，只是每次启动都需要重新输入密码。")
                return
            self._store_password()
        else:
            self.cfg.forget_password()
            self.set_status("已从系统凭据管理器中删除保存的密码", "info")

    def _store_password(self) -> None:
        pwd = self.ed_password.text()
        if not pwd:
            self.set_status("请先输入密码再勾选「保存账号信息」", "warn")
            self.ck_save.setChecked(False)
            return
        account = self.ed_account.text().strip() or self.ed_student.text().strip()
        if not account:
            self.set_status("请先填写学(工)号，再保存密码", "warn")
            self.ck_save.setChecked(False)
            return
        self._persist()               # 先把账号写进配置，凭据条目名要用到它
        if self.cfg.set_password(pwd):
            register_secret(pwd)
            self.set_status("密码已保存到系统凭据管理器", "ok")
        else:
            self.ck_save.setChecked(False)
            self.set_status("密码保存失败，请稍后重试", "err")

    def _on_startup_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        if not autostart.supported():
            return
        ok, msg = autostart.apply(checked)
        self.cfg.autostart = checked if ok else autostart.is_enabled()
        self._persist()
        if ok:
            self.set_status(msg + ("（登录 Windows 后自动运行）" if checked else ""), "ok")
            self.tray.refresh()
        else:
            self.set_status(msg, "err")
            self._loading = True
            self.ck_startup.setChecked(autostart.is_enabled())
            self._loading = False

    def _on_tray_auto_toggled(self, on: bool) -> None:
        self.ck_auto.setChecked(on)

    # ================================================================ Router 视觉节点
    # ---------------------------------------------------------------- 共享助手
    def _on_hotspot_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        if not checked:
            self._fixer.stop()
            self._refresh_hotspot()
            self._schedule_save()
            return

        ok, msg = self._fixer.start()
        if not ok:
            self.ck_hotspot.setChecked(False)      # 回滚勾选
            if "管理员" in msg:
                self.lb_hotspot.setText(
                    "共享助手需要管理员权限：点下方「以管理员重启」，重启后会自动启用。")
                self.btn_admin.setVisible(True)
            else:
                self.lb_hotspot.setText(f"共享助手启动失败：{msg}")
            return

        self.lb_hotspot.setText(msg)
        self._schedule_save()

    def _on_hotspot_toggled_restore(self) -> None:
        """启动时恢复上次的共享开关（提权重启后自动回到这里）。"""
        if not self.ck_hotspot.isChecked():
            return
        ok, msg = self._fixer.start()
        if ok:
            self.lb_hotspot.setText(msg)
        else:
            # 非管理员：保持勾选（重启后自动生效），提示提权按钮
            self.lb_hotspot.setText(
                f"共享助手待启用：{msg}。点「以管理员重启」生效。")
            self.btn_admin.setVisible(True)

    def _on_admin_restart(self) -> None:
        """保存当前配置 → UAC 提权重启（重启后 cfg.hotspot_ttl 自动拉起重启）。"""
        self.cfg.hotspot_ttl = True
        self._persist()
        if hotspot.relaunch_as_admin():
            self._quitting = True
            QApplication.instance().quit()
        else:
            self.lb_hotspot.setText("UAC 被取消，共享助手未启用。")

    def _refresh_hotspot(self) -> None:
        """触发一次后台采集（3 秒定时器调用；绝不在 UI 线程跑 ipconfig）。"""
        if self._hotspot_busy:
            return
        self._hotspot_busy = True
        fixer = self._fixer

        def _collect():
            try:
                st = hotspot.collect_status(fixer)
            except Exception:
                st = None
            self._hotspot_busy = False
            if st is not None:
                self._hotspotStatusReady.emit(st)

        threading.Thread(target=_collect, name="hotspot-status", daemon=True).start()

    def _apply_hotspot_status(self, st) -> None:
        """后台采集结果 → UI（只在主线程执行）。"""
        self.lb_hotspot.setText(
            f"{st.ready_hint}"
            + (f"　｜　回程改写 {st.packets_in} 包 · 出站 {st.packets_out} 包"
               if st.ttl_running else "")
            + (f"\n最近错误：{st.last_error}" if st.last_error else ""))
        self.btn_admin.setVisible(not st.admin and st.pydivert_ok)
        self.ck_hotspot.setEnabled(st.pydivert_ok)

    def _on_vision_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self.ed_router.setEnabled(checked)
        self.btn_pair.setEnabled(checked)
        if checked:
            self._start_watcher()
            self._refresh_vision_state()
        else:
            self._resume_timer.stop()
            self._stop_watcher()
            self._stop_node()
            self._apply_mode("independent")
            self.lb_router_status.setText("")
        self._schedule_save()

    def _on_pair_clicked(self) -> None:
        url = self.ed_router.text().strip()
        if not url:
            self.set_status("请先填写 Router 地址", "warn")
            return
        client = NodeClient(url)
        try:
            pin = client.pair_start().get("pin", "")
        except NodeError as exc:
            self.set_status(f"无法连接 Router：{exc}", "err")
            return
        ret = QMessageBox.question(
            self, "Router 配对",
            f"配对码：<b>{pin}</b>\n\n点「是」完成与 Router 的配对。\n"
            f"（配对码 60 秒内单次有效，过期需重新点「配对」）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret != QMessageBox.Yes:
            return
        try:
            result = client.pair_confirm(pin, device_name="本机（Windows）",
                                         device_kind="desktop",
                                         capabilities=["ocr"])
        except NodeError as exc:
            self.set_status(f"配对失败：{exc}", "err")
            return
        token = result.get("device_token", "")
        if not token:
            self.set_status("配对失败：未返回设备凭据", "err")
            return
        secrets.save("router", url, token)     # device_token 存系统凭据管理器
        register_secret(token)
        self.set_status("配对成功，已保存设备凭据", "ok")
        self._start_watcher()                  # 用带 token 的 client 重建监测
        self._refresh_vision_state()

    def _start_watcher(self) -> None:
        url = self.ed_router.text().strip()
        self._stop_watcher()
        if not url:
            self._vision_client = None
            return
        token = secrets.load("router", url)
        self._vision_client = NodeClient(url, token=token or "")
        self._router_watcher = RouterWatcher(self._vision_client,
                                             on_change=self._routerReachable.emit)
        self._router_watcher.start()

    def _stop_watcher(self) -> None:
        if self._router_watcher:
            self._router_watcher.stop()
            self._router_watcher = None

    def _on_router_reachable(self, reachable: bool) -> None:
        if not self.ck_vision.isChecked():
            return
        if reachable:
            # 恢复：10s 防抖后再重新托管，避免抖动
            self._resume_timer.start()
            self.lb_router_status.setText("Router 已恢复，稍后重新托管…")
        else:
            # 失联：立即降级回独立模式
            self._resume_timer.stop()
            self._apply_mode("degraded")
            self.lb_router_status.setText("Router 失联，已切回独立模式")
            self.tray.notify("CampusLink", "Router 失联，已自动切回独立模式")

    def _on_resume_timeout(self) -> None:
        if not self.ck_vision.isChecked():
            return
        url = self.ed_router.text().strip()
        token = secrets.load("router", url) if url else ""
        if token and self._router_watcher and self._router_watcher.reachable:
            self._apply_mode("managed")
            self.lb_router_status.setText("已重新托管")

    def _on_router_event(self, kind: str, payload: object) -> None:
        p = payload if isinstance(payload, dict) else {}
        if kind == "submitted":
            log.info("已向 Router 提交验证码结果 task=%s accepted=%s",
                     p.get("task_id"), p.get("accepted"))
        elif kind == "error":
            log.warning("视觉节点：%s", p.get("message"))
        else:
            log.debug("视觉节点事件 %s %s", kind, p)

    def _refresh_vision_state(self) -> None:
        """根据 开关/配对/可达性 重新决定模式。"""
        if not self.ck_vision.isChecked():
            self._apply_mode("independent")
            return
        url = self.ed_router.text().strip()
        if not url:
            self.lb_router_status.setText("请填写 Router 地址")
            self._apply_mode("independent")
            return
        token = secrets.load("router", url)
        if not token:
            self.lb_router_status.setText("尚未配对，请点「配对」")
            self._apply_mode("independent")
            return
        reachable = self._router_watcher.reachable if self._router_watcher else False
        if reachable:
            self.lb_router_status.setText("已连接 Router，托管认证中")
            self._apply_mode("managed")
        else:
            self.lb_router_status.setText("Router 不可达")
            self._apply_mode("degraded")

    def _apply_mode(self, mode: str) -> None:
        if mode == self._mode:
            return
        prev, self._mode = self._mode, mode
        log.info("运行模式切换：%s → %s", prev, mode)
        if mode == "managed":
            self.lb_mode.setText("模式：Router 托管")
            self._set_managed(True)
            self._ensure_node_running()
        elif mode == "degraded":
            self.lb_mode.setText("模式：失联降级中")
            self._set_managed(False)
            self._stop_node()
        else:
            self.lb_mode.setText("模式：独立")
            self._set_managed(False)
            self._stop_node()

    def _set_managed(self, managed: bool) -> None:
        """托管时禁用本地「立即连接」，避免双登录抢门户并发。"""
        self._managed = managed
        self.btn_connect.setEnabled(not managed)
        self.btn_connect.setToolTip("由 Router 托管，本地认证已禁用" if managed else "")

    def _ensure_node_running(self) -> None:
        if self._vision_node and self._vision_node.running:
            return
        if not self._vision_client:
            return
        self._vision_node = VisionNode(self._vision_client,
                                       on_event=self._routerEvent.emit)
        self._vision_node.start()

    def _stop_node(self) -> None:
        if self._vision_node:
            self._vision_node.stop()
            self._vision_node = None

    # ================================================================ 认证
    def start_connect(self) -> None:
        if self._managed:
            self.set_status("当前由 Router 托管认证，本地「立即连接」已禁用", "warn")
            return
        if self._auth and self._auth.isRunning():
            return

        cred = self.current_credentials()
        if not cred.is_complete():
            missing = []
            if not cred.student_id:
                missing.append("学(工)号")
            if not cred.account:
                missing.append("上网账号")
            if not cred.password:
                missing.append("密码")
            self.set_status("请先填写：" + "、".join(missing), "warn")
            self.ed_password.setFocus() if not cred.password else self.ed_student.setFocus()
            self.show_from_tray()
            return

        register_secret(cred.password)

        # 勾了保存但还没存过，顺手存一下，省得用户再点一次
        if self.ck_save.isChecked() and not self.cfg.get_password():
            self._store_password()

        self._set_busy(True)
        self.set_status("正在连接…", "info")
        self.pill.set_state(NetworkState.CHECKING)

        self._auth = AuthWorker(self.authenticator, cred, self)
        self._auth.progress.connect(lambda m: self.set_status(m, "info"))
        self._auth.captcha_seen.connect(self._on_captcha)
        self._auth.result.connect(self._on_auth_result)
        self._auth.finished.connect(self._on_auth_thread_done)

        # 人工输入兜底：托管模式下登录发生在 Router 侧，本地弹窗没意义，不启用
        if self.cfg.captcha_manual and not self._managed:
            self._auth.enable_manual_captcha(wait_s=DEFAULT_TIMEOUT + 10)
            self._auth.manual_captcha.connect(self._on_manual_captcha)

        self._auth.start()

    def cancel_connect(self) -> None:
        if self._auth and self._auth.isRunning():
            self._auth.cancel()
            self.set_status("正在取消…", "warn")

    def current_credentials(self) -> Credentials:
        pwd = self.ed_password.text()
        return Credentials(
            student_id=self.ed_student.text().strip(),
            account=self.ed_account.text().strip(),
            password=pwd,
            operator=self.cb_operator.currentText(),
        ).normalized()

    def _on_captcha(self, img: bytes, text: str) -> None:
        # 验证码本身不敏感，可以进日志；但只记识别结果，不记图片
        log.debug("本次验证码识别结果: %s", text or "(未识别出)")

    def _on_manual_captcha(self, img: bytes, auto_text: str) -> None:
        """
        工作线程请求人工输入（运行在主线程，可以安全弹 UI）。

        线程模型：worker 阻塞在 threading.Event 上等这里回话，
        所以对话框必须是模态的、且不能拖太久（有 90s 自动跳过兜底）。
        """
        if self._auth is None or not self._auth.isRunning():
            # 线程已经结束了（比如用户点了取消），直接交回 None 放行
            if self._auth is not None:
                self._auth.submit_answer(None)
            return
        dlg = CaptchaDialog(
            img, auto_text,
            math_mode=(getattr(self.profile, "captcha_mode", "text") == "math"),
            timeout_s=DEFAULT_TIMEOUT,
            cancel_fn=lambda: bool(self._auth and self._auth.cancelled),
            parent=self,
        )
        try:
            answer = dlg.exec_answer()
        except Exception:
            log.exception("验证码对话框异常")
            answer = None
        # 对话框关了但线程可能刚好结束：submit_answer 只是 set Event，永远安全
        if self._auth is not None:
            self._auth.submit_answer(answer)

    def _on_auth_result(self, res: LoginResult) -> None:
        if res.ok:
            self.pill.set_state(NetworkState.ONLINE)
            self.set_status(res.message, "ok")
            self.tray.notify("CampusLink", res.message or "认证成功")
        else:
            state = res.state if res.state != NetworkState.UNKNOWN else NetworkState.NEED_LOGIN
            self.pill.set_state(state)
            self.set_status(res.message, "err")
            if res.attempts:
                self.lb_status.setText(f"{res.message}　（共尝试 {res.attempts} 次）")

    def _on_auth_thread_done(self) -> None:
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.btn_connect.setVisible(not busy)
        self.btn_cancel.setVisible(busy)
        self.btn_cancel.setEnabled(busy)
        for w in (self.ed_student, self.ed_account, self.ed_password,
                  self.cb_operator, self.ck_save, self.ck_minimized):
            w.setEnabled(not busy)
        if busy:
            self._cancel_pending = False

    # ============================================================ 注销（2026-11-18）
    def start_logout(self) -> None:
        """
        注销当前校园网会话。

        为什么要这个功能：学校限制「一台设备在线」。
        想在手机上用时，得先在电脑上注销腾出名额。

        **注销成功后不自动重连** —— 这是设计意图：
        用户要的就是"断开"，自动登回去等于白做。
        """
        if self._auth and self._auth.isRunning():
            self.set_status("正在连接中，请先取消再注销", "warn")
            return
        if self._logout and self._logout.isRunning():
            return

        # 在线状态一般才有意义，但离线下也允许点（幂等，服务端会返回未联网）
        if not self._confirm_logout():
            return

        self._set_logout_busy(True)
        self.set_status("正在注销…", "info")
        self.pill.set_state(NetworkState.CHECKING)

        self._logout = LogoutWorker(
            self.authenticator,
            min_confidence=self.cfg.logout_min_confidence,
            parent=self,
        )
        self._logout.progress.connect(lambda m: self.set_status(m, "info"))
        self._logout.captcha_seen.connect(self._on_captcha)
        self._logout.result.connect(self._on_logout_result)
        self._logout.finished.connect(self._on_logout_thread_done)

        # 人工兜底：注销的验证码很简单（4 位字符），只在快用完重试时才打扰
        if self.cfg.captcha_manual and not self._managed:
            self._logout.enable_manual_captcha(wait_s=DEFAULT_TIMEOUT + 10)
            self._logout.manual_captcha.connect(self._on_manual_logout_captcha)

        self._logout.start()

    def _confirm_logout(self) -> bool:
        """注销前确认 —— 会断网，别手滑。"""
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle("确认注销")
        box.setIcon(QMessageBox.Question)
        box.setText("注销会断开当前校园网连接。")
        box.setInformativeText(
            "如果你是想在手机上用校园网，注销后等 1~2 分钟再让手机连，"
            "服务端需要一点时间释放会话。\n\n继续吗？")
        yes = box.addButton("注销", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def _on_manual_logout_captcha(self, img: bytes, auto_text: str) -> None:
        """注销的人工兜底（与登录同一套跨线程问答机制）。"""
        if self._logout is None or not self._logout.isRunning():
            if self._logout is not None:
                self._logout.submit_answer(None)
            return
        dlg = CaptchaDialog(
            img, auto_text,
            math_mode=False,          # 注销验证码是 4 位字符，不是算式
            timeout_s=DEFAULT_TIMEOUT,
            cancel_fn=lambda: bool(self._logout and self._logout.cancelled),
            parent=self,
        )
        try:
            answer = dlg.exec_answer()
        except Exception:
            log.exception("注销验证码对话框异常")
            answer = None
        if self._logout is not None:
            self._logout.submit_answer(answer)

    def _on_logout_result(self, res: LoginResult) -> None:
        """注销结果处理 —— 注意：成功不自动重连。"""
        if res.ok:
            self.pill.set_state(NetworkState.NEED_LOGIN)
            self.set_status(res.message, "ok")
            self.tray.notify("CampusLink", res.message or "已注销")
            # 主动触发一次状态检测，让 pill 尽快反映"需要认证"
            if self._monitor:
                self._monitor.request_check()
        else:
            # 注销失败不改状态（可能还连着）
            self.set_status(res.message, "err")
            if res.attempts:
                self.lb_status.setText(f"{res.message}　（共尝试 {res.attempts} 次）")

    def _on_logout_thread_done(self) -> None:
        self._set_logout_busy(False)

    def _set_logout_busy(self, busy: bool) -> None:
        self.btn_logout.setVisible(not busy)
        self.btn_connect.setEnabled(not busy)
        self.btn_check.setEnabled(not busy)
        for w in (self.ed_student, self.ed_account, self.ed_password,
                  self.cb_operator, self.ck_save):
            w.setEnabled(not busy)
        if busy:
            self.btn_cancel.setVisible(True)
            self.btn_cancel.setText("取消注销")
            try:
                self.btn_cancel.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.btn_cancel.clicked.connect(self.cancel_logout)
        else:
            self.btn_cancel.setVisible(False)
            self.btn_cancel.setText("取消")
            try:
                self.btn_cancel.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.btn_cancel.clicked.connect(self.cancel_connect)
            self.btn_logout.setVisible(True)

    def cancel_logout(self) -> None:
        if self._logout and self._logout.isRunning():
            self._logout.cancel()
            self.set_status("正在取消注销…", "warn")

    # ================================================================ 网络
    def check_now(self) -> None:
        self.pill.set_state(NetworkState.CHECKING)
        self.set_status("正在检测网络状态…", "info")
        if self._monitor:
            self._monitor.request_check()

    def _start_monitor(self) -> None:
        self._monitor = MonitorWorker(self.cfg.check_interval, self)
        self._monitor.stateChanged.connect(self._on_state_changed)
        self._monitor.needLoginDetected.connect(self._on_dropped)
        self._monitor.start()

    def _on_state_changed(self, value: str) -> None:
        try:
            state = NetworkState(value)
        except ValueError:
            state = NetworkState.UNKNOWN
        # 认证进行中时不要用后台检测结果覆盖界面
        if self._auth and self._auth.isRunning():
            return
        self.pill.set_state(state)
        if state is NetworkState.ONLINE:
            self.set_status("网络已连通", "ok")
        elif state is NetworkState.NEED_LOGIN:
            self.set_status("检测到需要认证，可点击「立即连接」", "warn")

    def _on_dropped(self) -> None:
        """从「已联网」变成「需要认证」= 掉线了。"""
        if not self.cfg.auto_connect:
            self.set_status("网络已断开，勾选「断线自动重新认证」可自动恢复", "warn")
            return
        if self._auth and self._auth.isRunning():
            return
        if not self.current_credentials().is_complete():
            self.set_status("网络已断开，但账号信息不完整，无法自动重连", "warn")
            return
        log.info("检测到掉线，自动重新认证")
        self.set_status("检测到掉线，正在自动重新认证…", "info")
        self.start_connect()

    def _start_warmup(self) -> None:
        self._warmup = WarmupWorker(self, self.profile.captcha_mode)
        self._warmup.start()

    # ================================================================ 杂项
    def set_status(self, text: str, level: str = "info") -> None:
        color = {"ok": style.OK, "warn": style.WARN,
                 "err": style.ERR, "info": style.TEXT_MUTED}.get(level, style.TEXT_MUTED)
        self.lb_status.setStyleSheet(f"color: {color}; font-size: 13px;")
        self.lb_status.setText(text)

    def _toggle_password(self) -> None:
        if self.ed_password.echoMode() == QLineEdit.Password:
            self.ed_password.setEchoMode(QLineEdit.Normal)
            self.btn_eye.setText("隐藏")
        else:
            self.ed_password.setEchoMode(QLineEdit.Password)
            self.btn_eye.setText("显示")

    def open_logs(self) -> None:
        d = paths.log_dir()
        try:
            if hasattr(__import__("os"), "startfile"):
                __import__("os").startfile(str(d))     # Windows
        except Exception:
            pass
        self.set_status(f"日志目录：{d}", "info")

    def show_about(self) -> None:
        QMessageBox.information(
            self, "关于 CampusLink",
            f"<b>CampusLink 校园网认证</b>　v{__version__}<br><br>"
            f"门户：{self.profile.name or '未配置'}<br>"
            f"密码存储：{secrets.status_text()}<br>"
            f"配置文件：{paths.config_file()}<br><br>"
            f"本程序只用于你自己的账号，请勿用于他人账号。")

    # ---------------------------------------------------------------- 托盘
    def hide_to_tray(self) -> None:
        self.hide()
        self.tray.show()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.notify("CampusLink 仍在运行",
                             "已最小化到托盘，双击图标可重新打开窗口")

    def show_from_tray(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def changeEvent(self, event) -> None:
        # 点最小化按钮 -> 进托盘（可配置）
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            if self.cfg.minimize_to_tray and self.tray.available():
                QTimer.singleShot(0, self.hide_to_tray)
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        if self._quitting or not self.cfg.close_to_tray or not self.tray.available():
            self._shutdown()
            event.accept()
            return
        event.ignore()
        self.hide_to_tray()

    def quit_app(self) -> None:
        try:
            self._fixer.stop()
        except Exception:
            pass
        self._quitting = True
        self._shutdown()
        QGuiApplication.quit()

    def _shutdown(self) -> None:
        """收尾：停线程、存配置。不能留悬挂线程，否则退出时会崩。"""
        log.info("正在退出")
        self._persist()
        self._stop_node()
        self._stop_watcher()
        for th in (self._auth, self._monitor, self._warmup):
            try:
                if th and th.isRunning():
                    if isinstance(th, AuthWorker):
                        th.cancel()
                    elif isinstance(th, MonitorWorker):
                        th.stop()
                    th.wait(2500)
            except Exception:
                pass
        try:
            self.tray.hide()
        except Exception:
            pass
