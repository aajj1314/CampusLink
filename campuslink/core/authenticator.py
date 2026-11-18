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
from .portal import (PortalClient, extract_message, extract_message_from_html,
                     extract_online_state, friendly_error, make_session)

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

#: 注销成功的页面文案候选（2026-11-18 新增）。
#:
#: ⚠️ 这组是**兼容方案**，不是实测结论 —— 实测成功文案需要一个真实在线的
#: 账号跑一次注销才能拿到，当时条件不具备。用户实测后应把真实文案补进来，
#: 或删掉不相关的项。判据逻辑本身有第二条兜底（在线状态变化），
#: 所以即使文案全不匹配，只要「已联网」变成「未联网」仍会判成功。
LOGOFF_SUCCESS_HINTS = (
    "注销成功",
    "断开成功",
    "已注销",
    "已断开",
    "断开网络成功",
)

#: 注销失败的页面文案
LOGOFF_FAIL_HINTS = (
    "验证码错误",
)


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

    @staticmethod
    def _gated(conf: float, profile: PortalProfile) -> bool:
        """
        该识别结果是否应被置信度门限拦下。

        条件：conf 落在 (0, 1) 开区间内才算「真实置信度」——
        模型引擎给的是连续值；启发式引擎给二值 1.0/0.0，
        1.0 不会被 0.90 的门限拦（0.0 早已被空串分支处理），
        所以二值引擎天然免疫门限，无需特判引擎类型。
        门限值本身做 0~1 截断，防手改档案写坏。
        """
        thr = getattr(profile, "captcha_min_confidence", 0.0)
        try:
            thr = float(thr)
        except (TypeError, ValueError):
            return False
        if not 0.0 < conf < 1.0:
            return False
        return conf < thr

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
                conf = ocr_result.confidence
                last_sig = hashlib.md5(img).hexdigest()
                if on_captcha:
                    try:
                        on_captcha(img, captcha_text)
                    except Exception:
                        pass

                # --- 3.5) 置信度门限（仅 math 模式的模型引擎有意义）---
                # 模型引擎的 confidence 是真实的最小字符置信度；低于门限的结果
                # 按没认出来处理（换图），而不是赌一把提交错答案。
                # 启发式引擎的 confidence 是二值的（1.0/0.0），门限对它无影响。
                if captcha_text and self._gated(conf, profile):
                    captcha_text = ""
                    say(f"验证码把握不足（{conf:.0%} < "
                        f"{profile.captcha_min_confidence:.0%}），换一张重试")

                if ask_captcha is not None and not captcha_text:
                    # 人工兜底：只在这张图「没认出来 / 被门限拦下」时才问。
                    # 识别有把握时不去打扰用户 —— 这是 2.0 的人工兜底语义
                    # （1.x 的「每次都弹」由 GUI 回调自行实现，此处不强制）。
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

                if not captcha_text:
                    say("验证码没认出来，换一张重试")
                    time.sleep(RETRY_DELAY)
                    continue

                if self.verbose:
                    # 算式形态下 captcha_text 是**计算结果**，光看数字没意义，
                    # 把识别到的算式一起打出来才排得动故障
                    expr = ocr_result.detail.get("_expr")
                    if expr:
                        say(f"验证码算式 {expr}=? → 填 {captcha_text}"
                            f"（把握 {conf:.0%}）")
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

    # ============================================================ 注销
    def logout(
        self,
        *,
        cancel: CancelFn | None = None,
        on_progress: ProgressFn | None = None,
        on_captcha: CaptchaFn | None = None,
        ask_captcha: "AskCaptchaFn | None" = None,
        min_confidence: float = 0.90,
        manual_after: int | None = None,
    ) -> LoginResult:
        """
        免验证码注销（2026-11-18 新增）。

        与 login() 的差异：

          · **不需要 Credentials** —— 注销只要验证码，不要账号密码
          · **验证码每次换图**（实测 /CheckCode 30 张 md5 全不同），
            所以没有 login() 那套「换 session 重取」的复杂逻辑
          · **重试次数更多**（logout_max_retry=10）：注销失败成本低
          · **成功后不自动登录** —— 用户要的是腾出会话名额（比如给手机用）

        参数
        ----
        min_confidence : 验证码置信度门限。低于此值**不提交**，直接换图。
                         实测注销验证码的 confidence 在 0.95~0.99，
                         0.90 的门限基本只会拦下真正认错的。
        manual_after   : 第几次尝试后开始允许人工兜底。
                         默认 None = 用 logout_max_retry-1（即第 9 次）。

        判据（三条 OR，见 LOGOFF_SUCCESS_HINTS 与 _judge_logout）
        """
        say = _Say(on_progress)
        aborted = _Cancel(cancel)
        profile = self.profile

        if manual_after is None:
            manual_after = max(1, profile.logout_max_retry - 1)

        # 注销用独立的后端实例：单次识别 + 真实置信度
        backend = LocalDdddocrBackend(mode="logout")

        client = PortalClient(profile)
        last_raw = ""
        last_reason = "未开始"
        before_state = ""

        for attempt in range(1, profile.logout_max_retry + 1):
            if aborted:
                return _cancelled()

            # --- 1) 打开注销页（每次重开：隐藏域都是动态的）---
            say(f"第 {attempt}/{profile.logout_max_retry} 次尝试：打开注销页…")
            try:
                page = client.open_logout_page()
            except PortalChanged as exc:
                return LoginResult(False, exc.user_message, NetworkState.UNKNOWN,
                                   attempts=attempt - 1, detail=exc.detail)
            except NetworkUnreachable as exc:
                return LoginResult(False, exc.user_message, NetworkState.OFFLINE,
                                   attempts=attempt - 1, detail=exc.detail)

            if attempt == 1:
                before_state = page.online_state
                if before_state:
                    say(f"服务端状态：{before_state}"
                        f"{'（IP ' + page.user_ip + '）' if page.user_ip else ''}")
                if before_state == "未联网":
                    # 不是错误，但要提示 —— 用户可能以为注销失败
                    say("提示：账号当前显示未联网，注销可能无实际效果")

            if aborted:
                return _cancelled()

            # --- 2) 取验证码 ---
            try:
                img = client.fetch_logout_captcha(page)
            except NetworkUnreachable as exc:
                say(f"验证码下载失败：{exc.user_message}")
                img = b""

            if not img:
                return LoginResult(
                    False, "注销页没有验证码，页面结构可能已变化",
                    NetworkState.UNKNOWN, attempts=attempt,
                    detail="fetch_logout_captcha 返回空")

            # --- 3) 识别（带真实置信度）---
            ocr_result = backend.recognize(
                img, length=profile.logout_captcha_length,
                strategies=("raw",))

            captcha_text = ocr_result.text
            conf = ocr_result.confidence

            if on_captcha:
                try:
                    on_captcha(img, captcha_text)
                except Exception:
                    pass

            # --- 3.5) 置信度门限：拿不准就换图，不冒险提交 ---
            # 注销是幂等操作，但提交错误验证码会让服务端刷新验证码，
            # 白白多一轮；更糟的是可能触发频率限制。所以低置信度直接换图。
            if captcha_text and conf < min_confidence:
                say(f"验证码把握不足（{conf:.0%} < {min_confidence:.0%}），换一张")
                captcha_text = ""

            if not captcha_text:
                say("验证码没认出来，换一张重试")

            # --- 3.6) 人工兜底：只在快用完重试时才打扰用户 ---
            if not captcha_text and ask_captcha is not None and attempt >= manual_after:
                if aborted:
                    return _cancelled()
                try:
                    manual = ask_captcha(img, captcha_text)
                except Cancelled:
                    return _cancelled()
                except Exception:
                    manual = None
                if manual and manual.strip():
                    captcha_text = manual.strip()
                    say("已采用人工输入的验证码")

            if not captcha_text:
                if attempt < profile.logout_max_retry:
                    time.sleep(RETRY_DELAY)
                continue

            if self.verbose:
                say(f"验证码 {captcha_text}（把握 {conf:.0%}），提交注销…")
            else:
                say("正在提交注销…")

            # --- 4) 提交 ---
            payload = client.build_logout_payload(page, captcha_text)
            try:
                resp = client.submit_logout(page, payload)
            except NetworkUnreachable as exc:
                return LoginResult(False, exc.user_message, NetworkState.OFFLINE,
                                   attempts=attempt, detail=exc.detail)

            # --- 5) 判定 ---
            ok, reason, raw = _judge_logout(resp.text, before_state)
            last_raw = raw
            last_reason = reason

            if ok:
                if reason == "already_offline":
                    msg = "账号本来就是未联网状态（注销无对象，目标状态已达成）"
                elif reason == "state_changed":
                    msg = "已注销（已联网 → 未联网）"
                else:
                    msg = next((h for h in LOGOFF_SUCCESS_HINTS if h in resp.text),
                               "已注销")
                return LoginResult(True, msg, NetworkState.NEED_LOGIN,
                                   attempts=attempt, raw_message=raw)

            if reason == "captcha_error":
                say(f"验证码错误（{attempt}/{profile.logout_max_retry}）")
                if attempt < profile.logout_max_retry:
                    time.sleep(RETRY_DELAY)
                continue

            # 其它不确定情况：记录原文继续重试
            say(f"注销结果不确定（{raw[:40] if raw else '无提示'}），重试")
            if attempt < profile.logout_max_retry:
                time.sleep(RETRY_DELAY)

        return LoginResult(
            False,
            f"注销失败（{last_reason}）",
            NetworkState.UNKNOWN,
            attempts=profile.logout_max_retry,
            raw_message=last_raw)


# ------------------------------------------------------------------ 注销判定
def _judge_logout(html: str, before_state: str) -> tuple[bool, str, str]:
    """
    判定一次注销提交的结果（2026-11-18 新增）。

    返回 (ok, reason, raw_message)

    reason 取值：
        "captcha_error"  —— 验证码错了，应换图重试（**唯一必须重试的失败**）
        "success_hint"   —— 命中成功文案
        "state_changed"  —— 在线状态从「已联网」变成「未联网」
        "already_offline"—— 提交前就已经未联网（注销无对象，但不算失败）
        "unknown"        —— 说不好

    判据优先级（按可靠性排）：

      1. **验证码错误** —— 实测最明确的信号：
         <p class="validation-summary-errors">验证码错误!</p>
         命中即失败，必须换图重来。

      2. **成功文案** —— 从 LOGOFF_SUCCESS_HINTS 里找。
         ⚠️ 这组文案**未经实测确认**（需要真实在线账号跑一次注销），
         属于兼容方案。如果全部不命中，还有第 3、4 条兜底。

      3. **在线状态变化** —— 最可靠的兜底：
         注销页的「当前在线状态」是服务端权威状态，
         提交前「已联网」、提交后「未联网」就是注销成功了。

      4. **本来就离线** —— 提交前就是「未联网」：
         服务端没有会话可断，提交一个格式正确的验证码后
         页面照旧显示「未联网」，没有任何变化可观察。
         这种情况**不应判失败**（用户的目标状态已经达成），
         但也不能说"注销成功"（本来就没登）。
         实测（2026-11-18）：5 次提交全部没有「验证码错误」，
         说明服务端接受了请求，只是没东西可断。
    """
    raw = extract_message_from_html(html) or ""

    # 1) 验证码错误 —— 最明确的失败，必须换图重试
    for hint in LOGOFF_FAIL_HINTS:
        if hint in html or hint in raw:
            return False, "captcha_error", raw

    # 2) 成功文案
    for hint in LOGOFF_SUCCESS_HINTS:
        if hint in html:
            return True, "success_hint", raw or hint

    after_state = extract_online_state(html)

    # 3) 在线状态变化（最可靠）
    if before_state == "已联网" and after_state == "未联网":
        return True, "state_changed", raw or "已联网 → 未联网"

    # 4) 提交前就已经离线 —— 目标状态已达成，但没有"注销"这个动作可做
    if before_state == "未联网" and after_state == "未联网":
        return True, "already_offline", raw or "本来就是未联网状态"

    # 5) 说不好
    return False, "unknown", raw


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
