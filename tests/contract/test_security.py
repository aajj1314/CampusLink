# -*- coding: utf-8 -*-
"""
安全契约测试（06 文档 §2.4）：PIN 爆破锁定、token 只存哈希、限速、吊销。
"""
from __future__ import annotations

import requests

from campuslink.router import PairingManager
from tests.contract.conftest import scan_forbidden


class TestPinLockout:
    def test_five_wrong_pins_lock_5_min(self):
        pm = PairingManager()
        pm.start_pairing()
        for i in range(4):
            try:
                pm.confirm("999999", device_name="x", device_kind="mobile",
                           capabilities=[])
            except Exception as e:
                assert e.code == "E_BAD_REQUEST"
        # 第 5 次错 → 锁定
        try:
            pm.confirm("999999", device_name="x", device_kind="mobile",
                       capabilities=[])
            assert False, "第 5 次错误应触发锁定"
        except Exception as e:
            assert e.code == "E_RATE_LIMITED"
        # 锁定期间，正确 PIN 也被拒
        try:
            pm.confirm("000000", device_name="x", device_kind="mobile",
                       capabilities=[])
            assert False
        except Exception as e:
            assert e.code == "E_RATE_LIMITED"


class TestTokenStorage:
    def test_token_stored_only_as_sha256(self):
        pm = PairingManager()
        pin = pm.start_pairing()["pin"]
        d = pm.confirm(pin, device_name="x", device_kind="mobile",
                       capabilities=["ocr"])
        dev = pm._devices[d["device_id"]]
        assert d["device_token"] not in str(dev.token_hash)
        assert dev.token_hash != d["device_token"].encode()
        # public() 里绝不含 token 明文
        assert "device_token" not in str(dev.public())
        assert scan_forbidden(dev.public()) == []

    def test_wrong_token_returns_none(self):
        pm = PairingManager()
        pin = pm.start_pairing()["pin"]
        d = pm.confirm(pin, device_name="x", device_kind="mobile", capabilities=[])
        assert pm.authenticate(d["device_token"]) is not None
        assert pm.authenticate(d["device_token"] + "x") is None


class TestRateLimit:
    def test_auth_endpoint_rate_limited(self, router):
        # 用全新设备，避免影响其它用例的 shared token
        pin = router.post("/api/v1/pair/start").json()["pin"]
        tok = router.post("/api/v1/pair/confirm", json={
            "pin": pin, "device_name": "限速测试", "device_kind": "desktop",
            "capabilities": []}).json()["device_token"]
        h = {"Authorization": "Bearer " + tok}
        codes = [router.get("/api/v1/auth/status", headers=h).status_code
                 for _ in range(11)]
        assert codes[:10] == [200] * 10
        assert codes[10] == 429, "第 11 次应触发 429"


class TestRevoke:
    def test_revoke_then_old_token_401(self, router):
        pin = router.post("/api/v1/pair/start").json()["pin"]
        d = router.post("/api/v1/pair/confirm", json={
            "pin": pin, "device_name": "x", "device_kind": "desktop",
            "capabilities": []}).json()
        h = {"Authorization": "Bearer " + d["device_token"]}
        assert router.get("/api/v1/auth/status", headers=h).status_code == 200
        assert router.delete(f"/api/v1/pair/devices/{d['device_id']}",
                             headers=h).status_code == 204
        assert router.get("/api/v1/auth/status", headers=h).status_code == 401


class TestSensitiveScan:
    def test_all_error_bodies_clean(self, router):
        """错误响应体同样不得含敏感字样。"""
        bodies = [
            router.get("/api/v1/auth/status").json(),          # 401
            router.get("/api/v1/vision/tasks/cap_deadbeef/image",
                       headers={"Authorization": "Bearer bad"}).json(),  # 401
        ]
        for b in bodies:
            assert scan_forbidden(b) == []
