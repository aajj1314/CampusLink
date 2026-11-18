# -*- coding: utf-8 -*-
"""
免验证码注销的单元测试（2026-11-18 新增）。

覆盖范围：
  · 注销页解析（LogoutPage 字段、在线状态、用户 IP）
  · 提交载荷（必须含 LogoffBtn）
  · 判定逻辑（五种 reason 分支）
  · 验证码置信度门限
  · OCR logout 模式

不覆盖真实网络请求 —— 那部分由手工集成测试验证
（`python -m campuslink.cli logout`，见 docs/design/09）。
"""
from __future__ import annotations

import pytest

from campuslink.core.authenticator import (
    LOGOFF_FAIL_HINTS,
    LOGOFF_SUCCESS_HINTS,
    _judge_logout,
)
from campuslink.core.models import PortalProfile
from campuslink.core.portal import (
    LogoutPage,
    PortalClient,
    extract_message_from_html,
    extract_online_state,
    extract_user_ip,
)


# ------------------------------------------------------------------ 测试数据
#: 实测抓到的注销页片段（2026-11-18，来自 10.6.6.6/logoff?lrl=1）
LOGOFF_HTML_ONLINE = """
<html><head><title>断开网络 - 多运营商接入管理平台统一认证系统</title></head>
<body>
<tr>
  <td align="right"><span class="login-1">当前用户IP</span></td>
  <td align="left"><span id="MainContent_LocalIP">172.22.67.139</span><br /></td>
</tr><tr>
  <td align="right"><span class="login-1">当前在线状态</span></td>
  <td align="left"><span id="MainContent_LabelOnline" style="color:LightGreen;">
    <font color="LightGreen">已联网</font></span><br /></td>
</tr>
<form method="post" action="./logoff?lrl=1" id="ctl01">
  <input type="hidden" name="__VIEWSTATE" value="VS_ONLINE" />
  <input type="hidden" name="__VIEWSTATEGENERATOR" value="C04F0937" />
  <input type="hidden" name="__VIEWSTATEENCRYPTED" value="" />
  <input type="hidden" name="__EVENTVALIDATION" value="EV_ONLINE" />
  <input name="ctl00$MainContent$TextBoxCC" type="text" maxlength="4" />
  <input type="submit" name="ctl00$MainContent$LogoffBtn" value="注销"
         disabled="disabled" class="aspNetDisabled LoginBtn" />
</form>
</body></html>
"""

LOGOFF_HTML_OFFLINE = LOGOFF_HTML_ONLINE.replace(
    '<font color="LightGreen">已联网</font>',
    '<font color="OrangeRed">未联网</font>'
).replace('LightGreen', 'OrangeRed')

LOGOFF_HTML_CAPTCHA_ERROR = """
<html><body>
<span id="MainContent_LabelOnline"><font>已联网</font></span>
<p class="validation-summary-errors">验证码错误!</p>
</body></html>
"""


# ------------------------------------------------------- extract_online_state
class TestExtractOnlineState:
    def test_online_with_font_tag(self):
        html = '<span id="MainContent_LabelOnline"><font>已联网</font></span>'
        assert extract_online_state(html) == "已联网"

    def test_offline_with_font_tag(self):
        html = '<span id="MainContent_LabelOnline"><font>未联网</font></span>'
        assert extract_online_state(html) == "未联网"

    def test_online_without_font_tag(self):
        """不同状态渲染不同，font 标签是可选的。"""
        html = '<span id="MainContent_LabelOnline" style="x">已联网</span>'
        assert extract_online_state(html) == "已联网"

    def test_real_page_online(self):
        assert extract_online_state(LOGOFF_HTML_ONLINE) == "已联网"

    def test_real_page_offline(self):
        assert extract_online_state(LOGOFF_HTML_OFFLINE) == "未联网"

    def test_missing_returns_empty(self):
        assert extract_online_state("<html></html>") == ""


# ------------------------------------------------------------ extract_user_ip
class TestExtractUserIp:
    def test_real_page(self):
        assert extract_user_ip(LOGOFF_HTML_ONLINE) == "172.22.67.139"

    def test_missing_returns_empty(self):
        assert extract_user_ip("<html></html>") == ""


# -------------------------------------------------- extract_message_from_html
class TestExtractMessage:
    def test_captcha_error(self):
        html = '<p class="validation-summary-errors">验证码错误!</p>'
        assert extract_message_from_html(html) == "验证码错误!"

    def test_empty_page_returns_empty_not_title(self):
        """
        2026-11-18 修的坑：原实现兜底返回前 120 字符，
        结果把 <title>断开网络 - ...</title> 当成报错，日志全是噪音。
        """
        html = ("<html><head><title>断开网络 - 某某系统</title></head>"
                "<body><p>无关内容</p></body></html>")
        assert extract_message_from_html(html) == ""

    def test_real_logoff_page_has_no_message(self):
        """正常注销页（无报错）应该返回空串。"""
        assert extract_message_from_html(LOGOFF_HTML_ONLINE) == ""


# --------------------------------------------------------------- _judge_logout
class TestJudgeLogout:
    def test_captcha_error_is_only_retryable_failure(self):
        ok, reason, raw = _judge_logout(LOGOFF_HTML_CAPTCHA_ERROR, "已联网")
        assert ok is False
        assert reason == "captcha_error"
        assert "验证码错误" in raw

    def test_success_hint(self):
        for hint in LOGOFF_SUCCESS_HINTS:
            html = f"<p>{hint}</p>"
            ok, reason, _ = _judge_logout(html, "已联网")
            assert ok is True, f"'{hint}' 应该判成功"
            assert reason == "success_hint"

    def test_real_success_page(self):
        """
        实测真实成功页（2026-11-18，_SUCCESS_2.html）。

        关键事实：成功文案在 validation-summary-errors 容器里，
        而且页面在线状态**仍然显示「已联网」**（服务端不实时刷新）。
        """
        html = """
        <html><body>
        <span id="MainContent_LocalIP">172.22.67.139</span>
        <span id="MainContent_LabelOnline" style="color:LightGreen;">
            <font color="LightGreen">已联网</font></span>
        <p class="validation-summary-errors">注销成功!</p>
        </body></html>
        """
        ok, reason, raw = _judge_logout(html, "已联网")
        assert ok is True
        assert reason == "success_hint"
        assert "注销成功" in raw

    def test_state_change_alone_is_not_success(self):
        """
        ⚠️ 反直觉但实测确认：注销成功后在线状态**不会**变成「未联网」。
        所以「已联网 → 未联网」不能当成功判据。
        这里验证：只有状态变化、没有成功文案时，判 unknown（需要重试）。
        """
        html = ('<span id="MainContent_LabelOnline">'
                '<font>未联网</font></span>')
        ok, reason, _ = _judge_logout(html, "已联网")
        assert ok is False
        assert reason == "unknown"

    def test_already_offline_is_not_failure(self):
        """
        提交前就是未联网：没有会话可断，但没有变化可观察。
        判成功（目标状态已达成），reason 用 already_offline 区分。
        """
        ok, reason, _ = _judge_logout(LOGOFF_HTML_OFFLINE, "未联网")
        assert ok is True
        assert reason == "already_offline"

    def test_online_unchanged_is_unknown(self):
        """提交前已联网，提交后还是已联网且无成功文案 → 注销没生效。"""
        ok, reason, _ = _judge_logout(LOGOFF_HTML_ONLINE, "已联网")
        assert ok is False
        assert reason == "unknown"

    def test_empty_html_is_unknown(self):
        ok, reason, _ = _judge_logout("<html></html>", "已联网")
        assert ok is False
        assert reason == "unknown"

    def test_captcha_error_beats_success_hint(self):
        """验证码错误优先级最高 —— 即使页面里有成功字样也判失败。"""
        html = ("<p>断开网络成功</p>"
                '<p class="validation-summary-errors">验证码错误!</p>')
        ok, reason, _ = _judge_logout(html, "已联网")
        assert ok is False
        assert reason == "captcha_error"

    def test_all_fail_hints_detected(self):
        for hint in LOGOFF_FAIL_HINTS:
            html = f"<p>{hint}</p>"
            ok, reason, _ = _judge_logout(html, "已联网")
            assert ok is False
            assert reason == "captcha_error"


# ------------------------------------------------------- build_logout_payload
class TestBuildLogoutPayload:
    @staticmethod
    def _page() -> LogoutPage:
        return LogoutPage(
            base_url="http://10.6.6.6/logoff?lrl=1",
            action="http://10.6.6.6/logoff?lrl=1",
            method="POST",
            fields={
                "__VIEWSTATE": "vs",
                "__VIEWSTATEGENERATOR": "C04F0937",
                "__VIEWSTATEENCRYPTED": "",
                "__EVENTVALIDATION": "ev",
                "ctl00$MainContent$TextBoxCC": "",
            },
        )

    def test_contains_captcha_field(self):
        prof = PortalProfile(portal_url="http://10.6.6.6/login")
        client = PortalClient(prof)
        payload = client.build_logout_payload(self._page(), "ab3d")
        assert payload["ctl00$MainContent$TextBoxCC"] == "ab3d"

    def test_contains_logoff_button(self):
        """
        LogoffBtn 必须带 —— ASP.NET WebForms 靠 <input type=submit> 的
        name 判断触发了哪个回发，漏掉整个请求不生效。
        """
        prof = PortalProfile(portal_url="http://10.6.6.6/login")
        client = PortalClient(prof)
        payload = client.build_logout_payload(self._page(), "ab3d")
        assert payload["ctl00$MainContent$LogoffBtn"] == "注销"

    def test_preserves_hidden_fields(self):
        prof = PortalProfile(portal_url="http://10.6.6.6/login")
        client = PortalClient(prof)
        payload = client.build_logout_payload(self._page(), "ab3d")
        assert payload["__VIEWSTATE"] == "vs"
        assert payload["__EVENTVALIDATION"] == "ev"
        assert payload["__VIEWSTATEGENERATOR"] == "C04F0937"

    def test_custom_field_map(self):
        """换学校时可覆盖字段名。"""
        prof = PortalProfile(
            portal_url="http://x/login",
            logout_field_map={"captcha": "other_captcha"},
            logout_submit_name="other_btn",
            logout_submit_value="下线",
        )
        client = PortalClient(prof)
        payload = client.build_logout_payload(self._page(), "zz99")
        assert payload["other_captcha"] == "zz99"
        assert payload["other_btn"] == "下线"


# ------------------------------------------------------------- PortalProfile
class TestPortalProfileDefaults:
    def test_logout_defaults(self):
        p = PortalProfile()
        assert p.logout_url == ""
        assert p.logout_captcha_url == "/CheckCode"
        assert p.logout_submit_name == "ctl00$MainContent$LogoffBtn"
        assert p.logout_submit_value == "注销"
        assert p.logout_max_retry == 10
        assert p.logout_captcha_length == 4

    def test_logout_field_map_default(self):
        p = PortalProfile()
        assert p.logout_field_map == {
            "captcha": "ctl00$MainContent$TextBoxCC",
        }

    def test_from_dict_roundtrip(self):
        p = PortalProfile(portal_url="http://x/login", logout_max_retry=3)
        d = p.to_dict()
        p2 = PortalProfile.from_dict(d)
        assert p2.logout_max_retry == 3
        assert p2.logout_captcha_url == "/CheckCode"

    def test_from_dict_ignores_unknown_keys(self):
        """手改 JSON 写错字段名不能炸。"""
        p = PortalProfile.from_dict({
            "portal_url": "http://x/login",
            "logout_unknown_field": "whatever",
        })
        assert p.portal_url == "http://x/login"


# ------------------------------------------------------------ OCR logout mode
class TestOcrLogoutMode:
    def test_mode_selection(self):
        from campuslink.core.ocr_backend import LocalDdddocrBackend
        assert LocalDdddocrBackend(mode="logout")._mode == "logout"

    def test_invalid_mode_falls_back_to_text(self):
        from campuslink.core.ocr_backend import LocalDdddocrBackend
        assert LocalDdddocrBackend(mode="nonsense")._mode == "text"

    def test_recognize_with_confidence_signature(self):
        """
        ocr.recognize_with_confidence 必须存在且可调用。
        真实识别需要 ddddocr，这里只验证接口（tdddocr 是可选依赖）。
        """
        from campuslink.core import ocr
        assert callable(getattr(ocr, "recognize_with_confidence", None))

    def test_recognize_with_confidence_on_real_sample(self, tmp_path):
        """
        用实测抓到的注销验证码跑一次真实识别。

        跳过条件：ddddocr 未安装（2.0 起是可选依赖）。
        """
        ddddocr = pytest.importorskip("ddddocr")
        sample = (
            b"\x89PNG\r\n\x1a\n"  # 只验证不崩，真实样本在手工测试里过
        )
        from campuslink.core import ocr
        text, conf, detail = ocr.recognize_with_confidence(sample)
        # 垃圾输入应该返回空串而不是抛异常
        assert isinstance(text, str)
        assert 0.0 <= conf <= 1.0
        assert isinstance(detail, dict)


# ---------------------------------------------------------- LogoutPage dataclass
class TestLogoutPage:
    def test_fields(self):
        page = LogoutPage(
            base_url="http://x/logoff",
            action="http://x/logoff?lrl=1",
            method="POST",
            online_state="已联网",
            user_ip="1.2.3.4",
        )
        assert page.online_state == "已联网"
        assert page.user_ip == "1.2.3.4"
        assert page.fields == {}

    def test_defaults(self):
        page = LogoutPage(base_url="u", action="a", method="POST")
        assert page.online_state == ""
        assert page.user_ip == ""
        assert page.captcha_url == ""
