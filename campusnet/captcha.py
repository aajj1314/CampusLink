"""算式验证码识别：CRNN + CTC，ONNX CPU 推理。"""

from __future__ import annotations

import io
import re
from pathlib import Path

import numpy as np
from PIL import Image

# 与训练侧逐字符绑定，顺序不可修改：0 是 CTC blank，1-12 才是字符
CHARS = "0123456789加减乘除"
IDX_TO_CHAR = {index + 1: char for index, char in enumerate(CHARS)}
EXPR_RE = re.compile(r"(\d{1,2})([加减乘除+\-*/×÷])(\d{1,2})")


class CaptchaSolver:
    def __init__(self, model_path: str, confidence_threshold: float = 0.9):
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"验证码模型不存在：{path}")

        # 延迟导入 onnxruntime，缩短无需识别的启动路径
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.log_severity_level = 3
        self._session = ort.InferenceSession(
            str(path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._input_name = self._session.get_inputs()[0].name
        self.threshold = float(confidence_threshold)

    @staticmethod
    def preprocess(png: bytes) -> np.ndarray:
        # 参数与训练侧完全一致：灰度 -> 200x32 双线性 -> [-1, 1] -> NCHW
        image = Image.open(io.BytesIO(png)).convert("L").resize(
            (200, 32), Image.Resampling.BILINEAR
        )
        array = np.asarray(image, dtype=np.float32) / 255.0
        array = (array - 0.5) / 0.5
        return array[None, None, :, :]

    @staticmethod
    def decode(logits: np.ndarray) -> tuple[str, float]:
        scores = logits.astype(np.float32)
        if scores.ndim == 3:
            scores = scores[0]
        scores = scores - scores.max(axis=-1, keepdims=True)
        probs = np.exp(scores)
        probs /= probs.sum(axis=-1, keepdims=True)

        text: list[str] = []
        confidences: list[float] = []
        previous = -1
        for step, index in enumerate(probs.argmax(axis=1)):
            index = int(index)
            if index != 0 and index != previous:
                char = IDX_TO_CHAR.get(index)
                if char:
                    text.append(char)
                    confidences.append(float(probs[step, index]))
            previous = index

        return "".join(text), (min(confidences) if confidences else 0.0)

    @staticmethod
    def solve(expression: str) -> str | None:
        match = EXPR_RE.fullmatch(expression)
        if not match:
            return None

        left, operator, right = int(match.group(1)), match.group(2), int(match.group(3))
        if operator in ("加", "+"):
            result = left + right
        elif operator in ("减", "-"):
            result = left - right
        elif operator in ("乘", "*", "×"):
            result = left * right
        else:
            if right == 0 or left % right != 0:
                return None
            result = left // right

        # 门户输入框 maxlength=4，超出范围的识别结果直接放弃
        value = str(result)
        return value if len(value) <= 4 else None

    def read(self, png: bytes) -> tuple[str, float]:
        logits = self._session.run(None, {self._input_name: self.preprocess(png)})[0]
        return self.decode(logits)

    def answer(self, png: bytes) -> tuple[str, float, str | None]:
        expression, confidence = self.read(png)
        answer = self.solve(expression) if confidence >= self.threshold else None
        return expression, confidence, answer
