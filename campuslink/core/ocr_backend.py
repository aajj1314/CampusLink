# -*- coding: utf-8 -*-
"""
OCR 后端抽象 —— 任务书 §A1 冻结接口的权威落点（所有者 Agent 2）。

把「验证码识别」从 `Authenticator` 里解耦成一个可注入的接口：
  · 本地实现 `LocalDdddocrBackend`：包装现有 `core/ocr.recognize/confidence`，
    行为与今天**逐字段一致**（这是回归的保证）。
  · 远程实现 `RemoteVisionBackend`（在 `campuslink/router/remotecomplete.py`，
    Agent 1 交付）：走视觉任务 API。

关键点：`LocalDdddocrBackend` **延迟导入** `core.ocr`（在 recognize 内部），
这样本模块顶层只依赖标准库 —— `import campuslink.core` 不再连带
cv2/numpy/ddddocr，OpenWrt 上才能 import core（视觉计算下沉到 Desktop/Mobile）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OcrResult:
    text: str                 # 识别文本；失败/超时为空串
    confidence: float         # 0.0~1.0；manual 来源恒为 1.0
    detail: dict[str, str]    # 各策略输出，诊断用，无敏感内容
    source: str               # "local" | "remote" | "manual"
    elapsed_ms: int = 0


class OcrBackend(Protocol):
    def recognize(self, image: bytes, *, length: int,
                  strategies: tuple[str, ...]) -> OcrResult: ...


class LocalDdddocrBackend:
    """
    本地 OCR：包装 `core/ocr.recognize/confidence`。

    recognize 返回的 `text` / `detail` / `confidence` 与旧的
    `ocr.recognize(...)` + `ocr.confidence(...)` 完全一致 ——
    `tests/test_ocr_backend.py` 逐字段验证。
    """

    def recognize(self, image: bytes, *, length: int,
                  strategies: tuple[str, ...]) -> OcrResult:
        import time as _t
        started = _t.monotonic()
        from . import ocr
        text, detail = ocr.recognize(image, strategies=strategies, length=length)
        confidence = ocr.confidence(text, detail)
        return OcrResult(
            text=text,
            confidence=confidence,
            detail=detail,
            source="local",
            elapsed_ms=max(0, int((_t.monotonic() - started) * 1000)),
        )
