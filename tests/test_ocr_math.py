# -*- coding: utf-8 -*-
"""
算式验证码识别链路（OCR 层）的接口与语义测试。

这里**不**断言具体识别率 —— 真实准确率取决于门户当前的干扰强度，
属于需要人工验证的指标（见 docs/design/08-验证码改版应对.md）。
本文件锁的是更容易出错、也更重要的东西：

  · `mode` 切换不会影响字符型验证码的既有行为（回归保护）
  · `math` 模式返回的是**答案**，求解失败时返回空串而不是瞎猜
  · 算式用的 OCR 实例与字符验证码的实例**互相隔离**
"""
from __future__ import annotations

import pytest

from campuslink.core import ocr
from campuslink.core.ocr_backend import LocalDdddocrBackend


# ============================================================ 实例隔离
class TestMathOcrIsolation:
    """
    限定字符集用的是 `set_ranges`，这是**实例级**状态。
    如果在共享实例上调用，字符型验证码的识别会被一起改掉 ——
    那是既有 44 条稳定版测试覆盖的路径，不能碰。
    """

    def test_math_instance_is_separate(self):
        assert ocr.get_math_ocr(False) is not ocr.get_ocr(False)

    def test_math_instance_cached(self):
        assert ocr.get_math_ocr(False) is ocr.get_math_ocr(False)

    def test_charset_excludes_letters(self):
        """
        字符集里不能有 ASCII 字母。

        'x' 会被算成乘号（见 captcha_math.OP_ALIASES），但如果模型能自由输出
        其它字母，那些就是纯噪声。注意 str.isalpha() 对汉字也返回 True，
        所以这里只看 ASCII 字母。
        """
        assert not any(c.isascii() and c.isalpha() for c in ocr.MATH_CHARSET)

    def test_charset_covers_expected(self):
        for ch in "0123456789加减乘除÷":
            assert ch in ocr.MATH_CHARSET


# ============================================================ mode 语义
class TestLocalBackendMode:
    def test_default_mode_is_text(self):
        """默认必须是 text：既有字符型门户靠它，不能被新逻辑影响。"""
        assert LocalDdddocrBackend()._mode == "text"

    def test_unknown_mode_falls_back_to_text(self):
        assert LocalDdddocrBackend(mode="nonsense")._mode == "text"

    def test_text_mode_source(self):
        be = LocalDdddocrBackend(mode="text")
        r = be.recognize(b"", length=4, strategies=("raw",))
        assert r.source == "local"

    def test_math_mode_source(self):
        be = LocalDdddocrBackend(mode="math")
        r = be.recognize(b"", length=4, strategies=("raw",))
        assert r.source == "local-math"

    def test_math_mode_does_not_call_text_pipeline(self, monkeypatch):
        """math 模式不该再走字符识别链路（否则会把算式原文当答案提交）。"""
        called = {"text": 0}

        def _boom(*a, **kw):
            called["text"] += 1
            raise AssertionError("math 模式不应调用 ocr.recognize")

        monkeypatch.setattr(ocr, "recognize", _boom)
        monkeypatch.setattr(ocr, "recognize_math", lambda img, **kw: ("12", {"_answer": "12"}))
        r = LocalDdddocrBackend(mode="math").recognize(b"x", length=4, strategies=("raw",))
        assert r.text == "12"
        assert called["text"] == 0

    def test_math_mode_returns_answer(self, monkeypatch):
        monkeypatch.setattr(ocr, "recognize_math",
                            lambda img, **kw: ("72", {"_expr": "6乘12", "_answer": "72"}))
        r = LocalDdddocrBackend(mode="math").recognize(b"x", length=4, strategies=("raw",))
        assert r.text == "72"
        assert r.confidence == 1.0
        assert r.detail["_expr"] == "6乘12"

    def test_math_mode_failure_returns_empty(self, monkeypatch):
        """
        求解失败必须返回空串。

        返回空串 → Authenticator 换一张图重试（不提交）；
        若返回一个猜的数字 → 必然提交错的答案，白白消耗登录尝试。
        """
        monkeypatch.setattr(ocr, "recognize_math",
                            lambda img, **kw: ("", {"bin152": "64123 -> None"}))
        r = LocalDdddocrBackend(mode="math").recognize(b"x", length=4, strategies=("raw",))
        assert r.text == ""
        assert r.confidence == 0.0


# ============================================================ recognize_math 直调
class TestRecognizeMath:
    def test_empty_bytes(self):
        ans, detail = ocr.recognize_math(b"")
        assert ans == ""
        assert isinstance(detail, dict)

    def test_garbage_bytes(self):
        ans, _detail = ocr.recognize_math(b"not an image at all")
        assert ans == ""

    def test_returns_str(self):
        ans, detail = ocr.recognize_math(b"")
        assert isinstance(ans, str)
        assert all(isinstance(k, str) and isinstance(v, str)
                   for k, v in detail.items())


# ============================================================ profile 接线
class TestProfileWiring:
    def test_default_captcha_mode_is_text(self):
        from campuslink.core.models import PortalProfile
        assert PortalProfile().captcha_mode == "text"

    def test_builtin_profile_is_math(self):
        """本门户已经改成算式形态，档案必须显式声明。"""
        from campuslink import profiles
        p = profiles.load_profile("桂林信息科技学院")
        assert p.captcha_mode == "math"

    def test_captcha_mode_roundtrip(self):
        from campuslink.core.models import PortalProfile
        p = PortalProfile(captcha_mode="math")
        assert PortalProfile.from_dict(p.to_dict()).captcha_mode == "math"

    def test_authenticator_passes_mode_to_backend(self):
        from campuslink.core.authenticator import Authenticator
        from campuslink.core.models import PortalProfile
        auth = Authenticator(PortalProfile(captcha_mode="math"))
        assert auth._backend()._mode == "math"

    def test_authenticator_default_is_text(self):
        from campuslink.core.authenticator import Authenticator
        from campuslink.core.models import PortalProfile
        auth = Authenticator(PortalProfile())
        assert auth._backend()._mode == "text"
