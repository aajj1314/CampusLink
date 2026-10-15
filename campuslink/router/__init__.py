# -*- coding: utf-8 -*-
"""
CampusLink Router —— 控制面（OpenWrt 可移植）。

包内容（任务书 Agent 1 交付）：
    apiserver.py     /api/v1 服务端（标准库 http.server）
    taskmanager.py   视觉任务状态机
    pairing.py       设备配对 / token 签发（仅存 SHA-256）
    remotecomplete.py RemoteVisionBackend（实现 OcrBackend 协议）
    auth.py          认证编排（把 core.Authenticator 接到远程 OCR）
    images.py        纯标准库图像校验
    _vendor/         纯 Python 依赖（requests/urllib3/bs4）vendoring

本包在 OpenWrt 上运行时，必须先 `_vendor.install()` 注入 vendored 依赖；
PC（router_sim）上依赖已随 Windows 安装，install() 为空操作。
"""
from __future__ import annotations

# 关键：必须在导入 apiserver/auth 之前注入 vendored 依赖。
# 这些模块会在顶层链式 import requests/urllib3/bs4（apiserver → auth →
# remotecomplete → campuslink.core.ocr_backend → core.portal），而 OpenWrt 上
# 只有 vendor 版、不在 sys.path。PC 上 CAMPUSLINK_VENDOR 未设，install() 是无害空操作。
from ._vendor import install as _install_vendor

_install_vendor()

from .apiserver import ApiServer, RateLimiter, RouterContext
from .auth import AuthController, AuthTask
from .pairing import DeviceIdentity, PairingError, PairingManager
from .remotecomplete import OcrResult, RemoteVisionBackend
from .taskmanager import (TaskManager, TaskState, VisionTask, TaskResult,
                          ImageMeta, TaskError)

__all__ = [
    "ApiServer", "RouterContext", "RateLimiter",
    "AuthController", "AuthTask",
    "PairingManager", "PairingError", "DeviceIdentity",
    "RemoteVisionBackend", "OcrResult",
    "TaskManager", "TaskState", "VisionTask", "TaskResult",
    "ImageMeta", "TaskError",
]
