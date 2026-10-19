# -*- coding: utf-8 -*-
"""
验证码人工输入对话框。

为什么需要它：算式型验证码的自动识别只有约 13% 正确率，而且认错了会
提交一个错误答案、白白消耗一次登录尝试。人工看图输入是 100% 准确的兜底，
也是模型达标之前让客户端"能用起来"的最短路径。

设计要点：

  · 纯展示 + 输入，不含任何识别逻辑 —— 识别永远是 core 的事
  · 自带超时：用户不在电脑前时不能让登录流程永远挂起，
    超时按「跳过这张」处理，让认证循环去换一张图
  · 支持外部取消：无人值守的自动重连里，用户点了主界面的「取消」，
    对话框要能自己关掉，而不是留着一个模态框挡住整个界面
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

from ..logging_setup import get_logger

log = get_logger("captcha_dialog")

#: 对话框默认等待时长（秒）。登录重试间隔 1.5s，人在电脑前几秒就能填完；
#: 超时太长会让「断线自动重连」在没人时卡住，太短又不够用户反应。
DEFAULT_TIMEOUT = 90


class CaptchaDialog(QDialog):
    """
    显示一张验证码图片，等用户输入答案。

    用法（主线程）::

        dlg = CaptchaDialog(img, auto_text="",
                            math_mode=True, parent=self)
        answer = dlg.exec_answer()      # str | None
    """

    def __init__(self, img: bytes, auto_text: str = "", *,
                 math_mode: bool = False,
                 timeout_s: int = DEFAULT_TIMEOUT,
                 cancel_fn=None, parent: QWidget | None = None):
        """
        cancel_fn : 可选，返回 True 表示外部要求放弃整个登录。
                    对话框会周期性检查，一旦为 True 就自行关闭。
        """
        super().__init__(parent)
        self._answer: str | None = None
        self._cancelled = False
        self._cancel_fn = cancel_fn
        self._deadline = time.monotonic() + max(10, int(timeout_s))
        self._math_mode = math_mode

        self.setWindowTitle("请输入验证码")
        self.setModal(True)
        self.setMinimumWidth(340)

        box = QVBoxLayout(self)
        box.setContentsMargins(18, 16, 18, 14)
        box.setSpacing(10)

        self.tip = QLabel("请输入图中等式的计算结果" if math_mode else "请输入图中的字符")
        self.tip.setObjectName("SectionTitle")
        box.addWidget(self.tip)

        self.pic = QLabel()
        self.pic.setAlignment(Qt.AlignCenter)
        pm = QPixmap()
        if img and pm.loadFromData(img):
            # 2x 放大显示：原图 200x60，字太小，看不清细笔画
            self.pic.setPixmap(pm.scaled(pm.width() * 2, pm.height() * 2,
                                         Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))
        else:
            # 理论上到不了这里（core 保证 img 非空才识别），兜一手
            self.pic.setText("（验证码图片加载失败，请跳过换一张）")
        box.addWidget(self.pic)

        self.ed_answer = QLineEdit()
        self.ed_answer.setPlaceholderText(
            "例如 16（算式 7加9 的结果）" if math_mode else "例如 a1b2")
        self.ed_answer.setMaxLength(16)
        self.ed_answer.returnPressed.connect(self._submit)
        box.addWidget(self.ed_answer)

        if auto_text:
            hint = QLabel(f"自动识别结果：{auto_text}（仅供参考，通常不可信）")
        else:
            hint = QLabel("自动未能识别这张图。填好后回车提交；"
                          "点「换一张」跳过。")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        box.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_skip = QPushButton("换一张")
        self.btn_skip.setObjectName("Ghost")
        self.btn_skip.setCursor(Qt.PointingHandCursor)
        self.btn_skip.clicked.connect(self._skip)
        row.addWidget(self.btn_skip)
        row.addStretch(1)
        self.btn_ok = QPushButton("提交")
        self.btn_ok.setObjectName("Primary")
        self.btn_ok.setCursor(Qt.PointingHandCursor)
        self.btn_ok.clicked.connect(self._submit)
        row.addWidget(self.btn_ok)
        box.addLayout(row)

        self.lb_countdown = QLabel("")
        self.lb_countdown.setObjectName("Hint")
        box.addWidget(self.lb_countdown)

        # 超时 + 外部取消，都用一个低频定时器轮询（0.5s，开销可忽略）
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    # ---------------------------------------------------------------- 槽
    def _submit(self) -> None:
        text = self.ed_answer.text().strip()
        if not text:
            # 空输入按「跳过」处理，和"没填"语义一致，不会提交空答案
            self._skip()
            return
        self._answer = text
        self._timer.stop()
        self.accept()

    def _skip(self) -> None:
        self._answer = None
        self._timer.stop()
        self.reject()

    def _poll(self) -> None:
        # 外部要求取消整个登录 → 直接放弃这张
        if self._cancel_fn is not None:
            try:
                if self._cancel_fn():
                    self._cancelled = True
                    self._timer.stop()
                    self.reject()
                    return
            except Exception:
                pass
        left = int(self._deadline - time.monotonic())
        if left <= 0:
            log.info("验证码输入超时，按跳过处理")
            self._skip()
            return
        self.lb_countdown.setText(f"{left}s 内不输入将自动换一张")

    # ---------------------------------------------------------------- 对外
    def exec_answer(self) -> "str | None":
        """
        显示对话框并返回结果：用户提交的答案，或 None（跳过/超时/外部取消）。
        """
        self.exec()
        if self._cancelled:
            raise _DialogCancelled()
        return self._answer


class _DialogCancelled(Exception):
    """内部信号：用户在主界面取消了登录，调用方应放弃本次询问。"""
