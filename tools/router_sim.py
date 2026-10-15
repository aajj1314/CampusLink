#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PC 模拟器 —— 在 PC 上跑起与 OpenWrt 完全相同的一套 `/api/v1` 服务端，
并附一个模拟门户 + 一个可选的内置演示视觉节点。

这是 Agent 2 / Agent 3 的联调依赖（任务书 §C3 T1 交付物），也用于
「配对 → 创建认证任务 → 模拟门户认证成功」全链路自验（验收标准）。

用法：
    python tools/router_sim.py                      # API 在 7700，模拟门户随机端口
    python tools/router_sim.py --port 7701 --demo-node
    python -m campuslink.cli router-serve --sim     # 等价入口

可选 `--demo-node` 会起一个本地视觉节点（走同一套 API：配对 → 长轮询领任务 →
本地 ddddocr 识别 → 提交），让你无需真机即可看到完整链路。

触发一次认证：
    curl -X POST -H "Authorization: Bearer <token>" \
         http://127.0.0.1:7700/api/v1/auth/login -d '{"wait": false}'
"""
from __future__ import annotations

import argparse
import hashlib
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# ------------------------------------------------------------------ 模拟门户
class _MockPortalState:
    def __init__(self, images: list[bytes], expected: dict[str, str],
                 user: str, pwd: str):
        self.sessions: dict[str, dict] = {}
        self.images = images
        self.expected = expected
        self.user = user
        self.pwd = pwd
        self.fail_first = 0
        self.posts = 0


def _mock_handler(state: "_MockPortalState"):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, ctype="text/html; charset=utf-8",
                  extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _sid(self):
            for part in self.headers.get("Cookie", "").split(";"):
                if part.strip().startswith("JSESSIONID="):
                    sid = part.strip().split("=", 1)[1]
                    if sid in state.sessions:
                        return sid
            return ""

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/login"):
                sid = uuid.uuid4().hex[:16]
                idx = len(state.sessions) % len(state.images)
                state.sessions[sid] = {"token": uuid.uuid4().hex, "img_idx": idx}
                html = _login_html(state.sessions[sid]["token"]).encode()
                self._send(200, html, extra={"Set-Cookie": f"JSESSIONID={sid}; Path=/"})
                return
            if path == "/captcha.png":
                sid = self._sid()
                if not sid:
                    self._send(403, b"no session")
                    return
                img = state.images[state.sessions[sid]["img_idx"]]
                self._send(200, img, "image/png")
                return
            self._send(404, b"not found")

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            form = {k: v[0] for k, v in parse_qs(
                self.rfile.read(n).decode("utf-8", "replace")).items()}
            state.posts += 1
            sid = self._sid()
            if not sid:
                self._send(200, _page("登录失败", "会话已过期，请刷新页面"))
                return
            sess = state.sessions[sid]
            if form.get("t") != sess["token"]:
                self._send(200, _page("登录失败", "页面令牌已失效，请重新登录"))
                return
            if "ctl00$MainContent$LoginBtn" not in form:
                self._send(200, _page("登录失败", "缺少提交按钮参数"))
                return
            want = state.expected[hashlib.md5(
                state.images[sess["img_idx"]]).hexdigest()]
            if (form.get("ctl00$MainContent$TextBoxCC") or "").lower() != want.lower():
                self._send(200, _page("登录失败", "验证码错误!"))
                return
            if state.fail_first > 0:
                state.fail_first -= 1
                self._send(200, _page("登录失败", "验证码错误!"))
                return
            if form.get("ctl00$MainContent$SSOACC") != state.user:
                self._send(200, _page("登录失败", "用户未实名注册运营商上网账号!"))
                return
            if form.get("ctl00$MainContent$SSOPWD") != state.pwd:
                self._send(200, _page("登录失败", "认证请求被拒绝，密码错误"))
                return
            self._send(200, _page("登录成功", "联网成功!"))

    return Handler


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


def _page(title: str, msg: str) -> bytes:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<form method="post" action="./login">
<input type="hidden" name="__VIEWSTATE" value="x">
<input name="ctl00$MainContent$SSOGUITID">
<input type="password" name="ctl00$MainContent$SSOPWD">
<input name="ctl00$MainContent$TextBoxCC">
</form>
<p class="validation-summary-errors">{msg}</p>
</body></html>""".encode()


def _variant(img: bytes) -> bytes:
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


# ------------------------------------------------------------------ 演示视觉节点
def _demo_node(base: str) -> None:
    """一个走真实 API 的本地视觉节点：配对 → 长轮询 → 本地识别 → 提交。"""
    import requests
    from campuslink.core import ocr

    r = requests.post(base + "/api/v1/pair/start", timeout=5).json()
    r = requests.post(base + "/api/v1/pair/confirm", json={
        "pin": r["pin"], "device_name": "演示节点(本地)", "device_kind": "desktop",
        "capabilities": ["ocr", "manual"],
    }, timeout=5).json()
    token = r["device_token"]
    h = {"Authorization": f"Bearer {token}"}
    print(f"  [demo-node] 已配对 device_id={r['device_id']}")

    while True:
        try:
            r = requests.get(base + "/api/v1/vision/tasks/next?wait=25",
                             headers=h, timeout=60)
        except requests.RequestException:
            time.sleep(1)
            continue
        if r.status_code != 200:
            continue
        t = r.json()
        try:
            img = requests.get(base + t["image_url"], headers=h, timeout=10).content
            text, detail = ocr.recognize(img, strategies=("raw", "gray"),
                                         length=t["length"])
        except Exception:
            text, detail = "", {}
        if not text:
            # 识别失败：主动放弃，任务回 pending 让别的节点（或人工）接手
            try:
                requests.delete(base + f"/api/v1/vision/tasks/{t['task_id']}",
                                headers=h, timeout=5)
            except requests.RequestException:
                pass
            continue
        try:
            requests.post(base + f"/api/v1/vision/tasks/{t['task_id']}/result",
                          headers=h, json={"nonce": t["nonce"], "text": text,
                                           "source": "ocr", "confidence": 1.0,
                                           "detail": detail, "elapsed_ms": 0},
                          timeout=5)
        except requests.RequestException:
            pass


# ------------------------------------------------------------------ 主入口
def main(argv: list[str] | None = None) -> int:
    import os
    from pathlib import Path

    from campuslink.core import ocr
    from campuslink.core.models import Credentials, PortalProfile
    from campuslink.router import (ApiServer, AuthController, PairingManager,
                                   RouterContext, TaskManager)

    ap = argparse.ArgumentParser(prog="router_sim",
                                 description="CampusLink Router PC 模拟器")
    ap.add_argument("--port", type=int, default=7700, help="API 监听端口")
    ap.add_argument("--mock-port", type=int, default=0, help="模拟门户端口（0=自动）")
    ap.add_argument("--demo-node", action="store_true", help="起一个本地演示视觉节点")
    ap.add_argument("--user", default="20240001", help="模拟门户账号")
    ap.add_argument("--pwd", default="test-password", help="模拟门户密码")
    args = ap.parse_args(argv)

    # 验证码样本（复刻 tests/test_mock_e2e 的思路）
    root = Path(__file__).resolve().parent.parent
    base_img = (root / "tests" / "fixtures" / "captcha_sample.png").read_bytes()
    images = [base_img, _variant(base_img)]
    expected = {hashlib.md5(im).hexdigest(): ocr.recognize(
        im, strategies=("raw", "gray"), length=4)[0] for im in images}

    # 1) 模拟门户
    state = _MockPortalState(images, expected, args.user, args.pwd)
    portal = ThreadingHTTPServer(("127.0.0.1", args.mock_port), _mock_handler(state))
    portal.daemon_threads = True
    threading.Thread(target=portal.serve_forever, daemon=True).start()
    portal_url = f"http://127.0.0.1:{portal.server_address[1]}/login"

    # 2) 档案 + 凭据（密码仅内存，不落盘）
    profile = PortalProfile(
        name="mock", portal_url=portal_url,
        field_map={
            "operator": "ctl00$MainContent$TextBoxISP",
            "student_id": "ctl00$MainContent$SSOGUITID",
            "account": "ctl00$MainContent$SSOACC",
            "password": "ctl00$MainContent$SSOPWD",
            "captcha": "ctl00$MainContent$TextBoxCC",
        },
        success_check={"type": "contains", "value": "联网成功"},
        captcha_url="auto", ocr_length=4, ocr_strategies=("raw", "gray"),
        max_retry=5, check_online_first=False,
    )
    cred = Credentials(args.user, args.user, args.pwd)

    # 3) 控制面
    tm = TaskManager()
    ctx = RouterContext(
        taskmanager=tm, pairing=PairingManager(),
        auth=AuthController(tm, profile=profile, credentials=cred),
        portal_name="模拟门户", router_version="0.1.0-sim",
    )
    srv = ApiServer(ctx, host="127.0.0.1", port=args.port)
    s = srv.start()
    port = s.server_address[1]
    base = f"http://127.0.0.1:{port}"

    print("=" * 60)
    print(" CampusLink Router 模拟器已启动")
    print("=" * 60)
    print(f"  控制面 API : {base}/api/v1")
    print(f"  模拟门户   : {portal_url}")
    print(f"  健康检查   : curl {base}/api/v1/health")
    print()
    print("  配对步骤（让 Desktop / Mobile 节点接入）：")
    print("    1) POST /api/v1/pair/start  取 PIN")
    print("    2) POST /api/v1/pair/confirm 换 device_token")
    print("    3) 用 Bearer token 长轮询 /vision/tasks/next")
    print()
    print("  触发一次认证：")
    print(f"    curl -X POST -H 'Authorization: Bearer <token>' \\")
    print(f"      {base}/api/v1/auth/login -d '{{\"wait\": false}}'")
    print()

    if args.demo_node:
        threading.Thread(target=_demo_node, args=(base,), daemon=True).start()
        print("  [demo-node] 本地演示视觉节点已启动（自动领取并识别任务）")
        print()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        srv.shutdown()
        portal.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
