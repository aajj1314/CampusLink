"""后台监测：周期性检查在线状态，掉线后自动认证。"""

from __future__ import annotations

import logging
import time
import webbrowser
from typing import Any

import requests

from .captcha import CaptchaSolver
from .portal import AntiBotError, LoginOutcome, PortalClient, PortalError


class Monitor:
    def __init__(
        self,
        config: dict[str, Any],
        portal: PortalClient,
        solver: CaptchaSolver,
        logger: logging.Logger | None = None,
    ):
        self.config = config
        self.portal = portal
        self.solver = solver
        self.log = logger or logging.getLogger("campusnet")

        monitor = config["monitor"]
        self.interval = max(5.0, float(monitor["check_interval_seconds"]))
        self.max_retries = max(1, int(monitor["max_captcha_retries"]))
        self.retry_delay = max(0.0, float(monitor["retry_delay_seconds"]))
        self.verify_seconds = max(0.0, float(monitor["login_verify_seconds"]))
        self.network_cooldown = max(5.0, float(monitor["network_cooldown_seconds"]))
        self.account_cooldown = max(30.0, float(monitor["account_cooldown_seconds"]))
        self.antibot_cooldown = max(10.0, float(monitor["antibot_cooldown_seconds"]))
        self.captcha_cooldown = max(30.0, float(monitor["captcha_fail_cooldown_seconds"]))
        self.open_browser_on_failure = bool(monitor["open_browser_on_failure"])
        self.browser_cooldown = max(60.0, float(monitor["browser_cooldown_seconds"]))

        self._online: bool | None = None
        self._next_attempt_at = 0.0
        self._last_browser_open = 0.0
        self._last_unreachable_log = 0.0

    def run_forever(self) -> None:
        self.log.info("后台认证服务已启动：%s（检测间隔 %.0f 秒）", self.portal.base_url, self.interval)
        last_cycle = time.monotonic()
        try:
            while True:
                cycle_start = time.monotonic()
                try:
                    self.tick()
                except Exception:
                    self.log.exception("主循环异常")
                if cycle_start - last_cycle > self.interval * 3 + 30:
                    self.log.warning("检测到休眠或长时间阻塞，立即重新检查")
                last_cycle = cycle_start
                time.sleep(self.interval)
        except KeyboardInterrupt:
            self.log.info("收到退出信号，服务停止")

    def tick(self) -> None:
        online = self.portal.is_online()
        if online is None:
            now = time.monotonic()
            if now - self._last_unreachable_log > 300:
                self.log.warning("无法访问认证门户 %s（可能不在校园网内）", self.portal.base_url)
                self._last_unreachable_log = now
            return

        if online:
            if self._online is False:
                self.log.info("网络已恢复在线")
            elif self._online is None:
                self.log.info("当前网络在线，无需认证")
            self._online = True
            self._next_attempt_at = 0.0
            return

        if self._online is not False:
            self.log.warning("检测到未联网，准备自动认证")
        self._online = False

        if time.monotonic() < self._next_attempt_at:
            return

        self._authenticate()

    def _authenticate(self) -> None:
        for attempt in range(1, self.max_retries + 1):
            if self.portal.is_online():
                self.log.info("认证前检测到已在线")
                self._online = True
                return

            try:
                fields = self.portal.begin_login()
                png = self.portal.fetch_captcha()
            except requests.RequestException as exc:
                self.log.warning("访问门户失败：%s", exc)
                self._schedule(self.network_cooldown)
                return
            except PortalError as exc:
                self.log.error("门户页面异常：%s", exc)
                self._schedule(self.network_cooldown)
                return

            try:
                expression, confidence, answer = self.solver.answer(png)
            except Exception:
                self.log.exception("验证码识别异常")
                continue
            if answer is None:
                self.log.info(
                    "验证码 %r 置信度 %.3f，不可靠或无法求值，换一张",
                    expression,
                    confidence,
                )
                continue

            try:
                outcome = self.portal.submit_login(fields, answer)
            except AntiBotError as exc:
                self.log.error("%s，暂停 %.0f 秒", exc, self.antibot_cooldown)
                self._schedule(self.antibot_cooldown)
                return
            except requests.RequestException as exc:
                self.log.warning("提交登录失败：%s", exc)
                self._schedule(self.network_cooldown)
                return
            except PortalError as exc:
                self.log.error("登录接口异常：%s", exc)
                self._schedule(self.network_cooldown)
                return

            if outcome.success_hint or self.portal.verify_online(self.verify_seconds):
                self._report_success(attempt, expression, outcome)
                return

            if "验证码" in outcome.message:
                self.log.info("验证码未通过：%s（第 %d 次）", outcome.message, attempt)
                time.sleep(self.retry_delay)
                continue

            if outcome.message:
                self.log.error("认证被拒绝：%s", outcome.message)
                self._schedule(self.account_cooldown)
                return

            self.log.warning("提交后未确认在线，稍后重试（第 %d 次）", attempt)
            time.sleep(self.retry_delay)

        self.log.error("连续 %d 次未通过验证码校验，暂停 %.0f 秒", self.max_retries, self.captcha_cooldown)
        self._schedule(self.captcha_cooldown)
        self._maybe_open_browser()

    def _report_success(self, attempt: int, expression: str, outcome: LoginOutcome) -> None:
        if outcome.success_hint:
            self.log.info("认证成功（第 %d 次尝试，算式 %s，门户提示已登录）", attempt, expression)
        else:
            self.log.info("认证成功（第 %d 次尝试，算式 %s）", attempt, expression)
        self._online = True
        self._next_attempt_at = 0.0
        self._last_browser_open = 0.0

    def _schedule(self, seconds: float) -> None:
        self._next_attempt_at = time.monotonic() + seconds

    def _maybe_open_browser(self) -> None:
        if not self.open_browser_on_failure:
            return
        now = time.monotonic()
        if now - self._last_browser_open < self.browser_cooldown:
            return
        self._last_browser_open = now
        try:
            webbrowser.open(self.portal.base_url + "/login")
            self.log.warning("已打开浏览器，请在页面中手动完成认证")
        except Exception:
            self.log.exception("打开浏览器失败")
