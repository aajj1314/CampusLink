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

#: math 模式的识别引擎标记（写进 `OcrResult.detail["_engine"]`，诊断用）。
#: 不新增 `OcrResult.source` 取值，避免动已冻结的契约（见 contract-change-log.md）。
ENGINE_CRNN = "crnn"            # 专用 CRNN+CTC 模型（2.0 默认引擎）
ENGINE_HEURISTIC = "heuristic"  # 传统图像处理启发式（模型不可用时兜底）


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

        两条链路，顺序固定：
          1. 专用 CRNN 模型（`crnn_math`，独立测试集 92.2%）—— 有模型才走，
             confidence 填真实的最小字符置信度（Authenticator 据此做门限）
          2. 传统启发式（`ocr.recognize_math`，实测约 13%）—— 模型不可用时兜底，
             confidence 恢复二值语义（1.0/0.0）
        没有模型时行为与 1.x 完全一致。
        """
        import time as _t
        from . import captcha_math, crnn_math, ocr

        # ---------- 1) 专用模型 ----------
        recognizer = crnn_math.get_recognizer()
        if recognizer is not None:
            expr, conf, cdetail = recognizer.recognize(image)
            if expr:
                # 注意：这里**不传** max_b_digits。模型输出的是干净的 A<op>B，
                # 减法 64.4% 的 B 是两位，截断会把 55减23 算成 55减2。
                model_answer = captcha_math.solve(expr)
                if model_answer:
                    cdetail["_engine"] = ENGINE_CRNN
                    cdetail["_expr"] = expr
                    cdetail["_answer"] = model_answer
                    return OcrResult(
                        text=model_answer,
                        confidence=max(0.0, min(1.0, conf)),
                        detail=cdetail,
                        source="local-math",
                        elapsed_ms=max(0, int((_t.monotonic() - started) * 1000)),
                    )
            # 模型放弃（没认出 / 算式不合法）→ 落到启发式，别让它空手而归

        # ---------- 2) 启发式兜底 ----------
        answer, detail = ocr.recognize_math(image)
        detail.setdefault("_engine", ENGINE_HEURISTIC)
        return OcrResult(
            text=answer,
            confidence=1.0 if answer else 0.0,
            detail=detail,
            source="local-math",
            elapsed_ms=max(0, int((_t.monotonic() - started) * 1000)),
        )

