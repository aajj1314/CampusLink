# -*- coding: utf-8 -*-
"""
认证核心的异常体系。

统一成这几种，是为了让 GUI 只根据异常类型决定提示文案，
而不用去解析底层各种库的报错。

所有异常的 message 都必须是可以直接显示给普通用户的中文，
技术细节放在 detail 里（同样不允许包含密码等敏感信息）。
"""
from __future__ import annotations


class CampusLinkError(Exception):
    """所有认证相关异常的基类。"""

    #: 给用户看的默认文案
    user_message = "发生未知错误"

    def __init__(self, detail: str = "", user_message: str | None = None):
        super().__init__(detail or self.user_message)
        self.detail = detail
        if user_message:
            self.user_message = user_message

    def __str__(self) -> str:
        return self.user_message


class NetworkUnreachable(CampusLinkError):
    """连不上门户 —— 多半是不在校园网内，或者地址换了。"""

    user_message = "连不上认证服务器，请确认已连接校园网 WiFi 或网线"


class PortalChanged(CampusLinkError):
    """门户页面结构变了 —— 登录表单解析不出来。"""

    user_message = "认证页面结构已变化，可能需要更新门户配置"


class CaptchaFailed(CampusLinkError):
    """验证码连续识别失败。"""

    user_message = "验证码识别失败，请稍后重试"


class AuthRejected(CampusLinkError):
    """服务端明确拒绝了 —— 账号密码错、欠费、并发超限等。"""

    user_message = "认证失败"


class AlreadyOnline(CampusLinkError):
    """其实已经在线了，不需要认证。"""

    user_message = "当前已经是联网状态"


class Cancelled(CampusLinkError):
    """用户主动取消。"""

    user_message = "已取消"
