# -*- coding: utf-8 -*-
"""
is_online 多点保守判定的测试。

背景（2026-10-19 实测踩坑）：校园网普遍放行 gstatic 连通性检查域名
（防设备反复弹认证窗），未认证时 gstatic 也返回真 204 —— 单点判定
把「未认证」误报成「已联网」，CLI 首次配置被"已联网无需认证"短路。

新语义：三个探测点（gstatic / msftconnecttest / firefox detectportal）
**至少 2 个返回正确内容才算在线**。放行单点骗不过它；真联网三个全过。
"""
from __future__ import annotations

import pytest

from campuslink.core import netdetect


def _touch_ok(status: int, body: str = "", length: int | None = None):
    def _t(url, timeout=5.0, **kw):
        return {"ok": True, "status": status, "final_url": url,
                "location": "", "body": body,
                "length": len(body) if length is None else length}
    return _t


def _touch_fail(exc_name: str = "ConnectError"):
    def _t(url, timeout=5.0, **kw):
        return {"ok": False, "error": exc_name}
    return _t


def _touch_hijack():
    """强制门户劫持：302 到内网地址 / 200 返回门户页。"""
    def _t(url, timeout=5.0, **kw):
        return {"ok": True, "status": 200, "final_url": "http://10.6.6.6/sso",
                "location": "", "body": "<html>登录</html>", "length": 3000}
    return _t


class TestIsOnlineMajority:
    def test_all_clean_is_online(self, monkeypatch):
        monkeypatch.setattr(netdetect, "_touch", _touch_ok(204 if False else 200,
                                                           "Microsoft Connect Test"))
        # gstatic 那条需要 204+空，上面 body 不匹配；用更精确的编排 ↓

    def test_real_online_three_clean(self, monkeypatch):
        responses = {
            "gstatic": _touch_ok(204, "", length=0),
            "msft": _touch_ok(200, "Microsoft Connect Test"),
            "firefox": _touch_ok(200, "success"),
        }
        def _route(url, timeout=5.0, **kw):
            key = "gstatic" if "gstatic" in url else (
                  "msft" if "msftconnecttest" in url else "firefox")
            return responses[key](url, timeout=timeout)
        monkeypatch.setattr(netdetect, "_touch", _route)
        assert netdetect.is_online() is True

    def test_gstatic_only_passes_is_not_online(self, monkeypatch):
        """校园网放行 gstatic：1/3 通过 → 必须判 False（本次踩坑点）。"""
        def _route(url, timeout=5.0, **kw):
            if "gstatic" in url:
                return _touch_ok(204, "", length=0)(url)
            return _touch_hijack()(url)
        monkeypatch.setattr(netdetect, "_touch", _route)
        assert netdetect.is_online() is False

    def test_two_clean_is_online(self, monkeypatch):
        """gstatic 被劫持但另两个真通（罕见的探测点差异）→ 在线。"""
        def _route(url, timeout=5.0, **kw):
            if "gstatic" in url:
                return _touch_hijack()(url)
            key = "msft" if "msftconnecttest" in url else "firefox"
            resp = {"msft": _touch_ok(200, "Microsoft Connect Test"),
                    "firefox": _touch_ok(200, "success")}[key]
            return resp(url, timeout=timeout)
        monkeypatch.setattr(netdetect, "_touch", _route)
        assert netdetect.is_online() is True

    def test_all_hijacked_is_not_online(self, monkeypatch):
        monkeypatch.setattr(netdetect, "_touch", _touch_hijack())
        assert netdetect.is_online() is False

    def test_all_unreachable_is_not_online(self, monkeypatch):
        monkeypatch.setattr(netdetect, "_touch", _touch_fail())
        assert netdetect.is_online() is False
