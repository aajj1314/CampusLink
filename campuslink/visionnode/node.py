# -*- coding: utf-8 -*-
"""
视觉节点主循环 —— 长轮询领任务 → 本地识别 → 提交结果。

职责（任务书 Agent 2 · 工作内容 2）：
  · 领任务 → `LocalDdddocrBackend.recognize()` → 提交（带 nonce）
  · 退出 / 网络错误时**不留悬空任务**：节点持有的 assigned 任务会被主动取消，
    让 Router 把它重新入队或转给其它节点 / 人工

纯 Python 线程（不依赖 Qt），状态通过 `on_event(kind, payload)` 回调上报，
GUI 层再转成 Qt 信号 —— 这样本模块可脱离 GUI 单测。
"""
from __future__ import annotations

import threading
from typing import Callable

from .client import NodeClient, NodeError

EventFn = Callable[[str, dict], None]


class VisionNode:
    """一个视觉节点。start() 起守护线程，stop() 停止并取消持有的任务。"""

    def __init__(self, client: NodeClient, backend=None, *, poll_wait: float = 25.0,
                 on_event: EventFn | None = None):
        self._client = client
        self._backend = backend or _default_backend()
        self._poll_wait = poll_wait
        self._on_event = on_event
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._held: dict | None = None          # 当前持有的任务
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="vision-node")
        self._thread.start()
        self._emit("started", {})

    def stop(self) -> None:
        self._stop.set()
        # 主动取消当前持有的任务，避免 Router 侧留悬空 assigned
        with self._lock:
            held = self._held
        if held:
            self._release(held["task_id"])
        if self._thread:
            self._thread.join(timeout=2.0)
        self._emit("stopped", {})

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------ 主循环
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                task = self._client.next_task(wait=self._poll_wait)
            except NodeError:
                # 网络抖动：退避一秒再试，避免打爆 Router
                if self._stop.wait(1.0):
                    break
                continue
            if task is None:          # 长轮询超时，无任务
                continue
            with self._lock:
                self._held = task
            try:
                self._process(task)
            finally:
                with self._lock:
                    self._held = None

    def _process(self, task: dict) -> None:
        task_id = task["task_id"]
        self._emit("claimed", {"task_id": task_id})

        try:
            img = self._client.get_image(task["image_url"])
        except NodeError as exc:
            self._emit("error", {"message": f"取图失败：{exc}"})
            self._release(task_id)
            return

        try:
            result = self._backend.recognize(
                img, length=task["length"],
                strategies=tuple(task.get("strategies_hint") or ("raw", "gray")))
        except Exception:
            result = None

        if result is None or not result.text:
            # 识别不出：主动放弃，让任务回 pending 转给其它节点 / 人工
            self._emit("skipped", {"task_id": task_id})
            self._release(task_id)
            return

        try:
            accepted = self._client.submit_result(
                task_id, task["nonce"], result.text, "ocr",
                result.confidence, result.detail, result.elapsed_ms)
            self._emit("submitted", {"task_id": task_id, "text": result.text,
                                     "accepted": accepted})
        except NodeError as exc:
            self._emit("error", {"message": f"提交失败：{exc}"})
            self._release(task_id)

    # ------------------------------------------------------------ 工具
    def _release(self, task_id: str) -> None:
        """尽力取消任务（节点主动放弃）。失败吞掉 —— 由 Router 兜底重派。"""
        try:
            self._client.cancel_task(task_id)
        except Exception:
            pass

    def _emit(self, kind: str, payload: dict) -> None:
        if self._on_event:
            try:
                self._on_event(kind, payload)
            except Exception:
                pass


def _default_backend():
    from campuslink.core.ocr_backend import LocalDdddocrBackend
    return LocalDdddocrBackend()
