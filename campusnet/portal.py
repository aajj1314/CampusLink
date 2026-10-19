"""校园网门户客户端：登录、状态查询、错误归类。"""

from __future__ import annotations

import html as html_lib
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}
ANTIBOT_MARK = "请勿使用第三方工具"
ERROR_BOX_RE = re.compile(r'class="validation-summary-errors"[^>]*>(.*?)</p>', re.S | re.I)
INPUT_RE = re.compile(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', re.I)
KNOWN_MESSAGES = (
    "验证码错误",
    "验证码无效",
    "用户未实名注册运营商上网账号",
    "账号或密码错误",
    "超过最大并发终端数",
    "请勿使用第三方工具",
)


class PortalError(Exception):
    """门户返回异常。"""


class AntiBotError(PortalError):
    """门户的反自动化拦截。"""


@dataclass
class LoginOutcome:
    message: str
    success_hint: bool
    url: str


class PortalClient:
    def __init__(
        self,
        base_url: str,
        student_id: str,
        username: str,
        password: str,
        timeout: float = 6.0,
        isp: str = "自动选择",
        save_login_info: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.student_id = student_id
        self.username = username
        self.password = password
        self.timeout = timeout
        self.isp = isp
        self.save_login_info = save_login_info

        self.session = requests.Session()
        self.session.trust_env = False  # 校园内网直连，绕开系统代理
        self.session.headers.update(BASE_HEADERS)

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def is_online(self) -> bool | None:
        """查询本机 IP 是否已联网；门户不可达时返回 None。"""
        try:
            response = self.session.get(
                self._url("/logoff"),
                timeout=self.timeout,
                headers={"Referer": self.base_url + "/"},
            )
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        response.encoding = "utf-8"
        return "已联网" in response.text

    def begin_login(self) -> dict[str, str]:
        """打开登录页，取回 ASP.NET 隐藏字段。"""
        response = self.session.get(
            self._url("/login"),
            timeout=self.timeout,
            headers={"Referer": self.base_url + "/"},
        )
        response.encoding = "utf-8"
        if response.status_code != 200:
            raise PortalError(f"登录页返回 HTTP {response.status_code}")

        fields: dict[str, str] = {}
        for match in INPUT_RE.finditer(response.text):
            name = match.group(1)
            if name.startswith("__") or name.startswith("ctl00$"):
                fields[name] = html_lib.unescape(match.group(2))

        if "__VIEWSTATE" not in fields or "__EVENTVALIDATION" not in fields:
            raise PortalError("登录页结构异常：缺少 __VIEWSTATE 或 __EVENTVALIDATION")
        return fields

    def fetch_captcha(self) -> bytes:
        response = self.session.get(
            self._url("/ACaptcha.ashx"),
            params={"t": int(time.time() * 1000)},
            timeout=self.timeout,
            headers={"Referer": self._url("/login")},
        )
        if response.status_code != 200 or not response.content.startswith(b"\x89PNG"):
            raise PortalError("获取验证码失败")
        return response.content

    def submit_login(self, fields: dict[str, str], answer: str) -> LoginOutcome:
        data = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": fields.get("__VIEWSTATE", ""),
            "__VIEWSTATEGENERATOR": fields.get("__VIEWSTATEGENERATOR", ""),
            "__VIEWSTATEENCRYPTED": fields.get("__VIEWSTATEENCRYPTED", ""),
            "__EVENTVALIDATION": fields.get("__EVENTVALIDATION", ""),
            "ctl00$MainContent$TextBoxISP": self.isp,
            "ctl00$MainContent$SSOGUITID": self.student_id,
            "ctl00$MainContent$SSOACC": self.username,
            "ctl00$MainContent$SSOPWD": self.password,
            "ctl00$MainContent$TextBoxCC": answer,
            "ctl00$MainContent$LoginBtn": "登录",
        }
        if self.save_login_info:
            data["ctl00$MainContent$CheckBoxSaveMe"] = "on"

        headers = {
            "Origin": self.base_url,
            "Referer": self._url("/login"),
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        }
        response = self.session.post(
            self._url("/login"),
            data=data,
            headers=headers,
            timeout=self.timeout,
            allow_redirects=True,
        )
        response.encoding = "utf-8"
        text = response.text

        if ANTIBOT_MARK in text:
            raise AntiBotError("门户拒绝了本次请求（第三方工具检测）")
        if response.status_code >= 500:
            raise PortalError(f"登录接口返回 HTTP {response.status_code}")

        message = self._extract_message(text)
        success_hint = "登录成功" in text or "认证成功" in text
        if not success_hint:
            path = urlparse(response.url).path.lower()
            # 失败时停留在 /login，被拦截时跳到 /error，其余跳转视为成功
            if path not in ("/login", "/error"):
                success_hint = True

        return LoginOutcome(message=message, success_hint=success_hint, url=response.url)

    def verify_online(self, seconds: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            online = self.is_online()
            if online:
                return True
            time.sleep(0.4)
        return False

    @staticmethod
    def _extract_message(html: str) -> str:
        match = ERROR_BOX_RE.search(html)
        if match:
            message = re.sub(r"<[^>]+>", " ", match.group(1))
            return " ".join(html_lib.unescape(message).split())
        for known in KNOWN_MESSAGES:
            if known in html:
                return known
        return ""
