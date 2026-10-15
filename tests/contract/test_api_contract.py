# -*- coding: utf-8 -*-
"""
API 契约测试 —— 02 文档全部端点，每个端点 ≥ 2 条。

配合 tests/contract/conftest.py 的 `router`（未配置认证的控制面）与
`paired`（一个已配对设备）夹具。认证 202/503 的用例用 function 级的
`configured_router`（指向不可达门户，后台线程快速失败，无网络依赖）。
"""
from __future__ import annotations

import pytest

from campuslink.core.models import Credentials, PortalProfile
from campuslink.router import ApiServer, AuthController, RouterContext
from campuslink.router.taskmanager import TaskManager

from tests.contract.conftest import make_png, scan_forbidden


# ============================================================ 服务能力
class TestCapabilities:
    def test_health_returns_200(self, router):
        r = router.get("/api/v1/health")
        assert r.status_code == 200

    def test_health_has_empty_body(self, router):
        r = router.get("/api/v1/health")
        assert r.content == b""

    def test_capabilities_required_fields(self, router):
        r = router.get("/api/v1/capabilities")
        assert r.status_code == 200
        d = r.json()
        assert d["protocol"] == "campuslink"
        assert d["api_version"] == "1.0"
        assert "router_version" in d
        assert d["auth"]["mode"] == "router"
        assert d["vision"]["accepting_nodes"] is True
        assert {"vision_tasks", "manual_input", "pairing"} <= set(d["features"])

    def test_capabilities_reports_pending_count(self, router, captcha_png):
        router.ctx.taskmanager.create(captcha_png, expect_length=4)
        d = router.get("/api/v1/capabilities").json()
        assert d["vision"]["pending_tasks"] == 1


# ============================================================ 配对
class TestPairing:
    def test_pair_start_pin_is_6_digits(self, router):
        d = router.post("/api/v1/pair/start").json()
        assert len(d["pin"]) == 6 and d["pin"].isdigit()
        assert "expires_at" in d and d["pair_url"] == "/api/v1/pair/confirm"

    def test_pair_start_pin_reuses_active_pin(self, router):
        a = router.post("/api/v1/pair/start").json()
        b = router.post("/api/v1/pair/start").json()
        assert a["pin"] == b["pin"], "60s 内的有效 PIN 应复用，避免旧 PIN 悬空"

    def test_pair_confirm_returns_token(self, router):
        pin = router.post("/api/v1/pair/start").json()["pin"]
        r = router.post("/api/v1/pair/confirm", json={
            "pin": pin, "device_name": "手机", "device_kind": "mobile",
            "capabilities": ["ocr", "manual"]})
        assert r.status_code == 200
        d = r.json()
        assert d["device_token"] and d["device_id"].startswith("dev_")
        assert d["token_expires_at"] is None

    def test_pair_confirm_wrong_pin_400(self, router):
        router.post("/api/v1/pair/start")
        r = router.post("/api/v1/pair/confirm", json={
            "pin": "000000", "device_name": "x", "device_kind": "mobile"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "E_BAD_REQUEST"

    def test_pair_devices_lists_paired(self, router, paired):
        r = router.get("/api/v1/pair/devices", headers=paired["headers"])
        assert r.status_code == 200
        ids = [d["id"] for d in r.json()["devices"]]
        assert paired["device_id"] in ids

    def test_pair_devices_requires_auth(self, router):
        assert router.get("/api/v1/pair/devices").status_code == 401

    def test_revoke_device_then_401(self, router, paired):
        r = router.delete(f"/api/v1/pair/devices/{paired['device_id']}",
                          headers=paired["headers"])
        assert r.status_code == 204
        # 吊销后旧 token 立即失效
        assert router.get("/api/v1/auth/status",
                          headers=paired["headers"]).status_code == 401


# ============================================================ 认证会话
class TestAuthStatus:
    def test_requires_auth(self, router):
        assert router.get("/api/v1/auth/status").status_code == 401

    def test_state_and_account_masked(self, router, paired):
        r = router.get("/api/v1/auth/status", headers=paired["headers"])
        assert r.status_code == 200
        d = r.json()
        assert d["state"] in ("unknown", "offline", "need_login", "online", "checking")
        assert "account" in d and "session" in d
        assert "cookie" not in str(d).lower()

    def test_reconnect_toggle(self, router, paired):
        r = router.post("/api/v1/auth/reconnect", json={"enabled": False},
                        headers=paired["headers"])
        assert r.status_code == 200 and r.json()["enabled"] is False

    def test_reconnect_requires_auth(self, router):
        assert router.post("/api/v1/auth/reconnect").status_code == 401


class TestAuthLogin:
    def test_login_unconfigured_returns_400(self, router, paired):
        r = router.post("/api/v1/auth/login", json={"wait": False},
                        headers=paired["headers"])
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "E_BAD_REQUEST"

    def test_login_requires_auth(self, router):
        assert router.post("/api/v1/auth/login").status_code == 401

    def test_auth_tasks_unknown_404(self, router, paired):
        r = router.get("/api/v1/auth/tasks/auth_deadbeef", headers=paired["headers"])
        assert r.status_code == 404


@pytest.fixture()
def configured_router():
    """认证已配置（不可达门户，后台线程快速失败，无真实网络）。"""
    tm = TaskManager()
    profile = PortalProfile(name="dummy", portal_url="http://127.0.0.1:1/login",
                            check_online_first=False, max_retry=1,
                            field_map={}, success_check={"type": "contains",
                                                         "value": "联网成功"})
    cred = Credentials("20240001", "20240001", "pwd")
    ctx = RouterContext(taskmanager=tm,
                        auth=AuthController(tm, profile=profile, credentials=cred),
                        portal_name="dummy")
    srv = ApiServer(ctx, port=0)
    s = srv.start()
    base = f"http://127.0.0.1:{s.server_address[1]}"
    # 配对一个设备
    import requests
    pin = requests.post(base + "/api/v1/pair/start").json()["pin"]
    tok = requests.post(base + "/api/v1/pair/confirm", json={
        "pin": pin, "device_name": "x", "device_kind": "desktop"}).json()["device_token"]
    yield base, {"Authorization": "Bearer " + tok}
    srv.shutdown()


class TestAuthLoginConfigured:
    def test_login_returns_202_with_task(self, configured_router):
        base, h = configured_router
        import requests
        r = requests.post(base + "/api/v1/auth/login", json={"wait": False},
                          headers=h)
        assert r.status_code == 202
        d = r.json()
        assert d["task_id"].startswith("auth_") and d["state"] == "checking"

    def test_login_second_call_busy_503(self, configured_router):
        base, h = configured_router
        import requests
        requests.post(base + "/api/v1/auth/login", json={"wait": False}, headers=h)
        r = requests.post(base + "/api/v1/auth/login", json={"wait": False},
                          headers=h)
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "E_BUSY"

    def test_auth_tasks_returns_task(self, configured_router):
        base, h = configured_router
        import requests
        tid = requests.post(base + "/api/v1/auth/login", json={"wait": False},
                            headers=h).json()["task_id"]
        r = requests.get(base + f"/api/v1/auth/tasks/{tid}", headers=h)
        assert r.status_code == 200
        d = r.json()
        assert d["task_id"] == tid and d["state"] in \
            ("checking", "submitting", "online", "failed", "cancelled")


# ============================================================ 视觉任务
class TestVisionTasks:
    def test_next_empty_returns_204(self, router, paired):
        r = router.get("/api/v1/vision/tasks/next?wait=1", headers=paired["headers"])
        assert r.status_code == 204

    def test_next_returns_task_and_claim_payload(self, router, paired, captcha_png):
        router.ctx.taskmanager.create(captcha_png, expect_length=4)
        r = router.get("/api/v1/vision/tasks/next?wait=1", headers=paired["headers"])
        assert r.status_code == 200
        d = r.json()
        for key in ("task_id", "nonce", "image_url", "image_meta", "length",
                    "charset_hint", "allow_manual", "strategies_hint"):
            assert key in d
        assert d["length"] == 4

    def test_next_requires_auth(self, router):
        assert router.get("/api/v1/vision/tasks/next?wait=1").status_code == 401

    def test_image_returns_png(self, router, paired, captcha_png):
        t = router.ctx.taskmanager.create(captcha_png, expect_length=4)
        claimed = router.ctx.taskmanager.next_task(paired["device_id"], wait=0)
        assert claimed.task_id == t.task_id
        r = router.get(f"/api/v1/vision/tasks/{t.task_id}/image",
                       headers=paired["headers"])
        assert r.status_code == 200
        assert r.headers["Content-Type"] == "image/png"
        assert r.content == captcha_png

    def test_image_403_for_other_device(self, router, paired, captcha_png):
        t = router.ctx.taskmanager.create(captcha_png, expect_length=4)
        router.ctx.taskmanager.next_task("someone_else", wait=0)
        r = router.get(f"/api/v1/vision/tasks/{t.task_id}/image",
                       headers=paired["headers"])
        assert r.status_code == 403

    def test_result_accepted(self, router, paired, captcha_png):
        t = router.ctx.taskmanager.create(captcha_png, expect_length=4)
        claimed = router.ctx.taskmanager.next_task(paired["device_id"], wait=0)
        r = router.post(f"/api/v1/vision/tasks/{t.task_id}/result",
                        json={"nonce": claimed.nonce, "text": "e5s3",
                              "source": "ocr", "confidence": 1.0,
                              "detail": {"raw": "e5s3"}, "elapsed_ms": 5},
                        headers=paired["headers"])
        assert r.status_code == 200 and r.json()["accepted"] is True

    def test_result_duplicate_409(self, router, paired, captcha_png):
        t = router.ctx.taskmanager.create(captcha_png, expect_length=4)
        claimed = router.ctx.taskmanager.next_task(paired["device_id"], wait=0)
        body = {"nonce": claimed.nonce, "text": "e5s3", "source": "ocr",
                "confidence": 1.0, "detail": {}}
        assert router.post(f"/api/v1/vision/tasks/{t.task_id}/result",
                           json=body, headers=paired["headers"]).status_code == 200
        r = router.post(f"/api/v1/vision/tasks/{t.task_id}/result",
                        json=body, headers=paired["headers"])
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "E_DUPLICATE"

    def test_result_invalid_length_422(self, router, paired, captcha_png):
        t = router.ctx.taskmanager.create(captcha_png, expect_length=4)
        claimed = router.ctx.taskmanager.next_task(paired["device_id"], wait=0)
        r = router.post(f"/api/v1/vision/tasks/{t.task_id}/result",
                        json={"nonce": claimed.nonce, "text": "abc",
                              "source": "ocr", "confidence": 1.0, "detail": {}},
                        headers=paired["headers"])
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "E_INVALID_RESULT"

    def test_result_expired_410(self, router, paired, captcha_png):
        from campuslink.router.taskmanager import TaskState
        t = router.ctx.taskmanager.create(captcha_png, expect_length=4)
        router.ctx.taskmanager.next_task(paired["device_id"], wait=0)
        t.state = TaskState.EXPIRED
        r = router.post(f"/api/v1/vision/tasks/{t.task_id}/result",
                        json={"nonce": t.nonce, "text": "e5s3", "source": "ocr",
                              "confidence": 1.0, "detail": {}},
                        headers=paired["headers"])
        assert r.status_code == 410

    def test_cancel_requeues_to_pending(self, router, paired, captcha_png):
        t = router.ctx.taskmanager.create(captcha_png, expect_length=4)
        router.ctx.taskmanager.next_task(paired["device_id"], wait=0)
        r = router.delete(f"/api/v1/vision/tasks/{t.task_id}",
                          headers=paired["headers"])
        assert r.status_code == 204
        assert t.state.value == "pending"

    def test_get_task_status(self, router, paired, captcha_png):
        t = router.ctx.taskmanager.create(captcha_png, expect_length=4)
        r = router.get(f"/api/v1/vision/tasks/{t.task_id}",
                       headers=paired["headers"])
        assert r.status_code == 200
        assert r.json()["state"] == "pending"

    def test_get_task_unknown_404(self, router, paired):
        r = router.get("/api/v1/vision/tasks/cap_deadbeef",
                       headers=paired["headers"])
        assert r.status_code == 404


# ============================================================ 系统状态
class TestSystemStatus:
    def test_requires_auth(self, router):
        assert router.get("/api/v1/system/status").status_code == 401

    def test_fields_present(self, router, paired):
        d = router.get("/api/v1/system/status",
                       headers=paired["headers"]).json()
        for key in ("uptime_s", "network", "vision_nodes", "tasks", "storage"):
            assert key in d
        assert "pending" in d["tasks"] and "verified_24h" in d["tasks"]


# ============================================================ 图像约束
class TestImageConstraints:
    def test_wrong_format_415(self, captcha_png):
        from campuslink.router import TaskError, TaskManager
        tm = TaskManager()
        with pytest.raises(TaskError) as ei:
            tm.create(b"GIF89a not really a png", expect_length=4)
        assert ei.value.code == "E_UNSUPPORTED_MEDIA"

    def test_too_large_bytes_413(self):
        from campuslink.router import TaskError, TaskManager
        tm = TaskManager()
        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (150 * 1024)
        with pytest.raises(TaskError) as ei:
            tm.create(big, expect_length=4)
        assert ei.value.code == "E_IMAGE_TOO_LARGE"

    def test_too_wide_413(self):
        from campuslink.router import TaskError, TaskManager
        tm = TaskManager()
        with pytest.raises(TaskError) as ei:
            tm.create(make_png(500, 20), expect_length=4)
        assert ei.value.code == "E_IMAGE_TOO_LARGE"

    def test_valid_small_png_passes(self):
        from campuslink.router import TaskManager
        tm = TaskManager()
        assert tm.create(make_png(96, 34), expect_length=4) is not None


# ============================================================ 其它
class TestRouting:
    def test_unknown_path_404(self, router):
        assert router.get("/api/v1/nonexistent").status_code == 404

    def test_sensitive_scan_zero_hits(self, router, paired, captcha_png):
        """全端点敏感字段扫描：响应体不得含 cookie/password/token 等字样。"""
        router.ctx.taskmanager.create(captcha_png, expect_length=4)
        router.ctx.taskmanager.next_task(paired["device_id"], wait=0)

        responses = [
            router.get("/api/v1/capabilities"),
            router.post("/api/v1/pair/start"),
            router.get("/api/v1/auth/status", headers=paired["headers"]),
            router.get("/api/v1/auth/status"),
            router.get("/api/v1/pair/devices", headers=paired["headers"]),
            router.get("/api/v1/system/status", headers=paired["headers"]),
            router.get("/api/v1/vision/tasks/next?wait=0",
                       headers=paired["headers"]),
        ]
        for r in responses:
            if "application/json" not in r.headers.get("Content-Type", ""):
                continue        # 204 / 二进制图像 / 空体：无 JSON 可扫
            hits = scan_forbidden(r.json())
            assert hits == [], f"{r.request.path_url} 命中敏感字段 {hits}"
