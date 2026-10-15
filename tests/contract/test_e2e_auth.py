# -*- coding: utf-8 -*-
"""
端到端认证链路（验收标准：router_sim 模式下「配对 → 创建认证任务 →
模拟门户认证成功」全链路走通）。

复用 tools/router_sim.py 的模拟门户与演示视觉节点：触发 /auth/login 后，
本地演示节点领走验证码任务 → ddddocr 识别 → 提交 → 认证编排拿到结果 →
提交模拟门户 → 返回「联网成功」。
"""
from __future__ import annotations

import hashlib
import importlib.util
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

from campuslink.core import ocr
from campuslink.core.models import Credentials, PortalProfile
from campuslink.router import (ApiServer, AuthController, PairingManager,
                               RouterContext, TaskManager)

ROOT = Path(__file__).resolve().parent.parent.parent


def _load_sim():
    spec = importlib.util.spec_from_file_location(
        "router_sim", ROOT / "tools" / "router_sim.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def full_chain():
    sim = _load_sim()
    base_img = (ROOT / "tests" / "fixtures" / "captcha_sample.png").read_bytes()
    images = [base_img, sim._variant(base_img)]
    expected = {hashlib.md5(im).hexdigest():
                ocr.recognize(im, strategies=("raw", "gray"), length=4)[0]
                for im in images}
    state = sim._MockPortalState(images, expected, "20240001", "test-password")
    portal = ThreadingHTTPServer(("127.0.0.1", 0), sim._mock_handler(state))
    portal.daemon_threads = True
    threading.Thread(target=portal.serve_forever, daemon=True).start()
    portal_url = f"http://127.0.0.1:{portal.server_address[1]}/login"

    profile = PortalProfile(
        name="mock", portal_url=portal_url,
        field_map={
            "operator": "ctl00$MainContent$TextBoxISP",
            "student_id": "ctl00$MainContent$SSOGUITID",
            "account": "ctl00$MainContent$SSOACC",
            "password": "ctl00$MainContent$SSOPWD",
            "captcha": "ctl00$MainContent$TextBoxCC",
        },
        success_check={"type": "contains", "value": "联网成功"},
        captcha_url="auto", ocr_length=4, ocr_strategies=("raw", "gray"),
        max_retry=5, check_online_first=False,
    )
    cred = Credentials("20240001", "20240001", "test-password")
    tm = TaskManager()
    ctx = RouterContext(
        taskmanager=tm, pairing=PairingManager(),
        auth=AuthController(tm, profile=profile, credentials=cred),
        portal_name="模拟门户")
    srv = ApiServer(ctx, port=0)
    s = srv.start()
    base = f"http://127.0.0.1:{s.server_address[1]}"

    threading.Thread(target=sim._demo_node, args=(base,), daemon=True).start()
    time.sleep(0.3)
    yield base
    srv.shutdown()
    portal.shutdown()


def test_full_auth_chain(full_chain):
    base = full_chain
    # 配对一个客户端设备
    pin = requests.post(base + "/api/v1/pair/start").json()["pin"]
    tok = requests.post(base + "/api/v1/pair/confirm", json={
        "pin": pin, "device_name": "测试客户端", "device_kind": "mobile",
        "capabilities": ["ocr", "manual"]}).json()["device_token"]
    h = {"Authorization": "Bearer " + tok}

    # 触发认证
    r = requests.post(base + "/api/v1/auth/login", json={"wait": False}, headers=h)
    assert r.status_code == 202
    assert r.json()["task_id"].startswith("auth_")

    # 轮询直到 online（演示节点自动识别验证码）
    state = "checking"
    deadline = time.time() + 20
    while time.time() < deadline and state not in ("online", "failed"):
        time.sleep(0.3)
        state = requests.get(base + "/api/v1/auth/status", headers=h).json()["state"]

    assert state == "online", "全链路认证应成功（演示节点识别 + 模拟门户通过）"
    st = requests.get(base + "/api/v1/auth/status", headers=h).json()
    assert st["last_result"]["ok"] is True
    assert "联网成功" in st["last_result"]["message"]
