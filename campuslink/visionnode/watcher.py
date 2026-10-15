# -*- coding: utf-8 -*-
"""
Router 可达性监测 —— `/health` 连续 3 次（间隔 2 s）失败 = 失联。

用于 Desktop 的降级决策（任务书 Agent 2 · 工作内容 2）：托管模式下
Router 失联 → 自动回独立模式；恢复 → 重新托管。状态变化经 `on_change`
回调上报（GUI 层转 Qt 信号），本模块纯 Python、可单测。
"""
from __future__ import annotations

import threading
from typing import Callable

from .client import NodeClient

ChangeFn = Callable[[bool], None]


class RouterWatcher:
    def __init__(self, client: NodeClient, *, interval: float = 2.0,
                 max_failures: int = 3, on_change: ChangeFn | None = None):
        self._client = client
        self._interval = max(0.5, float(interval))
        self._max_failures = max(1, int(max_failures))
        self._on_change = on_change
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reachable: bool = False   # 保守初始：未探测成功前不视为可达
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="router-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    @property
    def reachable(self) -> bool:
        with self._lock:
            return self._reachable

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _set_reachable(self, value: bool) -> None:
        changed = False
        with self._lock:
            if value != self._reachable:
                self._reachable = value
                changed = True
        if changed and self._on_change:
            try:
                self._on_change(value)
            except Exception:
                pass

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            ok = self._client.health()
            if ok:
                failures = 0
                self._set_reachable(True)
            else:
                failures += 1
                if failures >= self._max_failures:
                    self._set_reachable(False)
            # 可被打断的休眠（100ms 粒度）
            self._sleep(self._interval)

    def _sleep(self, seconds: float) -> None:
        import time
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._stop.is_set():
                return
            time.sleep(0.1)
