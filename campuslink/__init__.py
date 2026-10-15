# -*- coding: utf-8 -*-
"""
CampusLink —— 校园网认证客户端。

包结构：
    campuslink.core        认证核心（平台无关，不依赖 GUI / Windows API）
    campuslink.gui         PySide6 图形界面
    campuslink.config      用户配置（JSON，不含密码）
    campuslink.secrets     密码存储（系统凭据管理器）
    campuslink.profiles    门户档案（各校差异的集中处）
    campuslink.autostart   开机启动
    campuslink.logging_setup  日志（含敏感信息脱敏）

分层原则：**core 不许 import gui，也不许 import Windows 专有模块**，
这样认证逻辑将来能原样搬到 OpenWrt 上。
"""

__version__ = "1.0.1"
APP_NAME = "CampusLink"
APP_TITLE = "CampusLink 校园网认证"
ORG_NAME = "CampusLink"

__all__ = ["__version__", "APP_NAME", "APP_TITLE", "ORG_NAME"]
