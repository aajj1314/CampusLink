# -*- coding: utf-8 -*-
"""
校园网共享（Windows 移动热点）—— TTL 修复与状态检测。

## 为什么需要它

Windows 的「移动热点」用 ICS 做 NAT/DHCP/DNS，开一次就能共享。但校园网
出口的 TCP 代理回 SYN-ACK 时 **TTL=1**，Windows 转发时递减到 0 直接当
过期包丢弃 —— 结果是「手机连上了热点、网页全打不开」。同时手机(64)与
Windows 设备(128)的包混在一个出口，容易被私接检测盯上。

所以共享功能的核心只有两件事（热点本身不用程序管）：

  1. inbound 且 TTL<=1 → 改成 64（救回校园网代理的 SYN-ACK）
  2. outbound 且 TTL==63/127 → 改成 64（转发出去看起来是一台设备）

Windows 没有 iptables，用户在态改 IP 头只能靠 WinDivert（`pydivert`）。

## 依赖与权限

- `pydivert`（可选依赖）：缺失时共享功能不可用，但认证功能不受影响
- 必须以管理员运行（WinDivert 要装内核驱动）

本模块只做「修 TTL + 报告状态」，不碰热点开关（Windows 设置里开）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

#: ICS 移动热点的固定网关地址 —— 它出现在本机 IPv4 列表里 = 热点已开启
ICS_GATEWAY = "192.168.137.1"

#: WinDivert 过滤器（2026-11 在 Win11 实测验证过的一组）
FILTER_INBOUND = "inbound and ip and ip.TTL <= 1"
FILTER_OUTBOUND = "outbound and ip and (ip.TTL == 63 or ip.TTL == 127)"
TTL_TARGET = 64


# ============================================================ 权限
def is_admin() -> bool:
    """当前进程是否管理员（WinDivert 装驱动必须）。"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(extra_args: str = "") -> bool:
    """
    以管理员身份重启自己（UAC 弹窗），当前进程退出由调用方决定。

    frozen（exe）时重启 exe；开发期重启 `python -m campuslink`。
    返回 UAC 是否被接受（用户点「是」）。
    """
    try:
        import ctypes
        if getattr(sys, "frozen", False):
            exe, params = sys.executable, extra_args
        else:
            exe = sys.executable
            params = f'-m campuslink {extra_args}'.strip()
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, str(Path(exe).parent), 1)
        return rc > 32
    except Exception:
        return False


def open_hotspot_settings() -> bool:
    """打开 Windows「移动热点」设置页。"""
    try:
        os.startfile("ms-settings:network-mobilehotspot")
        return True
    except Exception:
        return False


# ============================================================ pydivert
def pydivert_available() -> tuple[bool, str]:
    """返回 (可用?, 原因)。缺失/驱动装不上时给出人话理由。"""
    try:
        import pydivert  # noqa: F401
        return True, ""
    except Exception as exc:
        return False, f"pydivert 不可用（{type(exc).__name__}）"


# ============================================================ 状态
@dataclass
class HotspotStatus:
    admin: bool = False
    pydivert_ok: bool = False
    pydivert_reason: str = ""
    ttl_running: bool = False
    hotspot_on: bool = False          # 192.168.137.1 是否在本机网卡上
    packets_in: int = 0               # 回程救回的包数
    packets_out: int = 0              # 出站归一改写的包数
    last_error: str = ""

    @property
    def ready_hint(self) -> str:
        """给 UI 的一句话状态。"""
        if not self.pydivert_ok:
            return "共享助手不可用：" + (self.pydivert_reason or "缺少 pydivert")
        if not self.admin:
            return "需要管理员权限才能启用共享助手"
        if self.ttl_running and self.hotspot_on:
            return "共享中：热点已开启，TTL 修复生效"
        if self.ttl_running:
            return "TTL 修复已就绪，但热点还没开（设置 → 移动热点）"
        return "共享助手未启用"


# ============================================================ TTL 修复
class TtlFixer:
    """
    起两条 WinDivert 过滤器线程改 TTL。

    设计要点：
      · 线程 daemon，stop() 置事件 + WinDivert 迭代自然退出
      · 单个过滤器异常自动 5 秒重试（驱动偶发抽风不致命）
      · 计数器只增不读锁（int 自增在 CPython 下线程安全够用）
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.packets_in = 0
        self.packets_out = 0
        self.last_error = ""
        self._started = False

    # -------------------------------------------------- 生命周期
    @property
    def running(self) -> bool:
        return self._started and not self._stop.is_set()

    def start(self) -> tuple[bool, str]:
        """启动。返回 (成功?, 原因)。重复调用是幂等的。"""
        if self.running:
            return True, "已在运行"
        ok, why = pydivert_available()
        if not ok:
            return False, why
        if not is_admin():
            return False, "需要管理员权限"

        self._stop.clear()
        self._started = True
        specs = (
            (FILTER_INBOUND, "in", "回程"),
            (FILTER_OUTBOUND, "out", "出站"),
        )
        self._threads = []
        for flt, tag, name in specs:
            t = threading.Thread(target=self._loop, args=(flt, tag, name),
                                 name=f"ttl-{tag}", daemon=True)
            t.start()
            self._threads.append(t)
        return True, "TTL 修复已启动"

    def stop(self) -> None:
        self._stop.set()
        self._started = False

    # -------------------------------------------------- 工作线程
    def _loop(self, flt: str, tag: str, name: str) -> None:
        import pydivert

        while not self._stop.is_set():
            try:
                with pydivert.WinDivert(flt) as w:
                    for packet in w:
                        if self._stop.is_set():
                            break
                        packet.ip.ttl = TTL_TARGET
                        w.send(packet)
                        if tag == "in":
                            self.packets_in += 1
                        else:
                            self.packets_out += 1
            except Exception as exc:
                if self._stop.is_set():
                    break
                self.last_error = f"[{name}] {type(exc).__name__}: {exc}"
                self._stop.wait(5.0)


# ============================================================ 热点检测
def local_ipv4_all() -> list[str]:
    """本机全部 IPv4（用 ipconfig，只解析 ASCII，避开中文编码）。"""
    import re as _re
    ips: list[str] = []
    try:
        out = subprocess.run(["ipconfig"], capture_output=True, timeout=10).stdout
        for line in out.decode("ascii", "replace").splitlines():
            if "IPv4" not in line:
                continue
            m = _re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
            if m and m.group(1) not in ips:
                ips.append(m.group(1))
    except Exception:
        pass
    return ips


def hotspot_on() -> bool:
    """ICS 网关存在 = 移动热点已开启（Win10/11 通用判据）。"""
    return ICS_GATEWAY in local_ipv4_all()


def collect_status(fixer: TtlFixer | None = None) -> HotspotStatus:
    """给 UI 的一次性状态快照。"""
    ok, why = pydivert_available()
    st = HotspotStatus(
        admin=is_admin(),
        pydivert_ok=ok,
        pydivert_reason=why,
        hotspot_on=hotspot_on(),
    )
    if fixer is not None:
        st.ttl_running = fixer.running
        st.packets_in = fixer.packets_in
        st.packets_out = fixer.packets_out
        st.last_error = fixer.last_error
    return st
