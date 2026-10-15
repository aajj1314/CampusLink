# -*- coding: utf-8 -*-
"""
视觉节点测试（任务书 Agent 2 交付物）：配对 / 轮询 / 提交 / 取消 / 降级监测。

对 Agent 1 的 Router 控制面（RouterContext + ApiServer）做本地 stub 联调，
不依赖真实 Router / 真机。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from campuslink.core.ocr_backend import OcrResult
from campuslink.visionnode import NodeClient, NodeError, RouterWatcher, VisionNode
from tests.contract.conftest import make_png

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def captcha_png() -> bytes:
    return (FIXTURES / "captcha_sample.png").read_bytes()


@pytest.fixture()
def router():
    from campuslink.router import ApiServer, RouterContext
    ctx = RouterContext(portal_name="test")
    srv = ApiServer(ctx, port=0)
    s = srv.start()
    base = f"http://127.0.0.1:{s.server_address[1]}"
    yield base, ctx, srv
    srv.shutdown()


@pytest.fixture()
def client(router) -> NodeClient:
    return NodeClient(router[0])


@pytest.fixture()
def paired(router, client):
    base, ctx, srv = router
    pin = client.pair_start()["pin"]
    result = client.pair_confirm(pin, device_name="测试节点", device_kind="desktop",
                                 capabilities=["ocr"])
    client.set_token(result["device_token"])
    return client, ctx


class _StubBackend:
    def __init__(self, text: str = "e5s3"):
        self.text = text

    def recognize(self, image, *, length, strategies):
        return OcrResult(text=self.text, confidence=1.0,
                         detail={"raw": self.text}, source="local")


class _BlockingBackend:
    """阻塞在 recognize 里，用于验证 stop() 时释放已持有的任务。"""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def recognize(self, image, *, length, strategies):
        self.started.set()
        self.release.wait(5.0)
        return OcrResult(text="e5s3", confidence=1.0, detail={}, source="local")


class _FakeHealthClient:
    def __init__(self):
        self.ok = True

    def health(self) -> bool:
        return self.ok


# ============================================================ NodeClient
class TestNodeClient:
    def test_health_ok(self, client):
        assert client.health() is True

    def test_health_false_when_down(self):
        c = NodeClient("http://127.0.0.1:1")
        assert c.health() is False

    def test_pair_start_returns_pin(self, client):
        d = client.pair_start()
        assert len(d["pin"]) == 6 and d["pin"].isdigit()

    def test_pair_confirm_returns_token(self, client):
        pin = client.pair_start()["pin"]
        d = client.pair_confirm(pin, device_name="x", device_kind="desktop",
                                capabilities=["ocr"])
        assert d["device_token"] and d["device_id"].startswith("dev_")

    def test_next_task_empty_returns_none(self, paired):
        client, ctx = paired
        assert client.next_task(wait=0.5) is None

    def test_next_task_returns_task(self, paired, captcha_png):
        client, ctx = paired
        ctx.taskmanager.create(captcha_png, expect_length=4)
        t = client.next_task(wait=0.5)
        assert t is not None and t["task_id"].startswith("cap_")
        assert t["length"] == 4 and "nonce" in t

    def test_submit_result_accepted(self, paired, captcha_png):
        client, ctx = paired
        ctx.taskmanager.create(captcha_png, expect_length=4)
        t = client.next_task(wait=0.5)
        assert client.submit_result(t["task_id"], t["nonce"], "e5s3", "ocr",
                                    1.0, {"raw": "e5s3"}, 5) is True

    def test_cancel_task(self, paired, captcha_png):
        client, ctx = paired
        t = ctx.taskmanager.create(captcha_png, expect_length=4)
        client.next_task(wait=0.5)
        assert client.cancel_task(t.task_id) is True
        assert t.state.value == "pending"

    def test_auth_required_without_token(self, router):
        c = NodeClient(router[0])      # 未配对、无 token
        with pytest.raises(NodeError):
            c.next_task(wait=0.5)


# ============================================================ VisionNode
class TestVisionNode:
    def test_node_claims_and_submits(self, paired, captcha_png):
        client, ctx = paired
        t = ctx.taskmanager.create(captcha_png, expect_length=4)
        events: list[str] = []
        node = VisionNode(client, backend=_StubBackend("e5s3"), poll_wait=0.2,
                          on_event=lambda k, p: events.append(k))
        node.start()
        try:
            _wait_for(lambda: "submitted" in events, timeout=5.0)
        finally:
            node.stop()
        assert "submitted" in events
        assert t.state.value == "verified"

    def test_node_skips_when_ocr_empty(self, paired, captcha_png):
        client, ctx = paired
        t = ctx.taskmanager.create(captcha_png, expect_length=4)
        events: list[str] = []
        node = VisionNode(client, backend=_StubBackend(""), poll_wait=0.2,
                          on_event=lambda k, p: events.append(k))
        node.start()
        try:
            _wait_for(lambda: "skipped" in events, timeout=5.0)
        finally:
            node.stop()
        assert "skipped" in events
        assert t.state.value == "pending", "识别失败应主动放弃，任务回 pending"

    def test_node_stop_releases_held_task(self, paired, captcha_png):
        client, ctx = paired
        t = ctx.taskmanager.create(captcha_png, expect_length=4)
        backend = _BlockingBackend()
        node = VisionNode(client, backend=backend, poll_wait=0.2)
        node.start()
        assert backend.started.wait(3.0), "节点应领到任务并开始识别"
        assert t.state.value == "assigned"
        node.stop()                     # 退出时应主动取消持有的任务
        backend.release.set()
        assert t.state.value == "pending", "节点退出后不应遗留 assigned 任务"

    def test_node_stop_when_idle(self, paired):
        client, ctx = paired
        node = VisionNode(client, backend=_StubBackend(), poll_wait=0.2)
        node.start()
        time.sleep(0.3)
        node.stop()
        assert node.running is False


# ============================================================ RouterWatcher
class TestRouterWatcher:
    def test_watcher_reports_reachable(self, router):
        w = RouterWatcher(NodeClient(router[0]), interval=0.3, max_failures=3,
                          on_change=lambda _: None)
        w.start()
        _wait_for(lambda: w.reachable, timeout=3.0)
        w.stop()
        assert w.reachable is True

    def test_watcher_detects_lost_after_3_failures(self):
        fake = _FakeHealthClient()
        changes: list[bool] = []
        w = RouterWatcher(fake, interval=0.2, max_failures=3,
                          on_change=changes.append)
        w.start()
        _wait_for(lambda: w.reachable, timeout=3.0)
        fake.ok = False
        _wait_for(lambda: not w.reachable, timeout=3.0)
        w.stop()
        assert w.reachable is False
        assert changes[-1] is False

    def test_watcher_recovers(self):
        fake = _FakeHealthClient()
        changes: list[bool] = []
        w = RouterWatcher(fake, interval=0.2, max_failures=3,
                          on_change=changes.append)
        w.start()
        _wait_for(lambda: w.reachable, timeout=3.0)
        fake.ok = False
        _wait_for(lambda: not w.reachable, timeout=3.0)
        fake.ok = True
        _wait_for(lambda: w.reachable, timeout=3.0)
        w.stop()
        assert changes == [True, False, True]


def _wait_for(cond, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("条件在超时内未满足")
