# -*- coding: utf-8 -*-
"""
math 模式识别链路的接口与语义测试（2.0：模型引擎，无启发式）。

这里**不**断言具体识别率 —— 真实准确率取决于门户当前的干扰强度，
属于需要人工验证的指标（见 docs/design/08-验证码改版应对.md）。
本文件锁的是更容易出错、也更重要的东西：

  · `mode` 切换不影响字符型验证码的既有行为（回归保护）
  · `math` 模式返回的是**答案**，求解失败时返回空串而不是瞎猜
  · text 模式在识别依赖（ddddocr/opencv）缺失时优雅降级，不崩溃

1.x 的启发式识别测试（分块投票、实例隔离、字符集等）已随引擎移除，
历史实现见 git 历史（<= f08d403）。
"""
from __future__ import annotations

import pytest

from campuslink.core.ocr_backend import LocalDdddocrBackend


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
        from campuslink.core import ocr

        def _boom(*a, **kw):
            raise AssertionError("math 模式不应调用 ocr.recognize")

        monkeypatch.setattr(ocr, "recognize", _boom)
        r = LocalDdddocrBackend(mode="math").recognize(b"x", length=4,
                                                       strategies=("raw",))
        # 模型在（本地已训练）→ 正常识别；模型不在 → 空串换图。
        # 无论哪种，都绝不能触达 text 管线（上面 _boom 已保证）。
        assert isinstance(r.text, str)

    def test_math_mode_returns_answer(self, monkeypatch):
        """识别成功 → 返回计算结果，confidence 为真实连续值。"""
        from campuslink.core import crnn_math

        class _R:
            def recognize(self, _img):
                return "6乘12", 0.97, {}

        monkeypatch.setattr(crnn_math, "get_recognizer", lambda: _R())
        r = LocalDdddocrBackend(mode="math").recognize(b"x", length=4,
                                                       strategies=("raw",))
        assert r.text == "72"
        assert r.confidence == pytest.approx(0.97)
        assert r.detail["_expr"] == "6乘12"
        assert r.detail["_answer"] == "72"

    def test_math_mode_failure_returns_empty(self):
        """
        求解失败必须返回空串。

        返回空串 → Authenticator 换一张图重试（不提交）；
        若返回一个猜的数字 → 必然提交错的答案，白白消耗登录尝试。
        """
        r = LocalDdddocrBackend(mode="math").recognize(b"", length=4,
                                                       strategies=("raw",))
        assert r.text == ""
        assert r.confidence == 0.0


# ============================================================ text 模式可选依赖
class TestTextModeOptionalDeps:
    """
    2.0 起 ddddocr / opencv 是可选依赖。

    用假模块模拟「未安装」环境：`ocr.recognize` 内部 `import ddddocr`
    失败抛 ImportError，backend 必须优雅降级而不是崩溃。
    """

    def test_missing_dep_returns_friendly_empty(self, monkeypatch):
        from campuslink.core import ocr

        def _no_ddddocr(*a, **kw):
            raise ImportError("No module named 'ddddocr'")

        monkeypatch.setattr(ocr, "recognize", _no_ddddocr)
        r = LocalDdddocrBackend(mode="text").recognize(
            b"img", length=4, strategies=("raw",))
        assert r.text == "" and r.source == "local"
        assert "字符识别引擎不可用" in r.detail["_reason"]

    def test_missing_dep_does_not_raise(self, monkeypatch):
        from campuslink.core import ocr

        def _raise(*a, **kw):
            raise ImportError("No module named 'ddddocr'")

        monkeypatch.setattr(ocr, "recognize", _raise)
        # 重复调用也不崩（每次登录都会走一遍）
        be = LocalDdddocrBackend(mode="text")
        for _ in range(3):
            r = be.recognize(b"img", length=4, strategies=("raw",))
            assert r.text == ""


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
