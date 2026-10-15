# -*- coding: utf-8 -*-
"""
RemoteVisionBackend —— 在 Router 侧实现 §A1 冻结的 `OcrBackend` 协议。

它把「识别一张验证码」这件事翻译成「在 TaskManager 里创建一个视觉任务，
然后同步等一个视觉节点领走、识别、提交结果」。

设计要点（03 文档 §2 / 任务书 §A1）：
  · 提交图像 → 同步等待结果（带超时）
  · 超时/失败返回空 text（source 仍标 "remote"），
    让 authenticator 现有的「换一张重试」逻辑自然接管
  · 本实现放在 `campuslink/router/`，不进 core

`OcrResult` / `OcrBackend` 的权威定义在 `core/ocr_backend.py`（§A1 落点，
Agent 2 交付），本文件直接导入，不自行复刻。
"""
from __future__ import annotations

import time

from campuslink.core.ocr_backend import OcrBackend, OcrResult

from .taskmanager import TaskManager, VisionTask


class RemoteVisionBackend:
    """
    走视觉任务 API 的 OCR 后端。认证流程本身是串行重试的，所以这里
    同步等待是安全的。等待超时上限略低于任务 TTL，避免等一个已经过期
    的任务。
    """

    def __init__(self, taskmanager: TaskManager, *, result_timeout: float = 60.0):
        self._tm = taskmanager
        self._result_timeout = result_timeout

    def recognize(self, image: bytes, *, length: int,
                  strategies: tuple[str, ...]) -> OcrResult:
        started = time.monotonic()
        task: VisionTask | None
        try:
            task = self._tm.create(
                image,
                expect_length=length,
                strategies_hint=tuple(strategies),
                allow_manual=True,        # 视觉节点识别失败时允许转人工
            )
        except Exception:
            task = None
        if task is None:
            return OcrResult(text="", confidence=0.0, detail={}, source="remote",
                             elapsed_ms=_elapsed(started))

        final = self._tm.wait_result(task.task_id, timeout=self._result_timeout)
        if final is None or final.result is None:
            return OcrResult(text="", confidence=0.0, detail={}, source="remote",
                             elapsed_ms=_elapsed(started))
        r = final.result
        return OcrResult(text=r.text, confidence=r.confidence, detail=dict(r.detail),
                         source="remote", elapsed_ms=int(r.elapsed_ms or _elapsed(started)))


def _elapsed(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
