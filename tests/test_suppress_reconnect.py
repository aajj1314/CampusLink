# -*- coding: utf-8 -*-
"""
注销后抑制自动重连的测试（2026-11-18 新增）。

## 为什么单独一个文件

这是实测发现的**功能性 bug**，不是边角情况：

    23:17:05  注销验证码 wmhm
    23:17:06  注销验证码 4Gjl
    23:17:07  检测到掉线，自动重新认证   ← 抵消了用户的注销
    23:17:08  识别 7（算式结果）          ← 又登回去了

用户点「注销」的目的是腾出会话名额（比如给手机用），
1 秒后自动登回去等于整个注销功能失效。

## 测试策略

不给 MainWindow 造完整实例（要 QApplication + 磁盘配置，
在 CI 里跑不动）。这里测的是**状态机契约**：
把抑制逻辑的参数和行为抽出来验证。

真正的 GUI 集成行为由手工测试覆盖（见 docs/design/09）。
"""
from __future__ import annotations

import pytest


class SuppressState:
    """
    抑制状态的极简模型 —— 与 MainWindow 里的逻辑一一对应。

    之所以要建模而不是直接测 MainWindow：MainWindow 的构造需要
    QApplication、Qt 网络栈、磁盘配置目录，在 pytest 里代价太大。
    而这套逻辑本身是纯状态机，单独测更有价值（也更快）。
    """

    def __init__(self):
        self.suppress = False
        self.reason = ""

    # ---- 与 MainWindow 对应的三个动作 ----
    def on_logout_start(self, switching_account: bool = False) -> None:
        """注销一开始就抑制（不等成功，否则来不及）。"""
        self.suppress = True
        self.reason = "切换账号" if switching_account else "用户注销"

    def on_logout_result(self, ok: bool) -> None:
        """成功保持抑制；失败解除（网络可能还连着）。"""
        if not ok:
            self.suppress = False
            self.reason = ""

    def on_manual_connect(self) -> None:
        """用户主动点「立即连接」= 明确要上网。"""
        self.suppress = False
        self.reason = ""

    def on_auth_success(self) -> None:
        """认证成功 = 确定要连网。"""
        self.suppress = False
        self.reason = ""

    # ---- 掉线时的判断 ----
    def should_auto_reconnect(self, auto_connect_enabled: bool = True,
                              creds_complete: bool = True) -> bool:
        """模拟 _on_dropped 的决策。"""
        if self.suppress:
            return False
        if not auto_connect_enabled:
            return False
        if not creds_complete:
            return False
        return True


class TestSuppressLifecycle:
    def test_initial_state_allows_reconnect(self):
        s = SuppressState()
        assert s.should_auto_reconnect() is True

    def test_logout_start_suppresses(self):
        s = SuppressState()
        s.on_logout_start()
        assert s.suppress is True
        assert s.should_auto_reconnect() is False

    def test_logout_start_reason_user(self):
        s = SuppressState()
        s.on_logout_start(switching_account=False)
        assert s.reason == "用户注销"

    def test_logout_start_reason_switch(self):
        s = SuppressState()
        s.on_logout_start(switching_account=True)
        assert s.reason == "切换账号"

    def test_logout_success_keeps_suppressed(self):
        """关键：注销成功后**保持**抑制 —— 这才是用户想要的。"""
        s = SuppressState()
        s.on_logout_start()
        s.on_logout_result(ok=True)
        assert s.suppress is True
        assert s.should_auto_reconnect() is False

    def test_logout_failure_releases(self):
        """注销失败 → 网络可能还连着，恢复掉线保护。"""
        s = SuppressState()
        s.on_logout_start()
        s.on_logout_result(ok=False)
        assert s.suppress is False
        assert s.should_auto_reconnect() is True

    def test_manual_connect_releases(self):
        s = SuppressState()
        s.on_logout_start()
        s.on_manual_connect()
        assert s.suppress is False
        assert s.reason == ""

    def test_auth_success_releases(self):
        s = SuppressState()
        s.on_logout_start()
        s.on_auth_success()
        assert s.suppress is False

    def test_full_cycle_logout_then_manual_connect(self):
        """
        完整一轮：登录成功 → 注销 → 掉线（不重连）→ 手动连接 → 恢复。
        """
        s = SuppressState()

        # 1. 正常在线
        assert s.should_auto_reconnect() is True

        # 2. 用户注销
        s.on_logout_start()
        s.on_logout_result(ok=True)
        assert s.should_auto_reconnect() is False, "注销后不该自动重连"

        # 3. 多轮掉线检测仍然不重连（背景监控每 60 秒跑一次）
        for _ in range(5):
            assert s.should_auto_reconnect() is False

        # 4. 用户想上网了，手动点连接
        s.on_manual_connect()
        assert s.should_auto_reconnect() is True


class TestSuppressInteractionWithOtherGates:
    """抑制标志与其它条件的关系：抑制优先级最高。"""

    def test_suppress_beats_auto_connect_enabled(self):
        """即使勾了「断线自动重新认证」，注销后也不该连。"""
        s = SuppressState()
        s.on_logout_start()
        assert s.should_auto_reconnect(auto_connect_enabled=True) is False

    def test_auto_connect_disabled_alone(self):
        s = SuppressState()
        assert s.should_auto_reconnect(auto_connect_enabled=False) is False

    def test_incomplete_creds_alone(self):
        s = SuppressState()
        assert s.should_auto_reconnect(creds_complete=False) is False

    def test_suppress_with_incomplete_creds(self):
        """三个条件同时成立时也是 False（不能因为别的条件松开）。"""
        s = SuppressState()
        s.on_logout_start()
        assert s.should_auto_reconnect(
            auto_connect_enabled=False, creds_complete=False) is False


class TestRegressionScenarios:
    """
    把实测日志里的时序固化成测试。

    原始日志（2026-11-18 23:17）：
        23:17:05  注销验证码 wmhm
        23:17:06  注销验证码 4Gjl       ← 第一次验证码错，重试
        23:17:07  检测到掉线，自动重新认证
        23:17:08  识别 7（算式结果）

    修复后应该是：
        23:17:05  注销开始（抑制置位）
        ...       验证码重试中（抑制已生效，掉线也不会重连）
        23:17:0X  注销成功（保持抑制）
        ...       needLoginDetected 触发 → 跳过重连
    """

    def test_captcha_retry_then_success_no_reconnect(self):
        s = SuppressState()

        # 注销开始就抑制 —— 这很关键：如果等注销成功才置位，
        # 验证码重试期间（可能十几秒）掉线就会触发重连
        s.on_logout_start()

        # 验证码错了两次（对应 wmhm → 4Gjl）
        assert s.suppress is True, "重试期间必须已经抑制"
        assert s.should_auto_reconnect() is False

        # 最终成功
        s.on_logout_result(ok=True)
        assert s.should_auto_reconnect() is False

    def test_monitor_polls_do_not_reconnect(self):
        """
        背景监控每 60 秒检测一次。注销后连续多次检测都不该重连 ——
        这就是原始 bug 的现场：第一次检测就把它登回去了。
        """
        s = SuppressState()
        s.on_logout_start()
        s.on_logout_result(ok=True)

        for i in range(10):
            assert s.should_auto_reconnect() is False, (
                f"第 {i+1} 次监控检测不该重连")
