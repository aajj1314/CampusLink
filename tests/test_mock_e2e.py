# -*- coding: utf-8 -*-
"""
端到端测试：起一个模拟门户，用真实的 Authenticator 走完整认证流程。

这个测试针对的是重构成 CampusLink 之后最容易出问题的地方 ——
认证循环里那套「重试 + 验证码去重」的逻辑。它复刻了真实门户的三个行为：

  1. **同一个 Session 反复请求验证码，返回的是同一张图**（真实行为，
     实测该门户就是这样）。客户端必须发现这一点并换会话，
     否则会「拿同一张错图反复提交」变成死循环。
  2. 隐藏域里塞一次性令牌，每次加载登录页都变 —— 逼客户端每次重新解析表单。
  3. 提交按钮的 name 必须带上，否则不认（ASP.NET 的行为）。

不用真实门户跑：那需要用户密码，而且会把账号刷出并发限制。
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from campuslink.core.authenticator import Authenticator
from campuslink.core.models import Credentials, NetworkState, PortalProfile

FIXTURES = Path(__file__).resolve().parent / "fixtures"

EXPECT_USER = "20240001"
EXPECT_PWD = "test-password"


# ------------------------------------------------------------------ 工具
def _variant(img: bytes) -> bytes:
    """把验证码图轻微平移，得到一张「内容相同但字节不同」的图。

    用来模拟「换了会话后服务端给的新验证码」，同时保持 OCR 结果稳定。
    """
    import cv2
    import numpy as np

    arr = cv2.imdecode(np.frombuffer(img, np.uint8), cv2.IMREAD_UNCHANGED)
    if arr is None:
        return img
    shifted = np.roll(arr, 3, axis=1)
    if shifted.ndim == 3:
        shifted[:, :3, :] = 255
    ok, buf = cv2.imencode(".png", shifted)
    return buf.tobytes() if ok else img


def _ocr(img: bytes) -> str:
    from campuslink.core import ocr
    text, _ = ocr.recognize(img, strategies=("raw", "gray"), length=4)
    return text


# ------------------------------------------------------------------ 模拟门户
class _State:
    """跨请求共享的状态。"""

    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.images: list[bytes] = []
        self.expected: dict[str, str] = {}     # 图片 md5 -> 正确的验证码文本
        self.fail_first = 0                    # 前 N 次提交强制失败（模拟验证码错）
        self.posts = 0
        self.captcha_hits = 0


def _make_handler(state: _State):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass                                # 静音

        # -------------------------------------------------- 辅助
        def _send(self, code, body: bytes, ctype="text/html; charset=utf-8",
                  extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _sid(self) -> str:
            for part in self.headers.get("Cookie", "").split(";"):
                if part.strip().startswith("JSESSIONID="):
                    sid = part.strip().split("=", 1)[1]
                    if sid in state.sessions:
                        return sid
            return ""

        # -------------------------------------------------- GET
        def do_GET(self):
            path = urlparse(self.path).path

            if path in ("/", "/login"):
                import uuid
                sid = uuid.uuid4().hex[:16]
                # 每个新会话分配下一张验证码图（看图顺序循环取）
                idx = len(state.sessions) % len(state.images)
                state.sessions[sid] = {
                    "token": uuid.uuid4().hex,
                    "img_idx": idx,
                }
                html = self._login_html(state.sessions[sid]["token"]).encode()
                self._send(200, html, extra={"Set-Cookie": f"JSESSIONID={sid}; Path=/"})
                return

            if path == "/captcha.png":
                sid = self._sid()
                if not sid:
                    self._send(403, b"no session")
                    return
                state.captcha_hits += 1
                # 关键：**同一个会话永远返回同一张图**（复刻真实门户行为）
                img = state.images[state.sessions[sid]["img_idx"]]
                self._send(200, img, "image/png")
                return

            self._send(404, b"not found")

        # -------------------------------------------------- POST
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8", "replace")
            form = {k: v[0] for k, v in parse_qs(raw).items()}
            state.posts += 1

            sid = self._sid()
            if not sid:
                self._send(200, self._page("登录失败", "会话已过期，请刷新页面"))
                return

            sess = state.sessions[sid]

            # 一次性令牌：证明客户端每次重新解析了表单
            if form.get("t") != sess["token"]:
                self._send(200, self._page("登录失败", "页面令牌已失效，请重新登录"))
                return

            # 提交按钮必须带上（ASP.NET 行为）
            if "ctl00$MainContent$LoginBtn" not in form:
                self._send(200, self._page("登录失败", "缺少提交按钮参数"))
                return

            # 验证码：跟服务端为该会话准备的那张图核对
            want = state.expected[hashlib.md5(
                state.images[sess["img_idx"]]).hexdigest()]
            got = (form.get("ctl00$MainContent$TextBoxCC") or "").lower()
            if got != want.lower():
                self._send(200, self._page("登录失败", "验证码错误!"))
                return

            # 强制失败若干次（用来逼出重试与去重逻辑）
            if state.fail_first > 0:
                state.fail_first -= 1
                self._send(200, self._page("登录失败", "验证码错误!"))
                return

            if form.get("ctl00$MainContent$SSOACC") != EXPECT_USER:
                self._send(200, self._page("登录失败", "用户未实名注册运营商上网账号!"))
                return
            if form.get("ctl00$MainContent$SSOPWD") != EXPECT_PWD:
                self._send(200, self._page("登录失败", "认证请求被拒绝，密码错误"))
                return

            self._send(200, self._page("登录成功", "联网成功!"))

        # -------------------------------------------------- 页面
        @staticmethod
        def _login_html(token: str) -> str:
            return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>登录 - 多运营商接入管理平台统一认证系统</title></head><body>
<form method="post" action="./login" id="ctl01">
<input type="hidden" name="__VIEWSTATE" value="FakeViewState==">
<input type="hidden" name="__EVENTVALIDATION" value="FakeEventValidation==">
<input type="hidden" name="t" value="{token}">
<table>
<tr><td>运营商:</td><td><input name="ctl00$MainContent$TextBoxISP" value="自动选择" readonly></td></tr>
<tr><td>学(工)号:</td><td><input name="ctl00$MainContent$SSOGUITID" maxlength="12"></td></tr>
<tr><td>上网账号:</td><td><input name="ctl00$MainContent$SSOACC" maxlength="12"></td></tr>
<tr><td>密码:</td><td><input type="password" name="ctl00$MainContent$SSOPWD" maxlength="16"></td></tr>
<tr><td>验证码:</td><td><img id="ImageCC" src="/captcha.png" onclick="this.src='/captcha.png?t='+Date.now()"></td></tr>
<tr><td></td><td><input name="ctl00$MainContent$TextBoxCC" maxlength="4"></td></tr>
<tr><td colspan="2"><input type="submit" name="ctl00$MainContent$LoginBtn" value="登录"></td></tr>
</table>
<p class="validation-summary-errors"></p>
</form></body></html>"""

        @staticmethod
        def _page(title: str, msg: str) -> bytes:
            # 复刻真实门户：成功与失败共用同一个提示容器，
            # 而且成功时登录表单照旧渲染、不跳转
            return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<form method="post" action="./login">
<input type="hidden" name="__VIEWSTATE" value="x">
<input name="ctl00$MainContent$SSOGUITID">
<input type="password" name="ctl00$MainContent$SSOPWD">
<input name="ctl00$MainContent$TextBoxCC">
</form>
<p class="validation-summary-errors">{msg}</p>
</body></html>""".encode()

    return Handler


# ------------------------------------------------------------------ 夹具
@pytest.fixture(scope="module")
def mock_portal():
    """起一个模拟门户，返回 (base_url, state, stop_fn)。"""
    base_img = (FIXTURES / "captcha_sample.png").read_bytes()
    img2 = _variant(base_img)

    state = _State()
    state.images = [base_img, img2]
    for im in state.images:
        state.expected[hashlib.md5(im).hexdigest()] = _ocr(im)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.2)

    yield f"http://127.0.0.1:{port}/login", state

    srv.shutdown()
    srv.server_close()


def _profile(url: str) -> PortalProfile:
    return PortalProfile(
        name="mock",
        portal_url=url,
        field_map={
            "operator": "ctl00$MainContent$TextBoxISP",
            "student_id": "ctl00$MainContent$SSOGUITID",
            "account": "ctl00$MainContent$SSOACC",
            "password": "ctl00$MainContent$SSOPWD",
            "captcha": "ctl00$MainContent$TextBoxCC",
        },
        success_check={"type": "contains", "value": "联网成功"},
        captcha_url="auto",
        ocr_length=4,
        ocr_strategies=("raw", "gray"),
        max_retry=5,
        check_online_first=False,     # 本地 mock 上跑，跳过联网判断
    )


def _creds(pwd: str = EXPECT_PWD) -> Credentials:
    return Credentials(EXPECT_USER, EXPECT_USER, pwd)


# =================================================================== 用例
class TestMockE2E:
    def test_successful_login(self, mock_portal):
        """正常路径：一次就成。"""
        url, state = mock_portal
        state.fail_first = 0
        progress: list[str] = []

        res = Authenticator(_profile(url)).login(_creds(), on_progress=progress.append)

        assert res.ok, f"应当成功，实际: {res.message} / {res.raw_message}"
        assert res.state is NetworkState.ONLINE
        assert res.attempts == 1
        assert any("提交" in m or "认证" in m for m in progress), "应当有进度回调"

    def test_wrong_password_is_reported_clearly(self, mock_portal):
        """密码错：给出人话，并且**不反复重试**把账号刷成锁定。"""
        url, state = mock_portal
        state.fail_first = 0

        res = Authenticator(_profile(url)).login(_creds(pwd="wrong-password"))

        assert not res.ok
        assert "密码" in res.message
        assert res.attempts == 1, "密码错误不该重试"

    def test_captcha_dedup_forces_new_session(self, mock_portal):
        """
        核心用例：服务端对同一会话始终返回同一张验证码图时，
        认证器必须发现并换会话重取，否则会拿同一张错图死循环。

        做法：让前 1 次提交强制失败，这样必然触发第二次尝试。
             第二次如果还复用同一会话，就会拿到同一张图（md5 相同），
             此时去重逻辑应当换新会话 —— 会话数因此增加。
        """
        url, state = mock_portal
        state.fail_first = 1
        sessions_before = len(state.sessions)

        res = Authenticator(_profile(url)).login(_creds())

        assert res.ok, f"换会话后应当成功: {res.message} / {res.raw_message}"
        assert res.attempts == 2, f"应当是第 2 次成功，实际 {res.attempts}"
        assert len(state.sessions) > sessions_before + 1, \
            "应当因为验证码重复而创建过额外会话（说明去重逻辑生效了）"

    def test_gives_up_after_max_retry(self, mock_portal):
        """一直失败时要停下来，而不是无限重试。"""
        url, state = mock_portal
        state.fail_first = 99

        res = Authenticator(_profile(url)).login(_creds())

        assert not res.ok
        assert res.attempts <= 5
        state.fail_first = 0

    def test_cancel_stops_quickly(self, mock_portal):
        """取消要能在一次尝试内生效。"""
        url, state = mock_portal
        state.fail_first = 99
        calls = {"n": 0}

        def cancel() -> bool:
            calls["n"] += 1
            return calls["n"] > 4          # 跑几步之后请求取消

        res = Authenticator(_profile(url)).login(_creds(), cancel=cancel)

        assert not res.ok
        assert "取消" in res.message
        state.fail_first = 0

    def test_token_changes_are_followed(self, mock_portal):
        """
        隐藏域里的一次性令牌每次加载页面都变。
        如果客户端偷懒复用上一次的 payload，服务端会回「页面令牌已失效」。
        """
        url, state = mock_portal
        state.fail_first = 0
        res = Authenticator(_profile(url)).login(_creds())
        assert res.ok
        assert "令牌" not in (res.raw_message or "")
