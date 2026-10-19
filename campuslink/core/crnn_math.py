# -*- coding: utf-8 -*-
"""
算式验证码的专用模型推理后端（CRNN + CTC）—— CampusLink 2.0 识别引擎。

## 它在识别链路里的位置

    math 模式的识别顺序（`ocr_backend._recognize_math`）：
      1. 本模块（CRNN 模型，独立测试集 92.2%）—— 有模型就走
      2. `ocr.recognize_math()` 传统启发式（13%）—— 模型不可用时兜底

模型文件、onnxruntime、甚至这个模块本身出任何问题，结果都是「静默降级到
启发式」，不允许变成新的故障点。

## 三条铁律

1. **失败一律降级**：模型缺失 / onnxruntime 缺失 / onnx 损坏 / 推理异常 /
   图片解码失败 → 返回空串。本模块不得抛异常打断登录。
2. **预处理与训练侧逐参数一致**（`ml/scripts/common.py`）：灰度 → BILINEAR
   缩到 200x32 → [-1,1] → 补 batch/channel 维。常量在两边各存一份
   （主仓不能反向依赖 `ml/`），一致性由 `tests/test_crnn_backend.py` 锁。
3. **推理结果不做 `max_b_digits` 截断**：模型输出的是干净的 `A<op>B`，
   真出现两位操作数就是真的（减法 64.4% 的 B 是两位）。

## 顶层只 import 标准库

`campuslink.core` 要能在 OpenWrt（mipsel、无 numpy/onnxruntime）上 import，
numpy / PIL / onnxruntime 全部在方法内部延迟导入。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

# ------------------------------------------------------------------ 与训练侧对齐
#
# 这组常量是**契约**，必须与 ml/scripts/common.py 完全一致。
# 之所以不复用（不让 core 反向依赖 ml/），是因为 ml/ 是给 Linux 训练机用的
# 独立工程；一致性由测试守（tests/test_crnn_backend.py 直接读 common.py 比对）。
CHARS = "0123456789加减乘除"          # 12 类
BLANK_IDX = 0                          # CTC blank 恒为 0
IDX_TO_CHAR = {i + 1: c for i, c in enumerate(CHARS)}

IMG_H = 32
IMG_W = 200

# ------------------------------------------------------------------ 模型定位
#: 模型文件默认位置（相对任一候选根）：`ml/models/crnn_math.onnx`
MODEL_RELPATH = ("ml", "models", "crnn_math.onnx")

#: 打包后的简化布局：直接把 `models/crnn_math.onnx` 放在 exe 旁边也能找到
MODEL_RELPATH_FLAT = ("models", "crnn_math.onnx")

#: 环境变量覆盖（模型想放在别处时用这个指）
ENV_MODEL = "CAMPUSLINK_CRNN_MODEL"

_INFER_PROVIDERS = ["CPUExecutionProvider"]


def _candidate_roots() -> list[Path]:
    """
    按优先级列出可能放着模型文件的根目录。

    这个程序有三种运行形态 ——
      1. 源码直接跑：模型在仓库的 `ml/models/` 下
      2. PyInstaller onedir：模块 `__file__` 在 `_internal/` 里，`_MEIPASS` 即 `_internal`
      3. 装完的 exe：运维可能把 onnx 放在 exe 同级或 `_internal/` 下
    只认「从 `__file__` 往上找」一种，第 3 种就永远找不到模型，而失败是
    静默降级 —— 表现为「模型没生效」，非常难查。
    """
    roots: list[Path] = []

    try:
        import sys

        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass))
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            roots.append(exe_dir)
            roots.append(exe_dir / "_internal")
    except Exception:
        pass

    roots.extend(Path(__file__).resolve().parents)

    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = str(r).lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def find_model_path(explicit: str | os.PathLike | None = None) -> Path | None:
    """
    找模型文件，优先级：显式参数 > 环境变量 > 各候选根下的默认位置。

    找不到返回 None —— 这是**正常状态**，不是错误。
    """
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None

    env = os.environ.get(ENV_MODEL)
    if env:
        p = Path(env)
        if p.is_file():
            return p
        # 环境变量指向不存在的文件：不要静默回落到仓库里的旧模型，
        # 否则「想换模型却发现没生效」会变成极难排查的问题。
        return None

    for root in _candidate_roots():
        for rel in (MODEL_RELPATH, MODEL_RELPATH_FLAT):
            cand = root.joinpath(*rel)
            if cand.is_file():
                return cand
    return None


def onnxruntime_available() -> bool:
    """onnxruntime 是否可导入。缺失时整条模型链路关闭，不影响启发式。"""
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


def available(explicit: str | os.PathLike | None = None) -> bool:
    """模型链路是否就绪（文件在 且 运行时在）。"""
    return find_model_path(explicit) is not None and onnxruntime_available()


# ============================================================ 预处理 / 解码
def preprocess(img_bytes: bytes):
    """
    图片字节 → 模型输入 [1, 1, 32, 200]，值域 [-1, 1]。

    与训练侧 `preprocess_pil` 逐参数一致。任何失败返回 None。
    """
    if not img_bytes:
        return None
    try:
        import io

        import numpy as np
        from PIL import Image
    except Exception:
        return None

    try:
        img = Image.open(io.BytesIO(img_bytes))
        img.load()
    except Exception:
        return None

    try:
        img = img.convert("L").resize((IMG_W, IMG_H), Image.Resampling.BILINEAR)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - 0.5) / 0.5
        return arr[None, None, :, :]
    except Exception:
        return None


def softmax_probs(logits):
    """logits → 概率（数值稳定版）。失败返回 None。"""
    try:
        import numpy as np
        x = np.asarray(logits, dtype=np.float32)
        x = x - x.max(axis=-1, keepdims=True)
        e = np.exp(x)
        return e / e.sum(axis=-1, keepdims=True)
    except Exception:
        return None


def ctc_greedy_decode(logits) -> tuple[str, float]:
    """
    CTC 贪心解码：去 blank、去连续重复，同时给出**最小字符置信度**。

    置信度口径：最终保留的每个字符在其被选中那一帧的概率，取最小值。
    这是「最弱环节」的度量 —— 一个字符没把握整条就没把握。
    返回 (文本, min_conf)；文本为空时 min_conf = 0。
    """
    probs = softmax_probs(logits)
    if probs is None:
        return "", 0.0
    if probs.ndim == 3:
        probs = probs[0]
    if probs.ndim != 2:
        return "", 0.0

    out: list[str] = []
    confs: list[float] = []
    prev = -1
    for t, k in enumerate(probs.argmax(axis=1)):
        k = int(k)
        if k != BLANK_IDX and k != prev:
            ch = IDX_TO_CHAR.get(k, "")
            if ch:
                out.append(ch)
                confs.append(float(probs[t, k]))
        prev = k

    text = "".join(out)
    if not confs:
        return "", 0.0
    return text, min(confs)


# ============================================================ 识别器
class CrnnMathRecognizer:
    """
    持有 onnxruntime 会话，把验证码图片识别成算式原文（如 `7加9`）+ 置信度。

    只负责识别，不求值 —— 求值交给 `captcha_math.solve()`。
    会话懒加载：进程启动不碰 onnxruntime，第一次识别时才付加载成本。
    """

    def __init__(self, model_path: str | os.PathLike | None = None):
        self._path = find_model_path(model_path)
        self._session = None
        self._input_name = ""
        self._lock = threading.Lock()
        self._load_error = ""

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def load_error(self) -> str:
        return self._load_error

    def ready(self) -> bool:
        """文件在 且 运行时在。不代表推理一定成功（那要真跑一次）。"""
        return self._path is not None and onnxruntime_available()

    def _get_session(self):
        if self._session is not None:
            return self._session
        with self._lock:
            if self._session is not None:      # 双检：并发下只建一次
                return self._session
            if self._path is None:
                self._load_error = "未找到模型文件"
                return None
            if not onnxruntime_available():
                self._load_error = "onnxruntime 未安装"
                return None
            try:
                import onnxruntime as ort

                opts = ort.SessionOptions()
                opts.log_severity_level = 3    # 只报 error，避免刷屏
                self._session = ort.InferenceSession(
                    str(self._path), sess_options=opts,
                    providers=_INFER_PROVIDERS,
                )
                self._input_name = self._session.get_inputs()[0].name
            except Exception as exc:            # 损坏的 onnx / 版本不匹配
                self._load_error = f"{type(exc).__name__}: {exc}"
                return None
        return self._session

    def recognize(self, img_bytes: bytes) -> tuple[str, float, dict[str, str]]:
        """
        → (算式原文, 最小字符置信度 0~1, 诊断信息)

        失败一律返回 `("", 0.0, detail)`，detail 带 `_reason`。
        调用方看到空串就走降级，不需要区分失败原因。
        """
        sess = self._get_session()
        if sess is None:
            return "", 0.0, {"_reason": self._load_error or "模型不可用"}

        arr = preprocess(img_bytes)
        if arr is None:
            return "", 0.0, {"_reason": "图片无法解码"}

        # 输入名在 `_get_session` 里回填；若会话是外部注入的（测试/替换实现），
        # 这里是空的，现取一次即可 —— 不能把空串当 key 喂给 run。
        name = self._input_name
        if not name:
            try:
                name = sess.get_inputs()[0].name
            except Exception:
                name = "input"
            self._input_name = name

        try:
            out = sess.run(None, {name: arr})[0]
        except Exception as exc:
            return "", 0.0, {"_reason": f"推理失败 {type(exc).__name__}"}

        text, conf = ctc_greedy_decode(out)
        return text, conf, {}


# ============================================================ 单例
_singleton_lock = threading.Lock()
_singleton: CrnnMathRecognizer | None = None
_singleton_not_ready = False


def get_recognizer(refresh: bool = False) -> CrnnMathRecognizer | None:
    """
    取进程级单例；不可用（无模型/无运行时）时返回 None。

    缓存「不可用」：每次登录都会问一次，没模型时没必要每问都扫目录。
    `refresh=True` 供测试用。
    """
    global _singleton, _singleton_not_ready
    with _singleton_lock:
        if refresh:
            _singleton = None
            _singleton_not_ready = False
        if _singleton_not_ready:
            return None
        if _singleton is None:
            rec = CrnnMathRecognizer()
            if not rec.ready():
                _singleton_not_ready = True
                return None
            _singleton = rec
        return _singleton


def reset_singleton() -> None:
    """测试用：清掉缓存，让下次调用重新探测。"""
    get_recognizer(refresh=True)
