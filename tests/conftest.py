# -*- coding: utf-8 -*-
"""pytest 公共夹具。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def make_response(html: str, url: str = "http://10.6.6.6/login",
                  status: int = 200) -> requests.Response:
    """把一段 HTML 包装成 requests.Response，用于测试判定逻辑。"""
    r = requests.Response()
    r._content = html.encode("utf-8")
    r.status_code = status
    r.url = url
    r.encoding = "utf-8"
    return r


@pytest.fixture(scope="session")
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def login_page_html(fixtures: Path) -> str:
    """真实的登录页（未提交状态）。"""
    return (fixtures / "portal_180737.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def success_html(fixtures: Path) -> str:
    """真实的认证成功响应。"""
    return (fixtures / "live_login_success.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def captcha_error_html(fixtures: Path) -> str:
    """真实的「验证码错误」响应。"""
    return (fixtures / "live_login_captcha_error.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def rejected_html(fixtures: Path) -> str:
    """真实的「用户未实名注册」响应。"""
    return (fixtures / "live_login_response.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def captcha_png(fixtures: Path) -> bytes:
    return (fixtures / "captcha_sample.png").read_bytes()
