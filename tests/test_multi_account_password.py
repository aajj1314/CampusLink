# -*- coding: utf-8 -*-
"""
多账号密码存储的契约测试（2026-11-18 新增）。

## 修的是什么

实测发现三处死代码 / 逻辑错误：

1. **`Account.label` 没有任何输入入口**
   数据结构有这个字段，`display_name()` 也支持它，但界面上
   没有地方能让用户填 —— 于是下拉框只能显示学工号，
   多账号时（比如"我的"和"室友的"）分不清。

2. **`_persist()` 把表单内容同步进 `accounts[索引]`，而它由每次
   输入变化触发（400ms 去抖）**
   后果：用户改了个学工号，400ms 后旧账号被悄悄改名，
   旧凭据条目成了孤儿，密码丢失。
   实测复现：凭据 OLD='' / NEW='new-password'（OLD 的密码没了）

3. **`_store_password` / `forget_password` / `_load_into_ui` 都走
   `cfg.effective_account`（顶层镜像字段）**
   单账号时两者一致，多账号下会存/删/读到错误的账号。

## 测试策略

`AppConfig` 那层是纯数据，直接测。
GUI 层（哪个函数在什么时候同步）用状态机模型测，
不构造 MainWindow —— 那需要 QApplication + 磁盘目录，在 CI 里跑不动。

GUI 实测流程见 docs/design/09 的「多账号密码实测」一节。
"""
from __future__ import annotations

import pytest

from campuslink import secrets
from campuslink.config import Account, AppConfig


# ==================================================== 凭据键的唯一性
class TestCredentialKey:
    """
    凭据条目名 = (门户, 账号)。这是多账号密码不互相覆盖的基础。
    """

    def test_different_accounts_different_keys(self):
        k1 = secrets.entry_name("学校", "A001")
        k2 = secrets.entry_name("学校", "B002")
        assert k1 != k2

    def test_different_portals_different_keys(self):
        k1 = secrets.entry_name("学校A", "A001")
        k2 = secrets.entry_name("学校B", "A001")
        assert k1 != k2

    def test_account_falls_back_to_student_id(self):
        """上网账号为空时用学工号 —— 门户里两者通常是同一个。"""
        a = Account(student_id="2021001", account="")
        assert a.effective_account() == "2021001"

    def test_account_prefers_explicit_account(self):
        a = Account(student_id="2021001", account="acc001")
        assert a.effective_account() == "acc001"


# ============================================ 表单同步的时机（核心修复）
class FormSyncModel:
    """
    `_persist` 与 `_sync_form_into_current_account` 的职责分离模型。

    修复前：_persist 里做同步 → 每次打字都改账号
    修复后：_persist 只存非账号字段；同步只在明确动作时发生
    """

    def __init__(self):
        self.cfg = AppConfig(profile_name="测试")
        self.cfg.add_account(Account(student_id="OLD-001", account="OLD-001"))
        self.cfg.current_account_index = 0
        self.cfg.set_current_account(self.cfg.accounts[0])
        # 表单（界面上正在编辑的内容）
        self.form_student = "OLD-001"
        self.form_account = "OLD-001"

    def persist_as_before(self):
        """
        修复前的行为：每次输入变化就同步。
        （保留这个方法是为了让测试能证明它有害）
        """
        c = self.cfg
        c.student_id = self.form_student
        c.account = self.form_account
        idx = c.current_account_index
        if 0 <= idx < len(c.accounts):
            old = c.accounts[idx]
            c.accounts[idx] = Account(
                label=old.label,
                student_id=c.student_id,
                account=c.account,
                operator=c.operator,
            )

    def persist_as_after(self):
        """修复后：只存非账号字段，不动 accounts。"""
        c = self.cfg
        c.student_id = self.form_student
        c.account = self.form_account

    def sync_form_into_current(self):
        """修复后：只在明确动作（勾保存/新增）时调用。"""
        c = self.cfg
        idx = c.current_account_index
        if not (0 <= idx < len(c.accounts)):
            return -1
        old = c.accounts[idx]
        if not (self.form_student or self.form_account):
            return idx
        c.accounts[idx] = Account(
            label=old.label,
            student_id=self.form_student,
            account=self.form_account,
            operator=c.operator,
        )
        c.set_current_account(c.accounts[idx])
        return idx


class TestFormSyncTiming:
    def test_before_behavior_clobbers_account(self):
        """
        修复前的行为确实有害 —— 这是回归测试的反面依据。

        用户只是把学工号框改成 NEW-999（还没确认），
        accounts[0] 就被改成了 NEW-999。
        """
        m = FormSyncModel()
        m.form_student = "NEW-999"
        m.persist_as_before()
        assert m.cfg.accounts[0].student_id == "NEW-999", (
            "这就是修复前的 bug：打字就改了账号")

    def test_after_behavior_keeps_account(self):
        """
        修复后：打字只改顶层镜像，accounts 不动。
        用户原来那个账号还在，凭据键也就不会漂移。
        """
        m = FormSyncModel()
        m.form_student = "NEW-999"
        m.persist_as_after()
        assert m.cfg.accounts[0].student_id == "OLD-001", (
            "打字不该改账号")
        assert m.cfg.student_id == "NEW-999", "顶层镜像应跟上"

    def test_explicit_sync_does_update(self):
        """明确动作（勾保存）时应该同步 —— 用户确实要改这个账号。"""
        m = FormSyncModel()
        m.form_student = "NEW-888"
        m.form_account = "NEW-888"
        m.sync_form_into_current()
        assert m.cfg.accounts[0].student_id == "NEW-888"

    def test_sync_preserves_label(self):
        """
        同步时 label 不能丢 —— 它是用户起的名字，不是表单字段。
        （这正是「死代码 1」的一半：label 应该由改名功能管，不由表单管）
        """
        m = FormSyncModel()
        m.cfg.accounts[0] = Account(
            label="我的号", student_id="OLD-001", account="OLD-001")
        m.form_student = "NEW-888"
        m.form_account = "NEW-888"
        m.sync_form_into_current()
        assert m.cfg.accounts[0].label == "我的号", "改名不该被表单同步冲掉"

    def test_sync_with_empty_form_does_nothing(self):
        """表单两个字段都空时不同步 —— 避免把账号清空。"""
        m = FormSyncModel()
        m.form_student = ""
        m.form_account = ""
        m.sync_form_into_current()
        assert m.cfg.accounts[0].student_id == "OLD-001"

    def test_multiple_typing_keeps_account_stable(self):
        """连续多次打字（模拟 400ms 去抖反复触发）账号保持稳定。"""
        m = FormSyncModel()
        for partial in ("N", "NE", "NEW", "NEW-", "NEW-9", "NEW-99", "NEW-999"):
            m.form_student = partial
            m.persist_as_after()
        assert m.cfg.accounts[0].student_id == "OLD-001"


# ======================================== 改名（补上死代码的入口）
class TestRenameLabel:
    def test_display_name_uses_label(self):
        a = Account(label="室友的号", student_id="2021002")
        assert a.display_name() == "室友的号"

    def test_display_name_falls_back_when_empty(self):
        a = Account(label="", student_id="2021002")
        assert a.display_name() == "2021002"

    def test_display_name_falls_back_to_placeholder(self):
        assert Account().display_name() == "（未命名）"

    def test_rename_does_not_touch_credentials_key(self):
        """
        改名不影响凭据键 —— label 只是显示名。
        这是改名功能能安全存在的前提。
        """
        a1 = Account(label="旧名", student_id="A001", account="A001")
        a2 = Account(label="新名", student_id="A001", account="A001")
        assert a1.effective_account() == a2.effective_account()

    def test_account_labels_list(self):
        c = AppConfig()
        c.add_account(Account(label="我", student_id="A1"))
        c.add_account(Account(label="", student_id="A2"))
        c.add_account(Account(label="备用", student_id="A3"))
        assert c.account_labels() == ["我", "A2", "备用"]


# ================================ 凭据键漂移时的清理（修复 2 的核心）
class TestCredentialMigration:
    """
    用户改学工号时，凭据键会变。
    旧键的密码必须删掉，否则永远留着一个孤儿条目。
    """

    @staticmethod
    def simulate_change(config: AppConfig, new_student: str,
                        new_password: str, store=None) -> tuple[str, str]:
        """
        模拟 _store_password 的迁移逻辑。

        store 是凭据字典（测试里用内存 dict 替代 keyring）。
        返回 (旧键, 新键)。
        """
        store = {} if store is None else store
        idx = config.current_account_index
        old = config.accounts[idx]
        old_key = old.effective_account()

        # 同步表单
        config.accounts[idx] = Account(
            label=old.label,
            student_id=new_student,
            account=new_student,
            operator=old.operator,
        )
        config.set_current_account(config.accounts[idx])
        new_key = config.accounts[idx].effective_account()

        store[new_key] = new_password
        if old_key and old_key != new_key:
            store.pop(old_key, None)

        return old_key, new_key

    def test_old_key_removed(self):
        c = AppConfig(profile_name="测试")
        c.add_account(Account(student_id="OLD-001", account="OLD-001"))
        c.current_account_index = 0
        c.set_current_account(c.accounts[0])

        store = {"OLD-001": "old-password"}
        old_key, new_key = self.simulate_change(c, "NEW-888", "new-password", store)

        assert old_key == "OLD-001"
        assert new_key == "NEW-888"
        assert store.get("OLD-001") is None, "旧凭据必须清理，否则成孤儿"
        assert store.get("NEW-888") == "new-password"

    def test_same_key_keeps_password(self):
        """学工号没变时不清凭据 —— 只是改密码。"""
        c = AppConfig(profile_name="测试")
        c.add_account(Account(student_id="A001", account="A001"))
        c.current_account_index = 0
        c.set_current_account(c.accounts[0])

        store = {"A001": "old-password"}
        old_key, new_key = self.simulate_change(c, "A001", "new-password", store)

        assert old_key == new_key == "A001"
        assert store.get("A001") == "new-password"


# ============================== 多账号密码互不干扰（端到端语义）
class TestMultiAccountPasswordIsolation:
    """
    用内存 store 验证「多账号 + 多密码」的核心语义。

    这条保证的是：切换账号时读到的密码一定是那个账号的。
    """

    def test_each_account_has_own_password(self):
        store: dict[str, str] = {}
        c = AppConfig(profile_name="测试")
        c.add_account(Account(label="我", student_id="A001", account="A001"))
        c.add_account(Account(label="室友", student_id="B002", account="B002"))

        # 分别存
        store["A001"] = "pwd-A"
        store["B002"] = "pwd-B"

        # 切到 A
        c.current_account_index = 0
        c.set_current_account(c.accounts[0])
        assert store[c.current_account().effective_account()] == "pwd-A"

        # 切到 B
        c.current_account_index = 1
        c.set_current_account(c.accounts[1])
        assert store[c.current_account().effective_account()] == "pwd-B"

    def test_switch_back_gets_original_password(self):
        store: dict[str, str] = {}
        c = AppConfig(profile_name="测试")
        c.add_account(Account(student_id="A001", account="A001"))
        c.add_account(Account(student_id="B002", account="B002"))
        store["A001"] = "pwd-A"
        store["B002"] = "pwd-B"

        for idx, expect in ((0, "pwd-A"), (1, "pwd-B"), (0, "pwd-A"), (1, "pwd-B")):
            c.current_account_index = idx
            c.set_current_account(c.accounts[idx])
            got = store[c.current_account().effective_account()]
            assert got == expect, f"第 {idx} 套账号密码读错"

    def test_deleting_one_account_leaves_other_intact(self):
        store: dict[str, str] = {"A001": "pwd-A", "B002": "pwd-B"}
        c = AppConfig(profile_name="测试")
        c.add_account(Account(student_id="A001", account="A001"))
        c.add_account(Account(student_id="B002", account="B002"))

        # 删第一个（模拟 _on_account_del 先删凭据再删条目）
        victim = c.accounts[0]
        store.pop(victim.effective_account(), None)
        c.remove_account(0)

        assert store.get("A001") is None
        assert store.get("B002") == "pwd-B", "另一个账号的密码不该被连带删除"
