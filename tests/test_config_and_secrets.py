# -*- coding: utf-8 -*-
"""
配置与凭据存储的测试。

重点验证任务书的三条硬性要求：
    1. 密码不得明文存储在配置里
    2. 敏感信息不得出现在日志里
    3. 配置要能承受手改坏、半截写入、缺字段这些情况
"""
from __future__ import annotations

import json
import logging

import pytest

from campuslink import secrets
from campuslink.config import AppConfig
from campuslink.logging_setup import (MASK, forget_secrets, get_logger,
                                      register_secret, scrub, setup)


# =================================================================== 配置
class TestAppConfig:
    def test_has_no_password_field(self):
        """结构上就不该有密码字段 —— 不是靠约定，是靠没有这个键。"""
        keys = set(AppConfig().__dataclass_fields__)
        assert not any("pass" in k.lower() or "pwd" in k.lower() for k in keys)
        assert "password" not in json.dumps(AppConfig().to_dict(), ensure_ascii=False)

    def test_roundtrip(self, tmp_path):
        p = tmp_path / "config.json"
        c = AppConfig(student_id="20240001", account="20240001",
                      operator="中国电信", auto_connect=False, check_interval=120)
        assert c.save(p)
        assert p.exists()
        got = AppConfig.load(p)
        assert got.student_id == "20240001"
        assert got.operator == "中国电信"
        assert got.auto_connect is False
        assert got.check_interval == 120

    def test_missing_file_returns_defaults(self, tmp_path):
        got = AppConfig.load(tmp_path / "nope.json")
        assert got.student_id == ""
        assert got.auto_connect is True

    def test_corrupt_file_does_not_raise(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text("{ 这不是合法 json", encoding="utf-8")
        assert AppConfig.load(p).student_id == ""

    def test_wrong_types_are_coerced(self, tmp_path):
        """手改过的 JSON 很容易把 true 写成 "true"。"""
        p = tmp_path / "config.json"
        p.write_text(json.dumps({
            "auto_connect": "false",
            "check_interval": "300",
            "start_minimized": 0,
            "profile_override": "本应是对象",
            "不认识的字段": 1,
        }), encoding="utf-8")
        c = AppConfig.load(p)
        assert c.auto_connect is False
        assert c.check_interval == 300
        assert c.start_minimized is False
        assert c.profile_override == {}

    def test_atomic_save_leaves_no_temp_file(self, tmp_path):
        c = AppConfig(student_id="x")
        c.save(tmp_path / "config.json")
        leftovers = [f.name for f in tmp_path.iterdir() if f.suffix == ".tmp"]
        assert leftovers == []

    def test_account_falls_back_to_student_id(self):
        assert AppConfig(student_id="S1", account="").effective_account == "S1"
        assert AppConfig(student_id="S1", account="A1").effective_account == "A1"

    def test_summary_hides_password(self):
        s = AppConfig(student_id="S1").summary()
        assert "已保存" in s["密码"] or "未保存" in s["密码"]


# =================================================================== 凭据
class TestSecrets:
    def test_backend_detected(self):
        # Windows 上应当是凭据管理器；其它平台可能是 keyring 的其它后端。
        # 这里只要求「能判断」，不要求一定是可用的。
        assert isinstance(secrets.available(), bool)
        assert isinstance(secrets.status_text(), str)

    def test_entry_name_includes_portal_and_account(self):
        """条目名带门户，避免换学校/换账号时互相覆盖。"""
        assert secrets.entry_name("A校", "u1") == "A校:u1"
        assert secrets.entry_name("A校", "u1") != secrets.entry_name("B校", "u1")

    def test_save_empty_password_is_rejected(self):
        assert secrets.save("portal", "acc", "") is False

    def test_load_returns_empty_when_not_found(self):
        assert secrets.load("不存在的门户", "不存在的人") == ""

    def test_delete_is_idempotent(self):
        assert secrets.delete("不存在的门户", "不存在的人") is True


# =================================================================== 日志脱敏
class TestLogRedaction:
    def test_registered_secret_is_masked(self):
        forget_secrets()
        register_secret("MyS3cretPwd")
        assert "MyS3cretPwd" not in scrub("登录密码是 MyS3cretPwd 请勿外传")
        assert MASK in scrub("登录密码是 MyS3cretPwd 请勿外传")

    def test_short_secrets_are_not_registered(self):
        """太短的串不登记，否则正常文本会被打成星号。"""
        forget_secrets()
        register_secret("ab")
        assert scrub("abcabc") == "abcabc"

    def test_pattern_fallback_masks_password_pairs(self):
        text = "payload: username=bob&password=hunter2&captcha=9K2M"
        out = scrub(text)
        assert "hunter2" not in out
        assert "9K2M" in out, "验证码不是敏感信息，不该被误伤"

    def test_cookie_and_token_masked(self):
        out = scrub("Cookie: ASP.NET_SessionId=abcdef123456")
        assert "abcdef123456" not in out

    def test_log_file_never_contains_password(self, tmp_path):
        """
        端到端验证：把一个密码登记后写进日志，再读文件确认它不在里面。

        这条对应任务书「禁止将密码、Token、Cookie 等输出到日志」。
        """
        forget_secrets()
        pwd = "P@ssw0rd-Should-Never-Appear"

        import campuslink.logging_setup as ls
        ls._configured = False
        for h in list(logging.getLogger(ls.LOGGER_NAME).handlers):
            logging.getLogger(ls.LOGGER_NAME).removeHandler(h)

        setup(console=False, log_dir=tmp_path)
        register_secret(pwd)

        log = get_logger("test")
        log.info("用户提交的密码是 %s", pwd)
        log.warning("完整 payload: username=bob&password=%s", pwd)
        for h in log.handlers or logging.getLogger(ls.LOGGER_NAME).handlers:
            h.flush()

        content = (tmp_path / "campuslink.log").read_text(encoding="utf-8")
        assert pwd not in content, "密码泄漏到日志里了！"
        assert MASK in content

        # 收尾：恢复正常状态，别影响别的测试
        ls._configured = False
        for h in list(logging.getLogger(ls.LOGGER_NAME).handlers):
            logging.getLogger(ls.LOGGER_NAME).removeHandler(h)
        forget_secrets()
