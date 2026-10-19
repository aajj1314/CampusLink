# -*- coding: utf-8 -*-
"""
is_online 双点并行保守判定的测试。

背景（2026-10-19 实测踩坑）：校园网普遍放行 gstatic 连通性检查域名
（防设备反复弹认证窗），未认证时 gstatic 也返回真 204 —— 单点判定
把「未认证」误报成「已联网」，CLI 首次配置被"已联网无需认证"短路。

新语义（用户拍板）：gstatic + msftconnecttest 两点**并行**探测
（总耗时 = 最慢单点，不叠加），**两点都通过才算在线**——保守取向：
宁可多走一次认证，也不误判在线跳过认证。
"""
from __future__ import annotations

from campuslink.core import netdetect


def _ok(status: int, body: str = ""):
    def _t(url, timeout=5.0, **kw):
        return {"ok": True, "status": status, "final_url": url,
                "location": "", "body": body, "length": len(body)}
    return _t


def _hijack():
    def _t(url, timeout=5.0, **kw):
        return {"ok": True, "status": 200, "final_url": "http://10.6.6.6/sso",
                "location": "", "body": "<html>登录</html>", "length": 3000}
    return _t


def _unreachable():
    def _t(url, timeout=5.0, **kw):
        return {"ok": False, "error": "ConnectError"}
    return _t


def _route_by_host(monkeypatch, gstatic, msft):
    """按主机名分流：gstatic / msft 各自返回指定探测结果。"""
    def _t(url, timeout=5.0, **kw):
        if "gstatic" in url:
            return gstatic(url, timeout=timeout)
        return msft(url, timeout=timeout)
    monkeypatch.setattr(netdetect, "_touch", _t)


class TestIsOnlineDualProbe:
    def test_both_clean_is_online(self, monkeypatch):
        """真联网：两点全过 → 在线。"""
        _route_by_host(monkeypatch, _ok(204), _ok(200, "Microsoft Connect Test"))
        assert netdetect.is_online() is True

    def test_gstatic_only_passes_is_not_online(self, monkeypatch):
        """核心踩坑点：校园网放行 gstatic（1/2）→ 不在线，正确去认证。"""
        _route_by_host(monkeypatch, _ok(204), _hijack())
        assert netdetect.is_online() is False

    def test_msft_only_passes_is_not_online(self, monkeypatch):
        """反向防护：仅 msft 通而 gstatic 被劫持 → 也不算在线（保守）。"""
        _route_by_host(monkeypatch, _hijack(), _ok(200, "Microsoft Connect Test"))
        assert netdetect.is_online() is False

    def test_all_hijacked_is_not_online(self, monkeypatch):
        _route_by_host(monkeypatch, _hijack(), _hijack())
        assert netdetect.is_online() is False

    def test_all_unreachable_is_not_online(self, monkeypatch):
        _route_by_host(monkeypatch, _unreachable(), _unreachable())
        assert netdetect.is_online() is False


class TestParallelism:
    def test_probes_run_concurrently(self, monkeypatch):
        """
        并行性：两点同时发出，总耗时 ≈ 最慢单点（而非相加）。

        让每个探测睡 0.6s：串行 ≥1.2s，并行 <1.0s。
        """
        import time

        def _slow(url, timeout=5.0, **kw):
            time.sleep(0.6)
            if "gstatic" in url:
                return _ok(204)(url, timeout=timeout)
            return _ok(200, "Microsoft Connect Test")(url, timeout=timeout)

        monkeypatch.setattr(netdetect, "_touch", _slow)
        start = time.monotonic()
        assert netdetect.is_online() is True
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"探测疑似串行：耗时 {elapsed:.2f}s"
