# -*- coding: utf-8 -*-
"""
门户协议层的测试。

夹具全部来自**真实抓包**（2026-10-14 对桂林信息科技学院门户的实跑响应），
不是编造的 HTML —— 因为这一层的坑，全都是从真实页面里踩出来的：

  · 成功和失败共用同一个提示容器，只能靠文案区分
  · 成功时登录表单照旧渲染、不跳转
  · 提交按钮必须带上，否则 ASP.NET 整条回发不触发
  · 隐藏域（__VIEWSTATE 等）必须原样回传
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from campuslink.core.models import Credentials, PortalProfile
from campuslink.core.portal import (PortalClient, check_success, extract_message,
                                    friendly_error)
from tests.conftest import make_response


# =================================================================== 成功判定
class TestSuccessCheck:
    def test_real_success_response(self, success_html):
        """真实成功响应：容器里写「联网成功!」。"""
        rule = {"type": "contains", "value": "联网成功"}
        assert check_success(make_response(success_html), rule) is True

    def test_real_captcha_error_response(self, captcha_error_html):
        assert check_success(make_response(captcha_error_html),
                             {"type": "contains", "value": "联网成功"}) is False

    def test_real_rejected_response(self, rejected_html):
        assert check_success(make_response(rejected_html),
                             {"type": "contains", "value": "联网成功"}) is False

    def test_success_page_still_contains_login_form(self, success_html):
        """
        这是本项目最容易写错的地方：
        成功页里登录表单**还在**，所以任何「表单消失即成功」的推断都会误判。
        """
        assert "SSOPWD" in success_html
        old_rule = {"type": "all", "value": [
            {"type": "not_regex", "value": r"<p class=\"validation-summary-errors\">\s*\S"},
            {"type": "not_regex", "value": r"MainContent\$SSOPWD"},
        ]}
        assert check_success(make_response(success_html), old_rule) is False, \
            "旧式推断条件在真实成功页上必然失败 —— 这就是它被废弃的原因"

    def test_composite_rules(self, success_html, captcha_error_html):
        ok = make_response(success_html)
        bad = make_response(captcha_error_html)
        assert check_success(ok, {"type": "all", "value": [
            {"type": "contains", "value": "联网成功"},
            {"type": "not_contains", "value": "错误"}]}) is True
        assert check_success(bad, {"type": "any", "value": [
            {"type": "contains", "value": "验证码错误"},
            {"type": "contains", "value": "联网成功"}]}) is True

    def test_other_rule_types(self, success_html):
        r = make_response(success_html)
        assert check_success(r, {"type": "regex", "value": "联网.{0,2}成功"}) is True
        assert check_success(r, {"type": "not_regex", "value": "不存在的串"}) is True
        assert check_success(r, {"type": "status", "value": 200}) is True
        assert check_success(r, {"type": "url_contains", "value": "10.6.6.6"}) is True


# =================================================================== 提示提取
class TestExtractMessage:
    def test_picks_container_not_form_labels(self, success_html):
        """
        必须优先读提示容器。
        上一版全文扫关键词，会先命中「上网账号:」这类表单标签，
        把真正的提示挡在后面，非常误导。
        """
        assert extract_message(make_response(success_html)) == "联网成功!"

    def test_captcha_error(self, captcha_error_html):
        assert extract_message(make_response(captcha_error_html)) == "验证码错误!"

    def test_rejected(self, rejected_html):
        msg = extract_message(make_response(rejected_html))
        assert "未实名" in msg


class TestFriendlyError:
    def test_translates_known_messages(self):
        # 返回的是给普通用户看的人话，所以断言的是「翻译后的措辞」，
        # 而不是原关键词 —— 原话里那些 NAS 术语用户根本看不懂。
        assert "密码" in friendly_error("认证请求被拒绝，密码错误")
        assert "其他设备" in friendly_error("账号超过最大并发终端数")
        assert "欠费" in friendly_error("Account OverDue")
        assert "实名" in friendly_error("用户未实名注册运营商上网账号!")
        assert "下线" in friendly_error("Reject by concurrency control")

    def test_unknown_message_passes_through(self):
        assert friendly_error("某种没见过的错误") == "某种没见过的错误"


# =================================================================== 表单解析
class TestFormParsing:
    def test_parses_real_login_page(self, login_page_html):
        soup = BeautifulSoup(login_page_html, "lxml")
        form = PortalClient._pick_login_form(soup)
        assert form is not None, "真实登录页里应该能挑出含密码框的表单"

        fields = PortalClient._parse_fields(form)
        # ASP.NET 的隐藏域必须全部保留
        for key in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
            assert key in fields, f"隐藏域 {key} 丢了"
        # 提交按钮必须保留 —— 漏了 ASP.NET 不会触发回发
        assert "ctl00$MainContent$LoginBtn" in fields
        assert fields["ctl00$MainContent$LoginBtn"] == "登录"
        # 五个业务字段
        for key in ("ctl00$MainContent$SSOGUITID", "ctl00$MainContent$SSOACC",
                    "ctl00$MainContent$SSOPWD", "ctl00$MainContent$TextBoxCC"):
            assert key in fields

    def test_locates_captcha_image(self, login_page_html):
        soup = BeautifulSoup(login_page_html, "lxml")
        url = PortalClient._locate_captcha(soup, "http://10.6.6.6/login")
        assert url == "http://10.6.6.6/CheckCode"

    def test_resubmits_button_but_not_decorative_buttons(self):
        html = ('<form action="/x" method="post">'
                '<input name="a" value="1">'
                '<input type="submit" name="go" value="登录">'
                '<input type="button" name="skip" value="取消">'
                '<input type="reset" name="rst" value="重置">'
                '<input type="image" name="img" src="b.png">'
                '</form>')
        fields = PortalClient._parse_fields(BeautifulSoup(html, "lxml").form)
        assert "a" in fields and "go" in fields
        assert "skip" not in fields and "rst" not in fields and "img" not in fields


# =================================================================== 提交内容
class TestBuildPayload:
    def _profile(self) -> PortalProfile:
        return PortalProfile(
            name="test",
            portal_url="http://10.6.6.6/login",
            field_map={
                "operator": "op",
                "student_id": "sid",
                "account": "acc",
                "password": "pwd",
                "captcha": "cc",
            },
        )

    def _page(self):
        from campuslink.core.portal import LoginPage
        return LoginPage(base_url="http://10.6.6.6/login",
                         action="http://10.6.6.6/login", method="POST",
                         fields={"__VIEWSTATE": "abc", "cc": ""})

    def test_keeps_hidden_fields(self):
        c = PortalClient(self._profile())
        payload = c.build_payload(self._page(), Credentials("s", "a", "p"), "XY12")
        assert payload["__VIEWSTATE"] == "abc", "隐藏域必须原样带回"
        assert payload["sid"] == "s" and payload["acc"] == "a"
        assert payload["pwd"] == "p" and payload["cc"] == "XY12"

    def test_pipe_syntax_fills_several_fields(self):
        """field_map 支持 'a|b'，一次填多个同义字段。"""
        prof = self._profile()
        prof.field_map["password"] = "pwd|pwdText"
        c = PortalClient(prof)
        payload = c.build_payload(self._page(), Credentials("s", "a", "secret"), "")
        assert payload["pwd"] == "secret" and payload["pwdText"] == "secret"

    def test_empty_values_are_skipped(self):
        c = PortalClient(self._profile())
        payload = c.build_payload(self._page(), Credentials("s", "a", ""), "")
        assert "pwd" not in payload, "空密码不该塞进提交内容"


# =================================================================== 档案
class TestProfile:
    def test_roundtrip(self):
        p = PortalProfile(name="x", portal_url="http://a/", ocr_strategies=("raw",))
        assert PortalProfile.from_dict(p.to_dict()).ocr_strategies == ("raw",)

    def test_ignores_unknown_keys(self):
        p = PortalProfile.from_dict({"name": "x", "portal_url": "http://a/",
                                     "note": "文档性字段", "乱七八糟": 1})
        assert p.name == "x"

    def test_builtin_profile_loads(self):
        from campuslink import profiles
        p = profiles.load_profile("桂林信息科技学院")
        assert p.portal_url.startswith("http")
        assert p.field_map["password"] == "ctl00$MainContent$SSOPWD"
        assert p.ocr_strategies == ("raw", "gray")
