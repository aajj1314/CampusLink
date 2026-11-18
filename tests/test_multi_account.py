# -*- coding: utf-8 -*-
"""
多账号配置的单元测试（2026-11-18 新增）。

覆盖 range：
  · Account 数据类（display_name / effective_account）
  · AppConfig 的账号增删改查
  · 旧配置自动迁移（只有 student_id/account → accounts 一条）
  · 序列化往返
  · 索引越界保护
  · 多账号与 secrets 的配合（凭据条目名不冲突）

GUI 层的切换逻辑（注销成功才切）由手工集成测试覆盖，见 docs/design/09。
"""
from __future__ import annotations

import pytest

from campuslink.config import Account, AppConfig
from campuslink import secrets


# ------------------------------------------------------------------ Account
class TestAccount:
    def test_display_name_prefers_label(self):
        a = Account(label="我", student_id="2021001")
        assert a.display_name() == "我"

    def test_display_name_falls_back_to_account(self):
        a = Account(label="", student_id="2021001", account="acc001")
        assert a.display_name() == "acc001"

    def test_display_name_falls_back_to_student_id(self):
        a = Account(label="", student_id="2021001", account="")
        assert a.display_name() == "2021001"

    def test_display_name_empty(self):
        assert Account().display_name() == "（未命名）"

    def test_effective_account_prefers_account(self):
        a = Account(account="acc001", student_id="2021001")
        assert a.effective_account() == "acc001"

    def test_effective_account_falls_back(self):
        a = Account(account="", student_id="2021001")
        assert a.effective_account() == "2021001"

    def test_to_dict_roundtrip(self):
        a = Account(label="我", student_id="2021001", account="acc",
                    operator="中国电信")
        d = a.to_dict()
        assert isinstance(d, dict)
        a2 = Account.from_dict(d)
        assert a2.label == "我"
        assert a2.operator == "中国电信"

    def test_from_dict_ignores_unknown(self):
        a = Account.from_dict({"student_id": "x", "unknown_field": 1})
        assert a.student_id == "x"


# -------------------------------------------------------------- AppConfig 多账号
class TestMultiAccount:
    def test_empty_config(self):
        c = AppConfig()
        assert c.accounts == []
        assert c.current_account_index == -1

    def test_add_account(self):
        c = AppConfig()
        i = c.add_account(Account(label="我", student_id="A1"))
        assert i == 0
        assert len(c.accounts) == 1
        assert c.accounts[0].label == "我"

    def test_add_duplicate_updates(self):
        """同学工号+账号时更新而不是重复添加。"""
        c = AppConfig()
        c.add_account(Account(label="旧名", student_id="A1"))
        c.add_account(Account(label="新名", student_id="A1"))
        assert len(c.accounts) == 1
        assert c.accounts[0].label == "新名"

    def test_add_different_accounts(self):
        c = AppConfig()
        c.add_account(Account(student_id="A1"))
        c.add_account(Account(student_id="A2"))
        assert len(c.accounts) == 2

    def test_remove_account(self):
        c = AppConfig()
        c.add_account(Account(student_id="A1"))
        c.add_account(Account(student_id="A2"))
        assert c.remove_account(0) is True
        assert len(c.accounts) == 1
        assert c.accounts[0].student_id == "A2"

    def test_remove_out_of_range(self):
        c = AppConfig()
        assert c.remove_account(0) is False
        assert c.remove_account(-1) is False

    def test_remove_current_resets_to_top_level(self):
        c = AppConfig()
        c.add_account(Account(student_id="A1"))
        c.current_account_index = 0
        c.set_current_account(c.accounts[0])
        c.remove_account(0)
        assert c.current_account_index == -1
        assert c.student_id == ""

    def test_remove_before_current_shifts_index(self):
        c = AppConfig()
        c.add_account(Account(student_id="A1"))
        c.add_account(Account(student_id="A2"))
        c.add_account(Account(student_id="A3"))
        c.current_account_index = 2
        c.remove_account(0)
        assert c.current_account_index == 1
        assert c.accounts[1].student_id == "A3"

    def test_current_account_via_index(self):
        c = AppConfig()
        c.add_account(Account(label="A", student_id="A1"))
        c.add_account(Account(label="B", student_id="A2"))
        c.current_account_index = 1
        assert c.current_account().label == "B"

    def test_current_account_fallback_to_top_level(self):
        """索引 -1 时用顶层三个字段（旧行为）。"""
        c = AppConfig(student_id="OLD", account="OLD")
        assert c.current_account().student_id == "OLD"

    def test_set_current_account_mirrors(self):
        c = AppConfig()
        c.add_account(Account(student_id="A1", account="a1"))
        c.current_account_index = 0
        c.set_current_account(Account(student_id="A1", account="a1",
                                      operator="中国联通"))
        assert c.student_id == "A1"
        assert c.account == "a1"
        assert c.operator == "中国联通"

    def test_account_labels(self):
        c = AppConfig()
        c.add_account(Account(label="我", student_id="A1"))
        c.add_account(Account(label="", student_id="A2"))
        assert c.account_labels() == ["我", "A2"]


# ------------------------------------------------------------- 序列化与迁移
class TestSerialization:
    def test_roundtrip(self):
        c = AppConfig()
        c.add_account(Account(label="我", student_id="A1", account="a1"))
        c.add_account(Account(label="室友", student_id="A2", account="a2"))
        c.current_account_index = 1
        c.set_current_account(c.accounts[1])

        d = c.to_dict()
        # to_dict 里 accounts 应该已被递归成 dict
        assert isinstance(d["accounts"][0], dict)

        c2 = AppConfig.from_dict(d)
        assert len(c2.accounts) == 2
        assert isinstance(c2.accounts[0], Account)
        assert c2.accounts[0].label == "我"
        assert c2.current_account_index == 1
        assert c2.current_account().label == "室友"

    def test_migrate_legacy_config(self):
        """
        旧配置（只有 student_id/account/operator）加载时
        自动迁移成 accounts 里的一条，索引指向它。
        """
        legacy = {
            "student_id": "OLD001",
            "account": "OLD001",
            "operator": "中国电信",
            "check_interval": 60,
            "auto_connect": True,
        }
        c = AppConfig.from_dict(legacy)
        assert len(c.accounts) == 1
        assert c.accounts[0].student_id == "OLD001"
        assert c.accounts[0].operator == "中国电信"
        assert c.current_account_index == 0
        # 顶层字段要保持原样（向后兼容）
        assert c.student_id == "OLD001"

    def test_migrate_empty_config(self):
        """没有账号的配置不该造出空 Account。"""
        c = AppConfig.from_dict({"check_interval": 60})
        assert c.accounts == []
        assert c.current_account_index == -1

    def test_index_out_of_range_protected(self):
        """手改 JSON 把索引写成 99，不能崩。"""
        c = AppConfig.from_dict({
            "student_id": "A1",
            "accounts": [{"student_id": "A1"}],
            "current_account_index": 99,
        })
        assert c.current_account_index == -1

    def test_negative_index_protected(self):
        c = AppConfig.from_dict({
            "student_id": "A1",
            "accounts": [{"student_id": "A1"}],
            "current_account_index": -5,
        })
        assert c.current_account_index == -1

    def test_accounts_not_list(self):
        """
        accounts 被写成字符串时不能崩。

        注意预期行为：`accounts` 非法但顶层有 student_id 时，
        会走**旧配置迁移**路径，自动建一条 —— 这是对的，
        因为用户的核心数据（学工号）还在，不该因为一个字段格式错就丢掉。
        """
        c = AppConfig.from_dict({"student_id": "A1", "accounts": "oops"})
        assert isinstance(c.accounts, list)
        assert len(c.accounts) == 1
        assert c.accounts[0].student_id == "A1"

    def test_accounts_not_list_no_legacy_data(self):
        """没有任何账号数据时，非法的 accounts 应该被清空。"""
        c = AppConfig.from_dict({"accounts": "oops"})
        assert c.accounts == []
        assert c.current_account_index == -1

    def test_accounts_with_dicts(self):
        c = AppConfig.from_dict({
            "accounts": [
                {"label": "A", "student_id": "A1"},
                {"label": "B", "student_id": "A2"},
            ],
            "current_account_index": 1,
        })
        assert len(c.accounts) == 2
        assert isinstance(c.accounts[0], Account)
        assert c.accounts[1].label == "B"


# ----------------------------------------------------- 与 secrets 的配合
class TestSecretsIntegration:
    def test_entry_name_differs_per_account(self):
        """
        不同账号的凭据条目名必须不同，否则多账号会互相覆盖密码。
        """
        n1 = secrets.entry_name("桂林信息科技学院", "A001")
        n2 = secrets.entry_name("桂林信息科技学院", "B002")
        assert n1 != n2
        assert "A001" in n1
        assert "B002" in n2

    def test_entry_name_differs_per_portal(self):
        n1 = secrets.entry_name("学校A", "acc")
        n2 = secrets.entry_name("学校B", "acc")
        assert n1 != n2

    def test_entry_name_handles_empty(self):
        """门户外/账号为空时不能造出非法条目名。"""
        n = secrets.entry_name("", "")
        assert n == "default:"


# ------------------------------------------------------------- logout 配置
class TestLogoutConfig:
    def test_default_confidence(self):
        assert AppConfig().logout_min_confidence == 0.90

    def test_from_dict_float_coercion(self):
        c = AppConfig.from_dict({"logout_min_confidence": "0.85"})
        assert c.logout_min_confidence == 0.85

    def test_from_dict_bad_float_falls_back(self):
        c = AppConfig.from_dict({"logout_min_confidence": "not-a-number"})
        assert c.logout_min_confidence == 0.90        # 默认值
