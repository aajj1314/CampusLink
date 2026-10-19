# -*- coding: utf-8 -*-
"""
认证编排：拿验证码 → 识别 → 提交 → 判定 → 失败换一张重试。

本模块是同步阻塞的（GUI 那边用 QThread 包一层即可），
好处是 core 保持平台无关、也便于命令行和 OpenWrt 复用。

**重试里的去重逻辑是本项目最关键的一段代码**，原因：

    实测该门户的 /CheckCode 不是每次请求都换图 ——
      同一 Session 连续请求        → 返回完全相同的图
      重新 GET /login 之后再取     → 会换
      两次尝试落在同一时间粒度内    → 仍可能撞到同一张
    不做去重的话，「识别错 → 重试 → 拿到同一张错图 → 又识别错」
    会变成一个永不停歇的死循环。上一版就是靠 md5 比对 + 换会话才解决的。
"""
from __future__ import annotations

import hashlib
import time
from typing import Callable

from . import netdetect
from .exceptions import (AlreadyOnline, AuthRejected, Cancelled, CampusLinkError,
                         CaptchaFailed, NetworkUnreachable, PortalChanged)
from .models import Credentials, LoginResult, NetworkState, PortalProfile
from .ocr_backend import LocalDdddocrBackend, OcrBackend
from .portal import PortalClient, friendly_error, make_session

ProgressFn = Callable[[str], None]
CancelFn = Callable[[], bool]
CaptchaFn = Callable[[bytes, str], None]
#: 向用户要验证码答案的钩子：参数是 (验证码图片, 自动识别结果)，
#: 返回用户输入的答案；返回 None / 空串表示「这张不要了，换一张」。
#: 抛 Cancelled 表示整个登录中止。**策略由调用方决定** —— 想每次都问、
#: 还是只在自动识别失败时才问，都在回调内部实现，core 不感知。
AskCaptchaFn = Callable[[bytes, str], "str | None"]

#: 重试之间的间隔。不能太短 —— 该门户的验证码像按时间种子生成，
#: 太快重试可能拿到同一张，等于拿同一个错答案再提交一遍。
RETRY_DELAY = 1.5
#: 发现「服务端没换图」时换会话重取的等待时间
RESHUFFLE_DELAY = 1.3
#: 换会话重取的最大次数
RESHUFFLE_TRIES = 4


class Authenticator:
    """
    一次认证的完整流程。

    一个实例可以重复使用（内部每次登录都会新建 HTTP 会话），
    所以 GUI 里可以长期持有。
    """

    def __init__(self, profile: PortalProfile, verbose: bool = False,
                 ocr_backend: OcrBackend | None = None):
        self.profile = profile
        self.verbose = verbose
        self._ocr_backend = ocr_backend

    def _backend(self) -> OcrBackend:
        """
        取 OCR 后端；未注入时惰性构造本地实现。

        mode 由 profile 声明：字符型门户走 "text"（与旧版逐字段一致），
        算式型门户走 "math"（识别后**算出答案**再提交）。
        """
        if self._ocr_backend is None:
            mode = getattr(self.profile, "captcha_mode", "text")
            self._ocr_backend = LocalDdddocrBackend(mode=mode)
        return self._ocr_backend

    # ------------------------------------------------------------ 对外
    def check_online(self, timeout: float = 5.0) -> bool:
        """轻量检测：现在能不能上外网。"""
        return netdetect.is_online(timeout=timeout)

    def _resolve_portal(self, profile: PortalProfile, say: _Say) -> PortalProfile | None:
        """
        确定门户地址。三条路，按可靠性排序：

          1. 档案里写死的 portal_url（最可靠，本项目的门户就是这种）
          2. 档案里的候选地址列表，逐个探
          3. 现场看 HTTP 请求被劫持到哪里

        顺序很关键：本项目的门户**原地返回登录页、不做 302**，
        所以第 3 条路抓不到东西，只能靠前两条。
        """
        if profile.portal_url and profile.portal_url != "auto":
            return profile

        say("正在查找认证门户…")
        if profile.portal_candidates:
            url = netdetect.probe_candidates(profile.portal_candidates)
            if url:
                say(f"已找到门户：{url}")
                return _with_portal(profile, url)

        rep = netdetect.detect()
        if rep.portal_url:
            say(f"已发现门户：{rep.portal_url}")
            return _with_portal(profile, rep.portal_url)
        return None

    def login(
        self,
        cred: Credentials,
        *,
        cancel: CancelFn | None = None,
        on_progress: ProgressFn | None = None,
        on_captcha: CaptchaFn | None = None,
        ask_captcha: "AskCaptchaFn | None" = None,
    ) -> LoginResult:
        """
        执行认证。任何异常都会被翻译成 LoginResult，不往外抛。

        cancel       : 返回 True 表示用户要求中止
        on_progress  : 收到一句可直接显示的中文状态
        on_captcha   : 每次拿到验证码时回调 (图片字节, 识别结果)，GUI 可以显示出来
        ask_captcha  : 人工输入钩子。识别完成后调用 (图片, 自动识别结果)，
                       返回非空文本则**覆盖**自动识别结果去提交；返回 None/空串
                       则按原逻辑走（识别失败就换图，识别成功就用自动结果）。
                       注意：这是阻塞调用（GUI 侧要弹模态框等用户输入），
                       会占用重试间隔，所以回调内部应自带超时。
        """
        say = _Say(on_progress)
        aborted = _Cancel(cancel)

        c = cred.normalized()
        if not c.is_complete():
            return LoginResult(False, "请先填写学(工)号、上网账号和密码",
                               NetworkState.UNKNOWN)

        profile = self.profile
        if not profile.portal_url or profile.portal_url == "auto":
            profile = self._resolve_portal(profile, say)
            if profile is None:
                return LoginResult(False, "没能找到认证门户地址，请检查门户配置",
                                   NetworkState.NEED_LOGIN)

        if profile.check_online_first:
            say("检查当前网络状态…")
            if aborted:
                return _cancelled()
            if self.check_online():
                return LoginResult(True, "当前已经是联网状态", NetworkState.ONLINE)

        client = PortalClient(profile)
        last_sig: str | None = None
        last_raw = ""

        for attempt in range(1, profile.max_retry + 1):
            if aborted:
                return _cancelled()

            # --- 1) 打开登录页（每次都要重来，隐藏域都是动态的）---
            say(f"第 {attempt}/{profile.max_retry} 次尝试：打开认证页面…")
            try:
                page = client.open_login_page()
            except PortalChanged as exc:
                # 页面明确说「已在线」时，当作成功处理
                if "已在线" in (exc.detail or "") or "已在线" in (exc.user_message or ""):
                    return LoginResult(True, "当前已经是联网状态", NetworkState.ONLINE)
                return LoginResult(False, exc.user_message, NetworkState.NEED_LOGIN,
                                   attempts=attempt - 1, detail=exc.detail)
            except NetworkUnreachable as exc:
                return LoginResult(False, exc.user_message, NetworkState.OFFLINE,
                                   attempts=attempt - 1, detail=exc.detail)

            if aborted:
                return _cancelled()

            # --- 2) 取验证码，并确保这次是「新」的 ---
            img = b""
            captcha_text = ""
            for sub in range(RESHUFFLE_TRIES):
                if aborted:
                    return _cancelled()
                try:
                    img = client.fetch_captcha(page)
                except NetworkUnreachable as exc:
                    img = b""
                    if attempt == 1:
                        say(f"验证码下载失败：{exc.user_message}")
                if not img:
                    break
                sig = hashlib.md5(img).hexdigest()
                if sig != last_sig:
                    break
                # 拿到的是上一次那张，直接重试等于白费 —— 换会话重来
                say("服务端未刷新验证码，正在重新获取…")
                time.sleep(RESHUFFLE_DELAY)
                client = PortalClient(profile, make_session(profile))
                try:
                    page = client.open_login_page()
                except CampusLinkError:
                    break

            # --- 3) 识别 ---
            if img:
                ocr_result = self._backend().recognize(
                    img, length=profile.ocr_length, strategies=profile.ocr_strategies)
                captcha_text = ocr_result.text
                detail = ocr_result.detail
                last_sig = hashlib.md5(img).hexdigest()
                if on_captcha:
                    try:
                        on_captcha(img, captcha_text)
                    except Exception:
                        pass
                if not captcha_text:
                    say("验证码没认出来，换一张重试")
                    time.sleep(RETRY_DELAY)
                    continue

                # --- 3.5) 人工输入兜底：策略由回调决定，core 只负责接线 ---
                if ask_captcha is not None:
                    if aborted:
                        return _cancelled()
                    try:
                        manual = ask_captcha(img, captcha_text)
                    except Cancelled:
                        return _cancelled()
                    except Exception:
                        manual = None          # 钩子出错不能拖垮登录
                    if manual and manual.strip():
                        captcha_text = manual.strip()
                        say("已采用人工输入的验证码答案")
                    elif not captcha_text:
                        # 自动没认出来、用户也没给答案 → 走换图
                        say("验证码没认出来，换一张重试")
                        time.sleep(RETRY_DELAY)
                        continue

                conf = ocr_result.confidence
                if self.verbose:
                    # 算式形态下 captcha_text 是**计算结果**，光看数字没意义，
                    # 把识别到的算式一起打出来才排得动故障
                    expr = ocr_result.detail.get("_expr")
                    if expr:
                        say(f"验证码算式 {expr}=? → 填 {captcha_text}")
                    else:
                        say(f"验证码识别为 {captcha_text}（置信度 {conf:.0%}）")

            if aborted:
                return _cancelled()

            # --- 4) 提交 ---
            say("正在提交认证…")
            payload = client.build_payload(page, c, captcha_text)
            try:
                resp = client.submit(page, payload)
            except NetworkUnreachable as exc:
                return LoginResult(False, exc.user_message, NetworkState.OFFLINE,
                                   attempts=attempt, detail=exc.detail)

            # --- 5) 判定 ---
            ok, user_msg, raw = client.evaluate(resp)
            last_raw = raw
            if ok:
                return LoginResult(True, user_msg or "认证成功", NetworkState.ONLINE,
                                   attempts=attempt, raw_message=raw)

            friendly = friendly_error(raw)
            if attempt < profile.max_retry:
                say(f"{friendly}（{attempt}/{profile.max_retry}）")

            # 密码错误 / 欠费这类问题重试无意义，早点退出别把账号刷成锁定
            if any(k in raw for k in ("密码错", "欠费", "未实名", "未开通", "不存在")):
                return LoginResult(False, friendly, NetworkState.NEED_LOGIN,
                                   attempts=attempt, raw_message=raw)

            time.sleep(RETRY_DELAY)

        return LoginResult(False,
                           friendly_error(last_raw) or "多次尝试后仍未认证成功",
                           NetworkState.NEED_LOGIN,
                           attempts=profile.max_retry, raw_message=last_raw)


# ------------------------------------------------------------------ 小工具
class _Say:
    """把 on_progress 回调包一层，回调本身出错不影响认证。"""

    def __init__(self, fn: ProgressFn | None):
        self._fn = fn

    def __call__(self, msg: str) -> None:
        if self._fn:
            try:
                self._fn(msg)
            except Exception:
                pass


class _Cancel:
    def __init__(self, fn: CancelFn | None):
        self._fn = fn

    def __bool__(self) -> bool:
        if not self._fn:
            return False
        try:
            return bool(self._fn())
        except Exception:
            return False


def _cancelled() -> LoginResult:
    return LoginResult(False, "已取消", NetworkState.UNKNOWN)


def _with_portal(profile: PortalProfile, url: str) -> PortalProfile:
    """复制一份档案，换上新的门户地址（不改动原对象）。"""
    d = profile.to_dict()
    d["portal_url"] = url
    return PortalProfile.from_dict(d)


# ------------------------------------------------------------------ 便捷入口
def quick_login(profile: PortalProfile, cred: Credentials,
                on_progress: ProgressFn | None = None) -> LoginResult:
    """一行登录，给命令行和脚本用。"""
    return Authenticator(profile).login(cred, on_progress=on_progress)
