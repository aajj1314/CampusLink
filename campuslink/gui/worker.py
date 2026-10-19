# -*- coding: utf-8 -*-
"""
后台工作线程：认证、网络监测、模型预热。

为什么必须开线程：
  · 一次认证要发好几个 HTTP 请求，还可能重试 8 次、每次间隔 1.5 秒 ——
    整个过程最长可能几十秒。放在主线程里界面会直接「未响应」。
  · 网络检测要发 HTTP 请求，最长等 6 秒，同样不能放主线程。
  · ddddocr 首次加载 ONNX 模型要 1～2 秒，也不能卡在窗口打开的时候。

线程与界面之间只通过 Qt 信号通信，不共享可变状态 —— 避免各种竞态。
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import Qt, QThread, Signal

from ..core.authenticator import Authenticator
from ..core.models import Credentials, LoginResult, NetworkState
from ..logging_setup import get_logger

log = get_logger("worker")


class AuthWorker(QThread):
    """
    执行一次认证。

    用 QThread 而不是 QThreadPool：认证是长任务且需要取消，
    独占一个线程语义更清楚。
    """

    progress = Signal(str)            # 一句可直接显示的中文状态
    result = Signal(object)           # LoginResult
    captcha_seen = Signal(bytes, str)  # (验证码图片, 识别结果)，界面可以显示出来
    #: 人工输入请求：工作线程 → 主线程弹对话框。参数 (图片, 自动识别结果)。
    manual_captcha = Signal(bytes, str)

    #: 默认等待用户输入的时长。要略大于对话框自身的超时，
    #: 保证「对话框先超时返回 None」这条正常路径不会先被 Event.wait 掐断。
    MANUAL_WAIT = 100

    def __init__(self, authenticator: Authenticator, credentials: Credentials,
                 parent=None):
        super().__init__(parent)
        self._auth = authenticator
        self._cred = credentials
        self._stop = threading.Event()
        # --- 人工输入问答的跨线程通道 ---
        self._ask_enabled = False
        self._answer_evt = threading.Event()
        self._answer: "str | None" = None
        self._manual_wait = self.MANUAL_WAIT

    def cancel(self) -> None:
        """请求中止。认证循环会在下一个检查点退出，通常 1 秒内。"""
        self._stop.set()
        # 若正阻塞在等人工输入，立刻放行（放行结果是 None → 换图 → 检查点退出）
        self._answer_evt.set()

    @property
    def cancelled(self) -> bool:
        return self._stop.is_set()

    def enable_manual_captcha(self, wait_s: int = MANUAL_WAIT) -> None:
        """打开「人工输入验证码」。必须在 start() 之前调用。"""
        self._ask_enabled = True
        # 下限 1s：真实场景建议 ≥15s（对话框自身 90s 超时兜底），
        # 但这里不硬卡下限 —— 测试和特殊场景需要更短的超时。
        self._manual_wait = max(1, int(wait_s))

    def submit_answer(self, text: "str | None") -> None:
        """主线程把对话框结果交回来（None = 跳过这张）。"""
        self._answer = text
        self._answer_evt.set()

    def _make_ask(self):
        """
        构造传给 core 的 ask_captcha 回调。

        运行在**工作线程**：发信号让主线程弹模态对话框，
        然后阻塞等主线程把答案交回来。这是唯一安全的跨线程问答方式 ——
        Qt 的 UI 只能在主线程动，而 core 的 login 是同步阻塞的。
        """
        def _ask(img: bytes, auto_text: str) -> "str | None":
            self._answer = None
            self._answer_evt.clear()
            self.manual_captcha.emit(img, auto_text)
            if not self._answer_evt.wait(self._manual_wait):
                log.info("等待人工输入超时（%ss），按跳过处理", self._manual_wait)
                return None
            return self._answer
        return _ask

    def run(self) -> None:
        ask = self._make_ask() if self._ask_enabled else None
        try:
            res: LoginResult = self._auth.login(
                self._cred,
                cancel=self._stop.is_set,
                on_progress=self.progress.emit,
                on_captcha=lambda img, text: self.captcha_seen.emit(img, text),
                ask_captcha=ask,
            )
        except Exception as exc:                      # 兜底，绝不让线程崩掉
            log.exception("认证线程异常")
            res = LoginResult(False, f"内部错误：{type(exc).__name__}",
                              NetworkState.UNKNOWN, detail=str(exc)[:200])
        self.result.emit(res)


class MonitorWorker(QThread):
    """
    周期性检测网络，用于「网络状态」显示和「断线自动重连」。

    只在状态**发生变化**时发信号，避免界面被高频刷新刷屏。
    用 100ms 粒度的分片休眠，这样 stop() 能很快生效。
    """

    stateChanged = Signal(str)        # NetworkState.value
    onlineDetected = Signal()         # 从「需要认证」变成「已联网」时发一次
    needLoginDetected = Signal()      # 掉线下线时发一次，主窗口据此决定要不要重连

    #: 主线程调 request_check() 时，会用这个内部信号唤醒休眠中的线程去立即检测。
    #: 跨线程不能直接调方法去改状态，走信号才安全。
    _wake = Signal()

    def __init__(self, interval_sec: int = 60, parent=None):
        super().__init__(parent)
        self._interval = max(10, int(interval_sec))
        self._stop = threading.Event()
        self._wake_flag = threading.Event()
        self._last: str | None = None
        self._wake.connect(self._wake_flag.set, Qt.QueuedConnection)

    def request_check(self) -> None:
        """让后台线程马上检测一次，而不是等到下一个周期。"""
        self._wake.emit()

    def set_interval(self, interval_sec: int) -> None:
        self._interval = max(10, int(interval_sec))

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        # 延迟一点再开始，让主窗口先画出来
        self._sleep(1.0)
        while not self._stop.is_set():
            try:
                # 延迟导入，避免主线程启动时就把 requests 全链拉起来
                from ..core import netdetect
                online = netdetect.is_online(timeout=5)
            except Exception:
                online = False

            state = NetworkState.ONLINE if online else NetworkState.NEED_LOGIN
            value = state.value
            if value != self._last:
                prev, self._last = self._last, value
                self.stateChanged.emit(value)
                if value == NetworkState.ONLINE.value:
                    self.onlineDetected.emit()
                elif value == NetworkState.NEED_LOGIN.value and prev is not None:
                    # 上一轮还是在线，这一轮掉了 —— 这才叫「断线」
                    self.needLoginDetected.emit()

            self._sleep(self._interval)

    def _sleep(self, seconds: float) -> None:
        """
        可被 stop() 或 request_check() 打断的休眠。

        用轮询而不是 Event.wait(timeout)，是为了同时听两个事件：
        「要停了」和「赶紧去检测一次」。轮询粒度 100ms，对界面手感足够。
        """
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._stop.is_set() or self._wake_flag.is_set():
                self._wake_flag.clear()
                return
            time.sleep(0.1)


class WarmupWorker(QThread):
    """
    后台预热 ddddocr 模型。

    首次加载 ONNX 要 1～2 秒，放在这里做，用户点「立即连接」时就是热启动。
    失败也无所谓 —— 真到识别的时候再加载就是了，所以异常直接吞掉。
    """

    done = Signal(bool)

    def run(self) -> None:
        ok = False
        try:
            from ..core import ocr
            ocr.warmup()
            ok = True
            log.info("验证码识别模型已预热")
        except Exception as exc:
            log.warning("模型预热失败（不影响使用）: %s", type(exc).__name__)
        self.done.emit(ok)
