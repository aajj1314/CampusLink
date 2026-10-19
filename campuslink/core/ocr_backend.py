# -*- coding: utf-8 -*-
"""
OCR 后端抽象 —— 任务书 §A1 冻结接口的权威落点。

把「验证码识别」从 `Authenticator` 里解耦成一个可注入的接口：
  · 本地实现 `LocalDdddocrBackend`：字符型门户走 `core/ocr.recognize`
    （ddddocr，**2.0 起为可选依赖**），算式门户走 CRNN 模型（core/crnn_math.py）
  · 远程实现 `RemoteVisionBackend`（在 `campuslink/router/remotecomplete.py`）：
    走视觉任务 API

关键点：`LocalDdddocrBackend` **延迟导入** `core.ocr`（在 recognize 内部），
这样本模块顶层只依赖标准库 —— `import campuslink.core` 不再连带
cv2/numpy/ddddocr，OpenWrt 上才能 import core（视觉计算下沉到 Desktop/Mobile）。

2.0 依赖策略：算式门户的识别由专用模型承担，`ddddocr` 与 `opencv` 已从
必要依赖中剥离（打包产物不再包含）。它们只在**字符型门户**（text 模式）
被使用，缺失时返回友好错误，不影响算式门户的任何功能。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

#: math 模式的识别引擎标记（写进 `OcrResult.detail["_engine"]`，诊断用）。
#: 2.0 起 math 模式只有模型引擎；启发式引擎已随 ddddocr 剥离一并移除
#: （13% 正确 / 17% 错答，期望收益为负，历史实现见 git <= f08d403）。
ENGINE_CRNN = "crnn"


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
    本地 OCR 后端。

    `mode` 决定走哪条链路：
        "text" —— 字符型门户：ddddocr 多策略识别 + 投票。
                  ddddocr / opencv 为可选依赖，缺失时返回空结果并带
                  `_reason`（上层按「没认出来」换图，最终给出明确失败）。
        "math" —— 算式门户：CRNN 专用模型（`core/crnn_math.py`），
                  返回的 text 是**计算结果**；模型不可用时返回空串
                  （上层换图），绝不用低质引擎编造答案。
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
        try:
            text, detail = ocr.recognize(image, strategies=strategies, length=length)
        except ImportError as exc:
            # ddddocr / opencv 未安装（2.0 起为可选依赖）。
            # 返回空结果 → 上层换图重试到 max_retry 用完，登录以失败告终，
            # 日志里有明确原因。不抛异常——字符型门户的失败不该变成崩溃。
            return OcrResult(
                text="",
                confidence=0.0,
                detail={"_reason": f"字符识别引擎不可用（{exc}）；"
                                   "本版本已精简 ddddocr，算式门户不受影响"},
                source="local",
                elapsed_ms=max(0, int((_t.monotonic() - started) * 1000)),
            )
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

        2.0 起本链路只有 CRNN 模型一个引擎（`core/crnn_math.py`）：
          · 模型读出算式 + 求值成功 → 提交，confidence 为真实的最小字符置信度
          · 模型读出算式但被求值策略拒绝（除不尽/除零）→ 换图（安全）
          · 模型弃权 / 模型不可用 → 换图
        传统启发式（ddddocr 分块投票，13% 正确 / 17% 错答）已移除：
        模型弃权时交给它重读，错答的代价大于偶尔救回一张的收益。
        """
        import time as _t
        from . import captcha_math, crnn_math

        recognizer = crnn_math.get_recognizer()
        if recognizer is None:
            return OcrResult(
                text="",
                confidence=0.0,
                detail={"_engine": ENGINE_CRNN,
                        "_reason": "模型不可用（文件缺失或 onnxruntime 未安装）"},
                source="local-math",
                elapsed_ms=max(0, int((_t.monotonic() - started) * 1000)),
            )

        expr, conf, cdetail = recognizer.recognize(image)
        if not expr:
            cdetail["_engine"] = ENGINE_CRNN
            cdetail.setdefault("_reason", "模型输出为空")
            return OcrResult(
                text="",
                confidence=0.0,
                detail=cdetail,
                source="local-math",
                elapsed_ms=max(0, int((_t.monotonic() - started) * 1000)),
            )

        # 注意：这里**不传** max_b_digits。模型输出的是干净的 A<op>B，
        # 减法 64.4% 的 B 是两位，截断会把 55减23 算成 55减2。
        answer = captcha_math.solve(expr)
        if answer is None:
            # 算式被求值策略拒绝（除不尽 / 除零 / 解析失败）：
            # 算式本身可信（模型 92%），换图是正确处置，不是识别失败。
            cdetail["_engine"] = ENGINE_CRNN
            cdetail["_expr"] = expr
            cdetail["_reason"] = "算式被求值策略拒绝（除不尽/除零），换图"
            return OcrResult(
                text="",
                confidence=0.0,
                detail=cdetail,
                source="local-math",
                elapsed_ms=max(0, int((_t.monotonic() - started) * 1000)),
            )

        cdetail["_engine"] = ENGINE_CRNN
        cdetail["_expr"] = expr
        cdetail["_answer"] = answer
        return OcrResult(
            text=answer,
            confidence=max(0.0, min(1.0, conf)),
            detail=cdetail,
            source="local-math",
            elapsed_ms=max(0, int((_t.monotonic() - started) * 1000)),
        )
