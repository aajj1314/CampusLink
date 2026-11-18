# -*- coding: utf-8 -*-
"""
HTTP User-Agent 归一化的单元测试（2026-11-18 新增）。

覆盖范围：
  · HTTP 请求识别（方法行判据）
  · UA 行匹配（大小写、多请求、格式变体）
  · 等长替换（长度守恒是本模块的硬约束）
  · 短 UA 的截断行为（与 ua2f 一致）
  · 非浏览器 UA 的跳过规则
  · 边界情况（空 payload、二进制、响应包）

真实 WinDivert 抓包验证不放单元测试（需要管理员权限，
在 CI 里跑不了）。那部分由手工集成测试覆盖，见 docs/design/09。
"""
from __future__ import annotations

import pytest

from campuslink.core.ua_mask import (
    DEFAULT_UA,
    SKIP_PREFIXES,
    build_replacement,
    describe,
    is_http_request,
    mask_ua_in_payload,
    should_skip,
)


# ------------------------------------------------------- is_http_request
class TestIsHttpRequest:
    @pytest.mark.parametrize("method", [
        b"GET", b"POST", b"HEAD", b"PUT", b"DELETE",
        b"OPTIONS", b"PATCH", b"CONNECT", b"TRACE",
    ])
    def test_all_methods(self, method):
        payload = method + b" /path HTTP/1.1\r\nHost: x\r\n\r\n"
        assert is_http_request(payload) is True

    def test_response_is_not_request(self):
        payload = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n"
        assert is_http_request(payload) is False

    def test_tls_client_hello(self):
        """TLS 握手包不能被当成 HTTP。"""
        assert is_http_request(b"\x16\x03\x01\x02\x00\x01\x00\x01\xfc") is False

    def test_binary(self):
        assert is_http_request(b"\x00\x01\x02\x03" * 10) is False

    def test_too_short(self):
        assert is_http_request(b"GET /") is False
        assert is_http_request(b"") is False

    def test_lowercase_method_not_matched(self):
        """HTTP 方法按规范是大写；小写不算请求（避免误判）。"""
        assert is_http_request(b"get / HTTP/1.1\r\n\r\n") is False


# --------------------------------------------------------- should_skip
class TestShouldSkip:
    @pytest.mark.parametrize("ua", [
        b"curl/8.0", b"Wget/1.21", b"python-requests/2.31",
        b"Go-http-client/1.1", b"okhttp/4.9.0", b"Java/17.0",
        b"Microsoft-CryptoAPI/10.0",
    ])
    def test_non_browser_skipped(self, ua):
        assert should_skip(ua) is True

    @pytest.mark.parametrize("ua", [
        b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131",
        b"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)",
        b"Mozilla/5.0 (Linux; Android 13)",
        b"",
    ])
    def test_browser_not_skipped(self, ua):
        """空 UA 不跳过 —— 交给 build_replacement 处理（长度 0 → None）。"""
        assert should_skip(ua) is False

    def test_all_prefixes_covered(self):
        """SKIP_PREFIXES 里的每一条都要真的生效。"""
        for p in SKIP_PREFIXES:
            assert should_skip((p + "test").encode()) is True


# --------------------------------------------------- build_replacement
class TestBuildReplacement:
    def test_exact_length_padding(self):
        """长度守恒是硬约束 —— 改长度会破坏 TCP 序列号。"""
        for n in (12, 20, 50, 120, 200):
            rep = build_replacement(n)
            assert rep is not None
            assert len(rep) == n, f"长度 {n} 时替换值长度不对"

    def test_long_ua_full_replace(self):
        rep = build_replacement(120)
        assert rep.strip() == DEFAULT_UA.encode()

    def test_short_ua_truncated(self):
        """比目标短的 UA 会被截断 —— 与 ua2f 完全一致的行为。"""
        rep = build_replacement(8)      # "curl/8.0"
        assert rep == b"Mozilla/"

    def test_very_short_gives_up(self):
        """长度 < 3 时放弃（截断只剩一两个字反而更假）。"""
        assert build_replacement(0) is None
        assert build_replacement(1) is None
        assert build_replacement(2) is None

    def test_exactly_target_length(self):
        rep = build_replacement(len(DEFAULT_UA))
        assert rep == DEFAULT_UA.encode()

    def test_custom_target(self):
        target = "Mozilla/5.0 (iPhone)"
        rep = build_replacement(50, target)
        assert rep is not None
        assert len(rep) == 50
        assert rep.strip() == target.encode()

    def test_custom_target_too_long(self):
        """自定义目标比原 UA 长时也截断。"""
        rep = build_replacement(5, "Mozilla/5.0 (iPhone)")
        assert rep == b"Mozil"


# -------------------------------------------------- mask_ua_in_payload
class TestMaskUaInPayload:
    @staticmethod
    def _req(ua: str) -> bytes:
        return (b"GET / HTTP/1.1\r\n"
                b"Host: example.com\r\n"
                b"User-Agent: " + ua.encode() + b"\r\n"
                b"Accept: */*\r\n\r\n")

    def test_long_ua_masked(self):
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"
        payload = self._req(ua)
        new, changed = mask_ua_in_payload(payload)
        assert changed is True
        assert len(new) == len(payload), "长度必须守恒"
        assert b"User-Agent: Mozilla/5.0   " in new

    def test_short_ua_skipped(self):
        """curl 类 UA 在 SKIP_PREFIXES 里，整个跳过。"""
        payload = self._req("curl/8.0")
        new, changed = mask_ua_in_payload(payload)
        assert changed is False
        assert new == payload

    def test_no_ua_header(self):
        payload = b"GET / HTTP/1.1\r\nHost: x\r\n\r\n"
        new, changed = mask_ua_in_payload(payload)
        assert changed is False

    def test_response_untouched(self):
        payload = (b"HTTP/1.1 200 OK\r\n"
                   b"User-Agent: whatever\r\n\r\n")
        new, changed = mask_ua_in_payload(payload)
        assert changed is False, "响应里改 UA 没有意义"

    def test_binary_untouched(self):
        payload = b"\x16\x03\x01\x02\x00" + b"\x00" * 100
        new, changed = mask_ua_in_payload(payload)
        assert changed is False

    def test_empty(self):
        new, changed = mask_ua_in_payload(b"")
        assert changed is False
        assert new == b""

    def test_case_insensitive_header_name(self):
        """HTTP 头字段名大小写不敏感（RFC 7230 §3.2）。"""
        payload = (b"GET / HTTP/1.1\r\n"
                   b"user-agent: Mozilla/5.0 (Windows NT 10.0) Chrome/131\r\n\r\n")
        new, changed = mask_ua_in_payload(payload)
        assert changed is True
        assert len(new) == len(payload)

    def test_multiple_requests_pipelined(self):
        """一个 TCP 段里有多条请求时全部改。"""
        payload = (b"GET /a HTTP/1.1\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0) Chrome/131\r\n\r\n"
                   b"GET /b HTTP/1.1\r\nUser-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)\r\n\r\n")
        new, changed = mask_ua_in_payload(payload)
        assert changed is True
        assert len(new) == len(payload)
        # 两条都变成 Mozilla/5.0
        import re
        uas = re.findall(rb"User-Agent: ([^\r\n]*)", new)
        assert len(uas) == 2
        for ua in uas:
            assert ua.strip() == b"Mozilla/5.0"

    def test_custom_target(self):
        payload = self._req("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0")
        new, changed = mask_ua_in_payload(payload, target="Mozilla/5.0 (X11; Linux x86_64)")
        assert changed is True
        assert len(new) == len(payload)

    def test_ua_at_end_of_payload(self):
        """UA 行在 payload 末尾（后面没别的头）时也要能改。"""
        payload = (b"GET / HTTP/1.1\r\n"
                   b"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131\r\n"
                   b"\r\n")
        new, changed = mask_ua_in_payload(payload)
        assert changed is True


# ------------------------------------------------------------ describe
class TestDescribe:
    def test_keys(self):
        d = describe()
        for k in ("pydivert_ok", "pydivert_reason", "admin",
                  "target_ua", "target_len", "covers", "limit"):
            assert k in d

    def test_target_len_correct(self):
        d = describe("Mozilla/5.0")
        assert d["target_len"] == 11

    def test_custom_target(self):
        d = describe("TestUA")
        assert d["target_ua"] == "TestUA"
        assert d["target_len"] == 6


# -------------------------------------------------------- 行为契约
class TestLengthInvariant:
    """
    核心不变式：**任何情况下输出的 payload 长度必须与输入相同。**

    这条一旦破坏，TCP 序列号就错位，连接会直接挂掉。
    所以单列一个测试类，穷举各种长度。
    """

    @pytest.mark.parametrize("ua_len", [3, 5, 8, 11, 12, 20, 50, 100, 200, 500])
    def test_length_preserved(self, ua_len):
        ua = "X" * ua_len
        payload = (b"GET / HTTP/1.1\r\n"
                   b"User-Agent: " + ua.encode() + b"\r\n\r\n")
        new, _ = mask_ua_in_payload(payload)
        assert len(new) == len(payload), f"UA 长度 {ua_len} 时破坏了长度守恒"
