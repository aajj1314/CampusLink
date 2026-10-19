# -*- coding: utf-8 -*-
"""
CRNN 模型推理后端（`campuslink.core.crnn_math`）的契约测试 —— 2.0 识别引擎。

锁的不是「模型准不准」（那要真模型真数据，见 `ml/scripts/evaluate.py`），
而是**接线是否可靠**：

  · 与训练侧 `ml/scripts/common.py` 的常量/预处理**不许漂移**
  · 模型缺失 / 运行时缺失 / 推理异常 → 必须静默降级到启发式，不许成为新故障点
  · 模型给的算式**不许做 max_b_digits 截断**（减法 64.4% 的 B 是两位）
  · confidence 是真实的连续值（门限靠它），不是二值
  · text 模式与启发式路径的行为与 1.x 一致

模型文件不存在时这些测试照样全绿 —— 用假会话/假识别器验证接线。
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from campuslink.core import crnn_math, ocr
from campuslink.core.ocr_backend import ENGINE_CRNN, ENGINE_HEURISTIC, LocalDdddocrBackend

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMON_PY = REPO_ROOT / "ml" / "scripts" / "common.py"


# ============================================================ 与训练侧对齐
class TestTrainingSideContract:
    """训练在 Linux、推理在 Windows，常量两边各存一份 —— 漂移就在这里炸。"""

    def _const(self, name: str) -> str:
        src = COMMON_PY.read_text(encoding="utf-8")
        m = re.search(rf"^{name}\s*=\s*(.+?)\s*(?:#.*)?$", src, re.M)
        assert m, f"ml/scripts/common.py 里找不到常量 {name}"
        return m.group(1).strip()

    def test_charset_identical(self):
        assert self._const("CHARS") == f'"{crnn_math.CHARS}"'

    def test_blank_idx_identical(self):
        assert int(self._const("BLANK_IDX")) == crnn_math.BLANK_IDX

    def test_image_height_identical(self):
        assert int(self._const("IMG_H")) == crnn_math.IMG_H

    def test_image_width_identical(self):
        assert int(self._const("IMG_W")) == crnn_math.IMG_W

    def test_index_map_matches_charset(self):
        """索引必须 1 基（0 留给 CTC blank），否则输出整体错位一格。"""
        assert crnn_math.IDX_TO_CHAR[1] == crnn_math.CHARS[0]
        assert len(crnn_math.IDX_TO_CHAR) == len(crnn_math.CHARS)
        assert crnn_math.BLANK_IDX not in crnn_math.IDX_TO_CHAR


# ============================================================ 模型定位
class TestFindModelPath:
    def test_missing_explicit_returns_none(self, tmp_path):
        assert crnn_math.find_model_path(tmp_path / "nope.onnx") is None

    def test_existing_explicit(self, tmp_path):
        p = tmp_path / "m.onnx"
        p.write_bytes(b"fake")
        assert crnn_math.find_model_path(p) == p

    def test_env_overrides(self, tmp_path, monkeypatch):
        p = tmp_path / "from_env.onnx"
        p.write_bytes(b"fake")
        monkeypatch.setenv(crnn_math.ENV_MODEL, str(p))
        assert crnn_math.find_model_path() == p

    def test_env_pointing_to_nothing_does_not_fall_back(self, tmp_path, monkeypatch):
        """环境变量指错了必须返回 None，不能悄悄用旧模型。"""
        monkeypatch.setenv(crnn_math.ENV_MODEL, str(tmp_path / "gone.onnx"))
        assert crnn_math.find_model_path() is None

    def test_finds_model_under_meipass(self, tmp_path, monkeypatch):
        """PyInstaller onedir：靠 `_MEIPASS` 兜。"""
        import sys
        p = tmp_path / "ml" / "models" / "crnn_math.onnx"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"fake")
        monkeypatch.delenv(crnn_math.ENV_MODEL, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert crnn_math.find_model_path() == p

    def test_finds_flat_model_layout(self, tmp_path, monkeypatch):
        """装完后把 onnx 丢在 `models/` 下也该能用。"""
        import sys
        p = tmp_path / "models" / "crnn_math.onnx"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"fake")
        monkeypatch.delenv(crnn_math.ENV_MODEL, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert crnn_math.find_model_path() == p

    def test_candidate_roots_deduplicated(self):
        roots = crnn_math._candidate_roots()
        assert roots
        assert len({str(r).lower() for r in roots}) == len(roots)


# ============================================================ 可用性探测
class TestAvailability:
    def test_not_available_without_model(self, monkeypatch, tmp_path):
        monkeypatch.setenv(crnn_math.ENV_MODEL, str(tmp_path / "none.onnx"))
        assert crnn_math.available() is False

    def test_recognizer_none_without_model(self, monkeypatch, tmp_path):
        monkeypatch.setenv(crnn_math.ENV_MODEL, str(tmp_path / "none.onnx"))
        crnn_math.reset_singleton()
        assert crnn_math.get_recognizer() is None
        crnn_math.reset_singleton()

    def test_singleton_cached_when_unavailable(self, monkeypatch, tmp_path):
        """「不可用」也要缓存，否则每次登录都扫一遍目录。"""
        monkeypatch.setenv(crnn_math.ENV_MODEL, str(tmp_path / "none.onnx"))
        crnn_math.reset_singleton()
        assert crnn_math.get_recognizer() is None
        assert crnn_math.get_recognizer() is None
        crnn_math.reset_singleton()


# ============================================================ 预处理
def _png_bytes(color: int = 255, size: tuple[int, int] = (200, 60)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("L", size, color).save(buf, format="PNG")
    return buf.getvalue()


class TestPreprocess:
    def test_shape(self):
        arr = crnn_math.preprocess(_png_bytes())
        assert arr.shape == (1, 1, crnn_math.IMG_H, crnn_math.IMG_W)

    def test_dtype_and_range(self):
        import numpy as np
        arr = crnn_math.preprocess(_png_bytes())
        assert arr.dtype == np.float32
        assert -1.0 <= float(arr.min()) and float(arr.max()) <= 1.0

    def test_direction(self):
        assert float(crnn_math.preprocess(_png_bytes(255)).mean()) == pytest.approx(1.0, abs=0.02)
        assert float(crnn_math.preprocess(_png_bytes(0)).mean()) == pytest.approx(-1.0, abs=0.02)

    def test_empty_and_garbage(self):
        assert crnn_math.preprocess(b"") is None
        assert crnn_math.preprocess(b"not a png") is None

    def test_resizes_any_input(self):
        arr = crnn_math.preprocess(_png_bytes(size=(320, 90)))
        assert arr.shape == (1, 1, crnn_math.IMG_H, crnn_math.IMG_W)


# ============================================================ CTC 解码 + 置信度
class TestCtcDecode:
    def _logits(self, rows: list[int], t: int = 8, c: int = 13, hi: float = 10.0):
        import numpy as np
        arr = np.zeros((t, c), dtype=np.float32)
        for i, idx in enumerate(rows):
            if idx:
                arr[i, idx] = hi
        return arr

    def test_simple(self):
        text, conf = crnn_math.ctc_greedy_decode(self._logits([8, 11, 10]))
        assert text == "7加9"
        assert conf == pytest.approx(1.0, abs=1e-3)   # logit 10 → ≈0.9995

    def test_min_confidence_is_weakest_char(self):
        """置信度取最弱环节：两个字符概率 1.0 和 0.6 → min=0.6。"""
        import numpy as np
        arr = np.zeros((8, 13), dtype=np.float32)
        arr[0, 8] = 20.0                        # '7'，softmax ≈ 1.0
        arr[1, 11] = 2.89                       # e^2.89/(e^2.89+12) ≈ 0.6
        text, conf = crnn_math.ctc_greedy_decode(arr)
        assert text == "7加"
        assert conf == pytest.approx(0.6, abs=0.02)

    def test_collapses_repeats(self):
        assert crnn_math.ctc_greedy_decode(self._logits([8, 8, 8, 11]))[0] == "7加"

    def test_blank_separates_repeats(self):
        assert crnn_math.ctc_greedy_decode(self._logits([1, 0, 1]))[0] == "00"

    def test_all_blank(self):
        text, conf = crnn_math.ctc_greedy_decode(self._logits([]))
        assert text == "" and conf == 0.0

    def test_batch_and_bad_input(self):
        import numpy as np
        arr = self._logits([8, 11, 10])[None, :, :]
        assert crnn_math.ctc_greedy_decode(arr)[0] == "7加9"
        assert crnn_math.ctc_greedy_decode([1, 2, 3])[0] == ""
        assert crnn_math.ctc_greedy_decode(None)[0] == ""


# ============================================================ 识别器容错
class _FakeSession:
    def __init__(self, logits, name="input"):
        self._logits = logits
        self._name = name

    def get_inputs(self):
        class _I:
            name = self._name
        return [_I()]

    def run(self, _outputs, feeds):
        assert self._name in feeds
        return [self._logits]


class TestRecognizerTolerance:
    def test_no_model_returns_empty_not_raise(self, tmp_path):
        rec = crnn_math.CrnnMathRecognizer(tmp_path / "none.onnx")
        text, conf, detail = rec.recognize(_png_bytes())
        assert text == "" and conf == 0.0
        assert detail["_reason"]

    def test_corrupt_model_returns_empty(self, tmp_path):
        bad = tmp_path / "broken.onnx"
        bad.write_bytes(b"this is not an onnx file")
        rec = crnn_math.CrnnMathRecognizer(bad)
        text, conf, detail = rec.recognize(_png_bytes())
        assert text == "" and detail["_reason"]

    def test_bad_image_after_session(self, monkeypatch):
        rec = crnn_math.CrnnMathRecognizer()
        monkeypatch.setattr(rec, "_get_session", lambda: _FakeSession(None))
        text, conf, detail = rec.recognize(b"not an image")
        assert text == ""
        assert detail["_reason"] == "图片无法解码"

    def test_inference_exception_returns_empty(self, monkeypatch):
        rec = crnn_math.CrnnMathRecognizer()

        class _Boom:
            def get_inputs(self):
                class _I:
                    name = "input"
                return [_I()]

            def run(self, *a, **kw):
                raise RuntimeError("boom")

        monkeypatch.setattr(rec, "_get_session", lambda: _Boom())
        text, conf, detail = rec.recognize(_png_bytes())
        assert text == "" and "推理失败" in detail["_reason"]

    def test_success_path(self, monkeypatch):
        import numpy as np
        logits = np.zeros((8, 13), dtype=np.float32)
        logits[0, 8] = 10.0      # '7'
        logits[1, 11] = 10.0     # '加'
        logits[2, 10] = 10.0     # '9'
        rec = crnn_math.CrnnMathRecognizer()
        monkeypatch.setattr(rec, "_get_session", lambda: _FakeSession(logits))
        text, conf, detail = rec.recognize(_png_bytes())
        assert text == "7加9" and conf == pytest.approx(1.0, abs=1e-3)
        assert detail == {}


# ============================================================ 后端接线（三级引擎）
class _FakeRecognizer:
    """假识别器：返回固定 (expr, conf, detail)。"""

    def __init__(self, expr, conf=0.95):
        self._expr = expr
        self._conf = conf

    def recognize(self, _img):
        return self._expr, self._conf, {}


class TestBackendWiring:
    def _fake_get(self, expr, conf=0.95):
        rec = _FakeRecognizer(expr, conf)
        return lambda: rec, rec

    def test_model_takes_priority(self, monkeypatch):
        get, rec = self._fake_get("7加9")
        monkeypatch.setattr(crnn_math, "get_recognizer", get)
        r = LocalDdddocrBackend(mode="math").recognize(_png_bytes(), length=4,
                                                       strategies=("raw",))
        assert r.text == "16"                     # 不截断：模型说 7加9 就是 7加9
        assert r.confidence == pytest.approx(0.95)
        assert r.detail["_engine"] == ENGINE_CRNN
        assert r.detail["_expr"] == "7加9"
        assert r.source == "local-math"

    def test_model_answer_not_truncated(self, monkeypatch):
        """减法两位 B：55减23 = 32，截断会算成 53。"""
        get, _ = self._fake_get("55减23")
        monkeypatch.setattr(crnn_math, "get_recognizer", get)
        r = LocalDdddocrBackend(mode="math").recognize(_png_bytes(), length=4,
                                                       strategies=("raw",))
        assert r.text == "32"

    def test_model_gives_up_falls_back_to_heuristic(self, monkeypatch):
        get, _ = self._fake_get("")
        monkeypatch.setattr(crnn_math, "get_recognizer", get)
        monkeypatch.setattr(ocr, "recognize_math",
                            lambda img, **kw: ("42", {"bin152": "ok"}))
        r = LocalDdddocrBackend(mode="math").recognize(_png_bytes(), length=4,
                                                       strategies=("raw",))
        assert r.text == "42"
        assert r.detail["_engine"] == ENGINE_HEURISTIC
        assert r.confidence == 1.0                # 启发式恢复二值语义

    def test_model_expr_unparseable_falls_back(self, monkeypatch):
        get, _ = self._fake_get("乱码")
        monkeypatch.setattr(crnn_math, "get_recognizer", get)
        monkeypatch.setattr(ocr, "recognize_math",
                            lambda img, **kw: ("42", {}))
        r = LocalDdddocrBackend(mode="math").recognize(_png_bytes(), length=4,
                                                       strategies=("raw",))
        assert r.text == "42" and r.detail["_engine"] == ENGINE_HEURISTIC

    def test_no_model_uses_heuristic(self, monkeypatch, tmp_path):
        monkeypatch.setenv(crnn_math.ENV_MODEL, str(tmp_path / "none.onnx"))
        crnn_math.reset_singleton()
        monkeypatch.setattr(ocr, "recognize_math",
                            lambda img, **kw: ("5", {}))
        r = LocalDdddocrBackend(mode="math").recognize(_png_bytes(), length=4,
                                                       strategies=("raw",))
        assert r.text == "5" and r.detail["_engine"] == ENGINE_HEURISTIC
        crnn_math.reset_singleton()

    def test_text_mode_untouched_by_crnn(self, monkeypatch):
        """字符型验证码绝不能因为模型链路改变行为。"""
        monkeypatch.setattr(crnn_math, "get_recognizer",
                            lambda: pytest.fail("text 模式不应触碰 CRNN"))
        monkeypatch.setattr(ocr, "recognize", lambda img, **kw: ("ab12", {"raw": "ab12"}))
        monkeypatch.setattr(ocr, "confidence", lambda t, d: 0.9)
        r = LocalDdddocrBackend(mode="text").recognize(_png_bytes(), length=4,
                                                       strategies=("raw",))
        assert r.text == "ab12" and r.source == "local"

    def test_engine_marks_distinct(self):
        assert ENGINE_CRNN != ENGINE_HEURISTIC


# ============================================================ 顶层不引入重依赖
class TestImportPurity:
    def test_top_level_imports_are_stdlib_only(self):
        """`campuslink.core` 要能在 OpenWrt（无 numpy/onnxruntime）import。"""
        src = (REPO_ROOT / "campuslink" / "core" / "crnn_math.py").read_text(
            encoding="utf-8")
        head = src.split('"""', 2)[-1]        # 跳过模块 docstring
        for line in head.splitlines():
            if line[:1] in (" ", "\t"):
                continue                       # 函数内的延迟导入是设计
            if re.match(r"^(import|from)\s+(numpy|onnxruntime|PIL)\b", line.strip()):
                pytest.fail(f"顶层不应 import 重依赖：{line!r}")
