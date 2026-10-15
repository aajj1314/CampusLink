# -*- coding: utf-8 -*-
"""
认证核心的数据模型。

刻意只用标准库（dataclasses / enum / typing），不引入任何 GUI 或平台相关依赖 ——
core 这一层要能在 OpenWrt 之类的环境里原样跑起来。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class NetworkState(str, Enum):
    """当前网络状态。GUI 直接拿它决定显示什么。"""

    UNKNOWN = "unknown"          # 还没检测
    OFFLINE = "offline"          # 没有局域网连接（没连 WiFi / 没插网线）
    NEED_LOGIN = "need_login"    # 已连上局域网，但被强制门户拦着，需要认证
    ONLINE = "online"            # 已经能上外网，不需要认证
    CHECKING = "checking"        # 正在检测


STATE_TEXT = {
    NetworkState.UNKNOWN: "未检测",
    NetworkState.OFFLINE: "未连接网络",
    NetworkState.NEED_LOGIN: "需要认证",
    NetworkState.ONLINE: "已联网",
    NetworkState.CHECKING: "检测中…",
}


class Operator(str, Enum):
    """运营商。这个门户的「运营商」是一个只读文本框，默认「自动选择」。"""

    AUTO = "自动选择"
    TELECOM = "中国电信"
    UNICOM = "中国联通"
    MOBILE = "中国移动"


@dataclass
class Credentials:
    """
    一份认证凭据。

    注意：**密码不落盘**。这个对象只在内存里短暂存在，
    持久化时密码单独走操作系统凭据管理器（见 campuslink.secrets）。
    """

    student_id: str = ""      # 学(工)号
    account: str = ""         # 上网账号（多数学校与学工号相同）
    password: str = ""
    operator: str = Operator.AUTO.value

    def normalized(self) -> "Credentials":
        """上网账号留空时回退到学工号 —— 门户那套系统里两者常常是同一个。"""
        return Credentials(
            student_id=self.student_id.strip(),
            account=(self.account.strip() or self.student_id.strip()),
            password=self.password,
            operator=(self.operator or Operator.AUTO.value),
        )

    def is_complete(self) -> bool:
        c = self.normalized()
        return bool(c.student_id and c.account and c.password)

    def masked(self) -> dict:
        """给日志/调试用的脱敏视图。任何情况下都不要直接打印本对象。"""
        c = self.normalized()
        return {
            "student_id": c.student_id,
            "account": c.account,
            "password": f"<已设置 {len(c.password)} 位>" if c.password else "<空>",
            "operator": c.operator,
        }


@dataclass
class LoginResult:
    """一次认证尝试的结果。message 是要直接显示给用户看的中文。"""

    ok: bool
    message: str
    state: NetworkState = NetworkState.UNKNOWN
    attempts: int = 0
    detail: str = ""          # 技术细节，保证不含敏感信息
    raw_message: str = ""     # 门户原样返回的提示（用于排查）

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class PortalProfile:
    """
    一个「门户档案」——把学校相关的差异全部集中在这里。

    这样做的好处：换学校只需要换一份 profile JSON，代码一行都不用改；
    也给 OpenWrt 移植留了接口（那边只需要实现同名的 ocr/portal 即可）。
    """

    name: str = ""
    portal_url: str = ""                      # 登录页地址；"auto" 表示现场自动发现
    #: 已知的候选门户地址，按顺序试。
    #: 为什么需要它：有些门户（本项目的即是）**原地返回登录页、不做 302 重定向**，
    #: 于是「靠劫持跳转反推门户地址」这条路走不通。那就只能拿已知地址去探。
    portal_candidates: list[str] = field(default_factory=list)
    login_url: str = ""                       # 提交地址；留空则用页面里 form 的 action
    method: str = "POST"
    field_map: dict[str, str] = field(default_factory=dict)
    success_check: dict[str, Any] = field(default_factory=lambda: {
        "type": "contains", "value": "联网成功"})
    captcha_url: str = "auto"                 # auto = 从登录页里现找
    ocr_length: int = 4
    ocr_strategies: tuple[str, ...] = ("raw", "gray")
    max_retry: int = 8
    timeout: int = 10
    verify_ssl: bool = False
    # 该门户是否会在「已在线」时给出登录表单（决定要不要先检测网络）
    check_online_first: bool = True
    # 额外的提交字段（一般留空，隐藏域由页面解析拿到）
    extra_fields: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ocr_strategies"] = list(self.ocr_strategies)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PortalProfile":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in (d or {}).items() if k in known}
        if "ocr_strategies" in clean:
            clean["ocr_strategies"] = tuple(clean["ocr_strategies"])
        return cls(**clean)
