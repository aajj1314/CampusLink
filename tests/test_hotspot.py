# -*- coding: utf-8 -*-
"""
校园网共享（TTL 修复）核心的测试。

真实 TTL 改包需要管理员 + WinDivert 驱动 + 真实流量，无法在单测里跑；
这里锁的是不会因重构而坏掉的契约：

  · 两条过滤器字符串与目标 TTL（线上实测验证过的那组，改错就全断网）
  · TtlFixer 的生命周期幂等性、失败原因可读
  · 状态快照的 hint 语义（UI 直接显示它）
  · 热点判据 = 192.168.137.1 出现在本机 IPv4
"""
from __future__ import annotations

import sys
import threading
import time

import pytest

from campuslink.core import hotspot


class TestFilters:
    def test_filter_strings(self):
        assert hotspot.FILTER_INBOUND == "inbound and ip and ip.TTL <= 1"
        assert (hotspot.FILTER_OUTBOUND
                == "outbound and ip and (ip.TTL == 63 or ip.TTL == 127)")
        assert hotspot.TTL_TARGET == 64

    def test_ics_gateway_constant(self):
        assert hotspot.ICS_GATEWAY == "192.168.137.1"


class TestHotspotDetect:
    def test_on_when_gateway_present(self, monkeypatch):
        monkeypatch.setattr(hotspot, "local_ipv4_all",
                            lambda: ["172.22.64.176", "192.168.137.1"])
        assert hotspot.hotspot_on() is True

    def test_off_when_gateway_absent(self, monkeypatch):
        monkeypatch.setattr(hotspot, "local_ipv4_all", lambda: ["172.22.64.176"])
        assert hotspot.hotspot_on() is False


class TestTtlFixerLifecycle:
    def test_start_fails_without_pydivert(self, monkeypatch):
        monkeypatch.setattr(hotspot, "pydivert_available",
                            lambda: (False, "pydivert 不可用（ImportError）"))
        f = hotspot.TtlFixer()
        ok, msg = f.start()
        assert ok is False and "pydivert" in msg
        assert f.running is False

    def test_start_fails_without_admin(self, monkeypatch):
        monkeypatch.setattr(hotspot, "pydivert_available", lambda: (True, ""))
        monkeypatch.setattr(hotspot, "is_admin", lambda: False)
        f = hotspot.TtlFixer()
        ok, msg = f.start()
        assert ok is False and "管理员" in msg

    def test_start_stop_idempotent(self, monkeypatch):
        monkeypatch.setattr(hotspot, "pydivert_available", lambda: (True, ""))
        monkeypatch.setattr(hotspot, "is_admin", lambda: True)

        started = []

        def fake_loop(self, flt, tag, name):
            started.append(flt)
            # 模拟过滤器挂上后一直等 stop
            while not self._stop.is_set():
                time.sleep(0.01)

        monkeypatch.setattr(hotspot.TtlFixer, "_loop", fake_loop)
        f = hotspot.TtlFixer()
        ok1, _ = f.start()
        ok2, msg2 = f.start()          # 重复启动幂等
        assert ok1 is True and ok2 is True and "已在运行" in msg2
        time.sleep(0.1)
        assert len(started) == 2       # 两条过滤器
        assert f.running is True

        f.stop()
        time.sleep(0.1)
        assert f.running is False


class TestStatus:
    def test_ready_hint_needs_pydivert_first(self):
        st = hotspot.HotspotStatus(pydivert_ok=False, pydivert_reason="x")
        assert "不可用" in st.ready_hint

    def test_ready_hint_needs_admin(self):
        st = hotspot.HotspotStatus(pydivert_ok=True, admin=False)
        assert "管理员" in st.ready_hint

    def test_ready_hint_sharing_ok(self):
        st = hotspot.HotspotStatus(pydivert_ok=True, admin=True,
                                   ttl_running=True, hotspot_on=True)
        assert "共享中" in st.ready_hint

    def test_ready_hint_ttl_only(self):
        st = hotspot.HotspotStatus(pydivert_ok=True, admin=True,
                                   ttl_running=True, hotspot_on=False)
        assert "热点还没开" in st.ready_hint

    def test_collect_includes_fixer_counters(self, monkeypatch):
        monkeypatch.setattr(hotspot, "pydivert_available", lambda: (True, ""))
        monkeypatch.setattr(hotspot, "is_admin", lambda: True)
        monkeypatch.setattr(hotspot, "hotspot_on", lambda: True)
        f = hotspot.TtlFixer()
        f.packets_in, f.packets_out = 7, 3
        st = hotspot.collect_status(f)
        assert st.packets_in == 7 and st.packets_out == 3
        assert st.admin is True and st.hotspot_on is True


class TestAdminHelpers:
    def test_is_admin_on_windows(self):
        # 本机测试环境是普通权限：应为 False；管理员运行 CI 时可为 True
        assert isinstance(hotspot.is_admin(), bool)

    def test_relaunch_returns_bool(self, monkeypatch):
        import ctypes
        monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW",
                            lambda *a: 33, raising=False)
        assert hotspot.relaunch_as_admin("--hotspot") is True
