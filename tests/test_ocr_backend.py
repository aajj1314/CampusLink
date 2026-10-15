# -*- coding: utf-8 -*-
"""
OCR 后端抽象测试（任务书 Agent 2 交付物）。

核心：LocalDdddocrBackend 与旧 `core.ocr.recognize/confidence` **逐字段一致**，
以及 Authenticator 的 `ocr_backend` 注入/默认行为。
"""
from __future__ import annotations

import hashlib
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from campuslink.core import ocr
from campuslink.core.authenticator import Authenticator
from campuslink.core.models import Credentials, PortalProfile
from campuslink.core.ocr_backend import (LocalDdddocrBackend, OcrResult,
                                         OcrBackend)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

STRATEGIES = ("raw", "gray")


@pytest.fixture(scope="module")
def captcha_png() -> bytes:
    return (FIXTURES / "captcha_sample.png").read_bytes()


# ============================================================ 逐字段一致
class TestLocalMatchesLegacy:
    def test_text_matches(self, captcha_png):
        legacy_text, _ = ocr.recognize(captcha_png, strategies=STRATEGIES, length=4)
        local = LocalDdddocrBackend().recognize(captcha_png, length=4,
                                                strategies=STRATEGIES)
        assert local.text == legacy_text

    def test_detail_matches(self, captcha_png):
        _, legacy_detail = ocr.recognize(captcha_png, strategies=STRATEGIES, length=4)
        local = LocalDdddocrBackend().recognize(captcha_png, length=4,
                                                strategies=STRATEGIES)
        assert local.detail == legacy_detail

    def test_confidence_matches(self, captcha_png):
        legacy_text, legacy_detail = ocr.recognize(captcha_png, strategies=STRATEGIES,
                                                  length=4)
        legacy_conf = ocr.confidence(legacy_text, legacy_detail)
        local = LocalDdddocrBackend().recognize(captcha_png, length=4,
                                                strategies=STRATEGIES)
        assert local.confidence == legacy_conf

    def test_source_is_local(self, captcha_png):
        local = LocalDdddocrBackend().recognize(captcha_png, length=4,
                                                strategies=STRATEGIES)
        assert local.source == "local"

    def test_empty_image_returns_empty(self):
        local = LocalDdddocrBackend().recognize(b"", length=4, strategies=STRATEGIES)
        assert local.text == ""
        assert local.confidence == 0.0


# ============================================================ Authenticator 注入
class TestAuthenticatorInjection:
    def _profile(self, url: str = "http://10.6.6.6/login") -> PortalProfile:
        return PortalProfile(name="t", portal_url=url, field_map={})

    def test_default_backend_is_local(self):
        auth = Authenticator(self._profile())
        assert isinstance(auth._backend(), LocalDdddocrBackend)

    def test_injected_backend_is_used(self):
        class _Stub:
            def recognize(self, image, *, length, strategies):
                return OcrResult(text="", confidence=0.0, detail={}, source="remote")
        stub = _Stub()
        auth = Authenticator(self._profile(), ocr_backend=stub)
        assert auth._backend() is stub

    def test_injected_backend_called_end_to_end(self):
        """注入的 backend 在真实 login 流程中被调用（而不是本地 OCR）。"""
        from tests.test_mock_e2e import _State, _make_handler, _profile

        base_img = (FIXTURES / "captcha_sample.png").read_bytes()
        expected = ocr.recognize(base_img, strategies=STRATEGIES, length=4)[0]

        state = _State()
        state.images = [base_img]
        state.expected = {hashlib.md5(base_img).hexdigest(): expected}
        state.user, state.pwd = "20240001", "test-password"

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/login"

        calls: list[int] = []

        class RecordingBackend:
            def recognize(self, image, *, length, strategies):
                calls.append(1)
                return OcrResult(text=expected, confidence=1.0, detail={},
                                 source="remote")

        profile = _profile(url)
        auth = Authenticator(profile, ocr_backend=RecordingBackend())
        res = auth.login(Credentials("20240001", "20240001", "test-password"))
        try:
            assert res.ok, f"应成功：{res.message} / {res.raw_message}"
            assert calls, "注入的 OCR backend 应被调用"
        finally:
            srv.shutdown()
            srv.server_close()


# ============================================================ 协议
class TestProtocol:
    def test_remote_vision_backend_satisfies_protocol(self):
        from campuslink.router import RemoteVisionBackend, TaskManager
        from tests.contract.conftest import make_png

        tm = TaskManager()
        backend = RemoteVisionBackend(tm, result_timeout=0.1)
        r = backend.recognize(make_png(80, 24), length=4, strategies=("raw", "gray"))
        assert isinstance(r, OcrResult)
        assert r.text == "" and r.source == "remote"

    def test_ocr_result_is_frozen_dataclass(self):
        r = OcrResult(text="a", confidence=1.0, detail={}, source="manual")
        with pytest.raises(Exception):
            r.text = "b"          # frozen：不可改
