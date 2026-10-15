# -*- coding: utf-8 -*-
"""
方案 A · 凭据下发端点（PUT/DELETE /api/v1/auth/credentials）契约测试。

对应 contract-change-log.md 的「方案 A」提案（已由用户裁决）。
核心断言：
  1. 已配对设备可下发凭据，响应只含脱敏摘要，绝不回显密码
  2. 任何响应体（含状态查询）不出现 password/secret/token 等敏感字段
  3. 未认证请求 401；缺字段 400
  4. DELETE 后凭据清空
"""
from __future__ import annotations

from tests.contract.conftest import scan_forbidden


class TestCredentials:
    def test_put_credentials_masked_response(self, router, paired):
        r = router.put("/api/v1/auth/credentials",
                       headers=paired["headers"],
                       json={"student_id": "20240001", "account": "20240001",
                             "operator": "中国电信", "password": "S3cret-Pwd"})
        assert r.status_code == 200
        body = r.json()
        assert body["configured"] is True
        assert body["student_id"] == "2024****001"
        assert body["operator"] == "中国电信"
        # 响应里绝不能有明文密码
        assert "S3cret" not in r.text
        assert scan_forbidden(body) == []

    def test_status_is_masked_and_clean(self, router, paired):
        router.put("/api/v1/auth/credentials", headers=paired["headers"],
                   json={"student_id": "20240001", "password": "S3cret-Pwd"})
        r = router.get("/api/v1/auth/status", headers=paired["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["account"]["student_id"] == "2024****001"
        assert scan_forbidden(body) == [], scan_forbidden(body)

    def test_delete_clears_credentials(self, router, paired):
        router.put("/api/v1/auth/credentials", headers=paired["headers"],
                   json={"student_id": "20240001", "password": "S3cret-Pwd"})
        r = router.delete("/api/v1/auth/credentials", headers=paired["headers"])
        assert r.status_code == 204
        st = router.get("/api/v1/auth/status", headers=paired["headers"]).json()
        assert st["account"]["student_id"] == ""

    def test_requires_authentication(self, router):
        r = router.put("/api/v1/auth/credentials",
                       json={"student_id": "x", "password": "y"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "E_UNAUTHORIZED"

    def test_rejects_missing_fields(self, router, paired):
        r = router.put("/api/v1/auth/credentials", headers=paired["headers"],
                       json={"student_id": "20240001"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "E_BAD_REQUEST"

        r = router.put("/api/v1/auth/credentials", headers=paired["headers"],
                       json={"password": "onlypwd"})
        assert r.status_code == 400

    def test_unknown_route_precedes_auth(self, router):
        # 未认证打到不存在路径仍是 404（不是 401）——既有约定回归
        r = router.put("/api/v1/nope")
        assert r.status_code == 404

    def test_credentials_do_not_leak_into_error_bodies(self, router, paired):
        # 下发后故意打错一个端点，确认错误体里也不含凭据字样
        router.put("/api/v1/auth/credentials", headers=paired["headers"],
                   json={"student_id": "20240001", "password": "S3cret-Pwd"})
        r = router.get("/api/v1/auth/tasks/not-exist", headers=paired["headers"])
        assert r.status_code == 404
        assert scan_forbidden(r.json()) == []
