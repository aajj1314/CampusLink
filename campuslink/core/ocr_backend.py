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

    `mode` 决定走哪条链路：
        "text" —— 只做字符识别，行为与历史版本**逐字段一致**（默认值）
        "math" —— 只做算式求解，解不出就返回空串（让上层换图）
    之所以默认 "text"：既有 44 条稳定版测试与字符型门户都依赖原行为，
    新形态必须由 profile 显式声明，不能靠猜。
    """

    def __init__(self, mode: str = "text"):
        self._mode = mode if mode in ("text", "math") else "text"

    def recognize(self, image: bytes, *, length: int,
                  strategies: tuple[str, ...]) -> OcrResult:
        import time as _t
        started = _t.monotonic()

        if self._mode == "math":
            return self._recognize_math(image, started)

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

    def _recognize_math(self, image: bytes, started: float) -> OcrResult:
        """
        算式验证码：返回的 text 是**计算结果**，不是识别出来的算式原文。

        求解失败时返回空 text —— 上层（Authenticator）看到空串会换一张图
        重试，而不是拿一个猜出来的数字去提交。这个区分很重要：
        提交错误答案会白白消耗一次登录尝试。
        """
        import time as _t
        from . import ocr

        answer, detail = ocr.recognize_math(image)
        return OcrResult(
            text=answer,
            confidence=1.0 if answer else 0.0,
            detail=detail,
            source="local-math",
            elapsed_ms=max(0, int((_t.monotonic() - started) * 1000)),
        )

