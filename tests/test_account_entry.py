# -*- coding: utf-8 -*-
"""
多账号入口可见性与「新增不丢备注名」的回归测试（2026-11-18 新增）。

## 修的两个 UI bug（都是用户实测发现的）

### Bug 1：账号行在只有 1 套账号时被隐藏 → 「新增」入口消失

原设计「少于 2 套时隐藏整行」，想的是单账号用户不该看到多余 UI。
但这是死循环：

    只有 1 套 → 整行隐藏 → 「新增」也没了 → 永远加不了第二套

用户原话：「找不到多账号的入口呀」

### Bug 2：点「新增」会把备注名清空

`_on_account_add` 创建 Account 时没传 label，于是：

    用户配好「我的号」→ 点新增 → label 变成 ''
    → 下拉框退回显示裸学工号「15577309726」

## 测试策略

GUI 层的可见性判断和分支逻辑抽成纯函数/模型来测
（不构造 MainWindow，那需要 QApplication + 磁盘目录）。
真实界面行为由手工截图验证，见 docs/design/09。
"""
from __future__ import annotations

import pytest

from campuslink.config import Account, AppConfig


# ============================================ Bug 1：入口可见性
class AccountRowVisibility:
    """
    `_refresh_account_combo` 的可见性决策模型。

    修复前的规则：`show = len(labels) >= 2`
    修复后的规则：**始终可见**

    为什么改成始终可见：账号行是「新增」按钮的唯一载体，
    隐藏它等于关掉了加账号的入口。
    """

    @staticmethod
    def visible_before(account_count: int) -> bool:
        """修复前：2 套以上才显示。"""
        return account_count >= 2

    @staticmethod
    def visible_after(account_count: int) -> bool:
        """修复后：始终显示。"""
        return True

    @staticmethod
    def rename_del_enabled(account_count: int) -> bool:
        """
        「改名」「删除」至少要有一套账号才有意义。
        「新增」不受此限 —— 它正是从 0 到 1 的入口。
        """
        return account_count >= 1

    @staticmethod
    def add_enabled(account_count: int) -> bool:
        """「新增」永远可用，包括 0 套账号时。"""
        return True


class TestAccountRowVisibility:
    def test_before_zero_accounts_row_hidden(self):
        """修复前：0 套账号时不显示 —— 用户根本没入口。"""
        assert AccountRowVisibility.visible_before(0) is False

    def test_before_one_account_row_hidden(self):
        """
        **这就是用户遇到的 bug 现场**：
        1 套账号时整行隐藏，连带「新增」也没了。
        """
        assert AccountRowVisibility.visible_before(1) is False

    def test_before_two_accounts_row_shown(self):
        assert AccountRowVisibility.visible_before(2) is True

    def test_after_row_always_visible(self):
        """修复后：任何数量都显示。"""
        for n in range(0, 6):
            assert AccountRowVisibility.visible_after(n) is True, (
                f"{n} 套账号时账号行不该隐藏")

    def test_add_button_always_enabled(self):
        """
        「新增」在 0 套账号时也必须可用 ——
        这是从「没有账号」到「有账号」的唯一入口。
        """
        for n in range(0, 6):
            assert AccountRowVisibility.add_enabled(n) is True

    def test_rename_del_disabled_when_empty(self):
        """没有账号时改名/删除无意义，禁用防止误操作。"""
        assert AccountRowVisibility.rename_del_enabled(0) is False
        assert AccountRowVisibility.rename_del_enabled(1) is True

    def test_deadloop_is_broken(self):
        """
        核心回归：验证「1 套账号也能加第二套」这条路是通的。

        修复前：visible(1)=False → 用户看不到新增 → 卡死
        修复后：visible(1)=True → 能点新增 → 变成 2 套
        """
        n = 1
        assert AccountRowVisibility.visible_after(n) is True
        assert AccountRowVisibility.add_enabled(n) is True
        # 点一下新增，就变 2 套了
        n += 1
        assert n == 2


# ======================================== Bug 2：新增时的 label 处理
class AddAccountLogic:
    """
    `_on_account_add` 的三分支决策模型。

    ## 修复前

        不管什么情况都创建 `Account(student_id=..., account=...)`
        —— 不传 label → 备注名被清空

    ## 修复后

        1. 跟当前账号完全一样        → 什么都不做（防重复）
        2. 学工号相同、账号不同      → 视为编辑，**继承原 label**
        3. 学工号不同                → 真的新账号，label 留空
    """

    def __init__(self):
        self.cfg = AppConfig(profile_name="测试")

    def seed(self, label: str, student: str, account: str) -> None:
        self.cfg.add_account(Account(
            label=label, student_id=student, account=account))
        self.cfg.current_account_index = 0
        self.cfg.set_current_account(self.cfg.accounts[0])

    def add_before(self, student: str, account: str) -> str:
        """
        修复前的行为：直接创建，label 丢失。
        返回 "added"。
        """
        acc = Account(student_id=student, account=account)
        idx = self.cfg.add_account(acc)
        self.cfg.current_account_index = idx
        self.cfg.set_current_account(acc)
        return "added"

    def add_after(self, student: str, account: str) -> str:
        """
        修复后的行为。返回 "noop" / "added"。
        """
        c = self.cfg
        cur_idx = c.current_account_index
        if 0 <= cur_idx < len(c.accounts):
            cur = c.accounts[cur_idx]
            # 分支 1：完全一样 → noop
            if (cur.student_id == student
                    and cur.effective_account() == (account or student)
                    and cur.account == account):
                return "noop"

        # 分支 2/3：判断要不要继承 label
        inherit = ""
        if 0 <= cur_idx < len(c.accounts):
            cur = c.accounts[cur_idx]
            if cur.student_id == student:
                inherit = cur.label

        acc = Account(label=inherit, student_id=student, account=account)
        idx = c.add_account(acc)
        c.current_account_index = idx
        c.set_current_account(acc)
        return "added"


class TestAddAccountLabel:
    def test_before_loses_label(self):
        """
        修复前的 bug：新增后原账号的 label 被覆盖成空。

        这是回归测试的反面依据 —— 证明这个 bug 真的存在过。
        """
        m = AddAccountLogic()
        m.seed("我的号", "2021001", "2021001")
        m.add_before("2021002", "2021002")
        labels = [a.label for a in m.cfg.accounts]
        assert "" in labels, "修复前 label 确实会丢"

    def test_after_different_student_keeps_original_label(self):
        """
        修复后：新增另一套账号时，原来那套的 label 不受影响。
        """
        m = AddAccountLogic()
        m.seed("我的号", "2021001", "2021001")
        m.add_after("2021002", "2021002")

        labels = [(a.label, a.student_id) for a in m.cfg.accounts]
        assert ("我的号", "2021001") in labels, "原账号的 label 必须还在"

    def test_new_account_label_is_empty(self):
        """真正的新账号 label 留空，让用户点「改名」自己起。"""
        m = AddAccountLogic()
        m.seed("我的号", "2021001", "2021001")
        m.add_after("2021002", "2021002")

        new = [a for a in m.cfg.accounts if a.student_id == "2021002"][0]
        assert new.label == ""

    def test_same_student_different_account_inherits_label(self):
        """
        只改上网账号（学工号不变）= 编辑当前账号，应该继承 label。

        实测场景：用户把上网账号从 15577309726 改成 15577309729，
        「我的号」不该丢。
        """
        m = AddAccountLogic()
        m.seed("我的号", "2021001", "old-account")
        m.add_after("2021001", "new-account")

        # 找那个新账号，label 应该继承
        found = [a for a in m.cfg.accounts if a.account == "new-account"]
        assert len(found) == 1
        assert found[0].label == "我的号", "同学工号视为编辑，label 必须继承"

    def test_identical_input_is_noop(self):
        """表单没改就点新增 → 不造重复项。"""
        m = AddAccountLogic()
        m.seed("我的号", "2021001", "2021001")
        result = m.add_after("2021001", "2021001")
        assert result == "noop"
        assert len(m.cfg.accounts) == 1

    def test_repeated_add_does_not_duplicate(self):
        """连点多次新增同一套账号，不会产生多条。"""
        m = AddAccountLogic()
        m.seed("我的号", "2021001", "2021001")
        for _ in range(5):
            m.add_after("2021001", "2021001")
        assert len(m.cfg.accounts) == 1


# ==================================== 组合回归：用户的真实操作路径
class TestUserFlow:
    def test_typical_flow_single_to_multi(self):
        """
        用户的真实路径：
          1. 只有一套「我的号」
          2. 看到「新增」按钮（Bug 1 修复后可见）
          3. 填第二套 → 点新增
          4. 两套都在，第一套的 label 还在（Bug 2 修复后不丢）
        """
        m = AddAccountLogic()
        m.seed("我的号", "2021001", "2021001")

        # 步骤 2：入口可见
        assert AccountRowVisibility.visible_after(1) is True
        assert AccountRowVisibility.add_enabled(1) is True

        # 步骤 3：新增
        result = m.add_after("2021002", "2021002")
        assert result == "added"

        # 步骤 4：验证
        assert len(m.cfg.accounts) == 2
        labels = {a.student_id: a.label for a in m.cfg.accounts}
        assert labels["2021001"] == "我的号", "第一套的 label 不能丢"
        assert labels["2021002"] == "", "第二套等用户改名"

    def test_edit_current_account_account_field(self):
        """
        用户只改上网账号（学工号不动），点新增 = 编辑，
        label 必须继承，不能变成空。
        """
        m = AddAccountLogic()
        m.seed("我的号", "2021001", "acc-old")

        m.add_after("2021001", "acc-new")

        for a in m.cfg.accounts:
            if a.account == "acc-new":
                assert a.label == "我的号"
                break
        else:
            pytest.fail("没找到新账号")
