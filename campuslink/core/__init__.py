# -*- coding: utf-8 -*-
"""
认证核心 —— 平台无关。

约束（很重要，别破坏）：
    1. 不 import PySide6 或任何 GUI 库
    2. 不 import winreg / pywin32 等 Windows 专有模块
    3. 不直接读写用户数据目录（路径由调用方传入）

依赖只有：requests / beautifulsoup4 / lxml / numpy / opencv / ddddocr ——
这些都跨平台，为将来移植 OpenWrt 留了条件。
"""
from .authenticator import Authenticator, quick_login
from .exceptions import (AlreadyOnline, AuthRejected, Cancelled, CampusLinkError,
                         CaptchaFailed, NetworkUnreachable, PortalChanged)
from .models import (Credentials, LoginResult, NetworkState, Operator,
                     PortalProfile, STATE_TEXT)

__all__ = [
    "Authenticator", "quick_login",
    "Credentials", "LoginResult", "NetworkState", "Operator",
    "PortalProfile", "STATE_TEXT",
    "CampusLinkError", "NetworkUnreachable", "PortalChanged",
    "CaptchaFailed", "AuthRejected", "AlreadyOnline", "Cancelled",
]
