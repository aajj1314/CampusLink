# -*- coding: utf-8 -*-
"""
视觉节点 —— Desktop 作为 Router 的「视觉面」。

把现有 ddddocr 识别能力暴露给 Router：连接 Router → 配对 → 长轮询领任务 →
本地识别 → 提交结果。纯 Python（不依赖 Qt），GUI 通过回调转 Qt 信号集成。
"""
from __future__ import annotations

from .client import NodeClient, NodeError
from .node import VisionNode
from .watcher import RouterWatcher

__all__ = ["NodeClient", "NodeError", "VisionNode", "RouterWatcher"]
