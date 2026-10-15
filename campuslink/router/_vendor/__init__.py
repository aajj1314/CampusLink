# -*- coding: utf-8 -*-
"""
依赖 vendor 化（任务书 Agent 1 · 工作内容 1）。

把 `requests` / `urllib3` / `bs4`（纯 Python 部分）vendor 进本目录的
`packages/` 子目录，在 OpenWrt 上避免跑 pip（python3-pip 约 6 MB 且
mipsel 轮子经常缺失）。`lxml` 是 C 扩展，走 OpenWrt feed 包
（python3-lxml）；feed 缺失时，bs4 的 `"lxml"` 解析请求自动退回
`html.parser`（本门户页面仅 ~5 KB，性能无碍）。

开关：
  · `install()` —— 导入前调用一次，把 `packages/` 注入 sys.path 并
    视 lxml 可用性决定是否启用 html.parser 兜底。
  · PC（router_sim / Windows）上依赖已随 requirements.txt 安装，
    `install()` 是无害的空操作。

本目录由 `tools/vendor_deps.py` 生成（拷贝当前环境已安装的纯 Python 包），
不会污染 requirements.txt，也不在设备上跑 pip。
"""
from __future__ import annotations

import os
import sys

_fallback_done = False


def install(prefer_vendor: bool | None = None) -> None:
    """
    导入前调用一次（幂等）。

    `prefer_vendor` 为 True 时把 `packages/` 注入 sys.path（OpenWrt 用）；
    缺省（None）时看环境变量 `CAMPUSLINK_VENDOR=1`，方便 procd init 脚本
    直接设变量而不用改代码。PC（router_sim / Windows）上 lxml 已装、依赖已随
    requirements.txt 安装，此调用为空操作（仅必要时启用 lxml 兜底）。
    """
    global _fallback_done
    if prefer_vendor is None:
        prefer_vendor = os.environ.get("CAMPUSLINK_VENDOR", "") in (
            "1", "true", "yes", "on")
    if prefer_vendor:
        _inject_packages()
    if not _fallback_done:
        _install_lxml_fallback()
        _fallback_done = True


def _inject_packages() -> None:
    _pkg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "packages")
    if os.path.isdir(_pkg_dir) and _pkg_dir not in sys.path:
        sys.path.insert(0, _pkg_dir)


def _install_lxml_fallback() -> bool:
    """
    若 lxml 不可用，把 bs4 的 `"lxml"` 解析请求退到 html.parser。

    原理：bs4.builder.builder_registry 按 feature 名找 TreeBuilder。
    lxml 没装时 `lookup("lxml")` 本来会抛 FeatureNotFound；这里注册一个
    继承 HTMLParserTreeBuilder、只宣称 feature=["lxml"] 的子类，于是
    `BeautifulSoup(html, "lxml")` 会落到 html.parser 上——core/portal.py
    无需改动即可在无 lxml 的 OpenWrt 上运行。

    返回 True 表示启用了兜底（lxml 确实缺失）。
    """
    try:
        import lxml  # noqa: F401
        return False
    except ImportError:
        pass

    try:
        from bs4.builder import HTMLParserTreeBuilder, builder_registry

        class _LxmlFallback(HTMLParserTreeBuilder):
            NAME = "lxml"
            features = ["lxml"]

        builder_registry.register(_LxmlFallback)
        return True
    except Exception:
        return False
