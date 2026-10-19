# -*- coding: utf-8 -*-
"""
网络状态检测与门户自动发现。平台无关，不依赖 Windows API。

核心判据（上一版实测换来的教训）：
    **不能靠 TCP 能否连通来判断是否已联网。**
    强制门户会劫持所有 :80 连接并由网关自己代答，TCP 三次握手百分百成功 ——
    那是网关在回你，不是互联网。上一版就因为用 TCP 连通性做判据，
    把「未认证」误报成「已联网」。

    可靠判据只能是 HTTP 响应内容：
        generate_204 真联网时返回 204 且 body 为空；
        被强制门户劫持时会返回门户页面或 302 到内网地址。
"""
from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import requests

from .models import NetworkState

# 未认证时会被网关劫持到认证页的探测地址
HIJACK_PROBES: tuple[tuple[str, str, str], ...] = (
    ("generate_204", "http://connectivitycheck.gstatic.com/generate_204", "204-empty"),
    ("connecttest", "http://www.msftconnecttest.com/connecttest.txt",
     "text:Microsoft Connect Test"),
    ("firefox", "http://detectportal.firefox.com/success.txt", "text:success"),
    ("1.1.1.1", "http://1.1.1.1/", "any"),
)


@dataclass
class NetReport:
    """一次网络体检的结果。"""

    state: NetworkState = NetworkState.UNKNOWN
    local_ip: str = ""
    gateways: list[tuple[str, str]] = field(default_factory=list)
    portal_url: str = ""
    found_by: str = ""              # hijack / gateway / manual
    probes: dict[str, str] = field(default_factory=dict)   # 名称 -> clean/hijacked/unreachable
    note: str = ""

    @property
    def summary(self) -> str:
        from .models import STATE_TEXT
        return STATE_TEXT.get(self.state, "未知")


# ------------------------------------------------------------------ 本机信息
def local_ipv4() -> str:
    """
    拿到本机「默认出口」的 IPv4。

    技巧：UDP socket 的 connect() 只写路由表、不发任何报文，
    所以完全没有外网时也能拿到「如果有路会从哪个网卡出去」的地址。
    纯标准库，Linux 上同样适用。
    """
    for probe in ("8.8.8.8", "114.114.114.114", "1.1.1.1"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.5)
            s.connect((probe, 80))
            return s.getsockname()[0]
        except OSError:
            continue
        finally:
            s.close()
    return ""


def local_ipv4_all() -> list[str]:
    """
    列出本机所有网卡的 IPv4。

    Windows 用 ipconfig、Linux 用 `ip -4 addr`，都只匹配 ASCII 的 "inet"
    关键字，避开中文 Windows 的 GBK 编码问题。
    """
    ips: list[str] = []
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["ipconfig"], capture_output=True, timeout=10).stdout
            text = out.decode("ascii", "replace")
            for line in text.splitlines():
                if "IPv4" not in line:
                    continue
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
                if m and m.group(1) not in ips:
                    ips.append(m.group(1))
        else:
            out = subprocess.run(["ip", "-4", "addr"], capture_output=True,
                                 timeout=10).stdout
            for m in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+)", out.decode("ascii", "replace")):
                if m.group(1) not in ips:
                    ips.append(m.group(1))
    except Exception:
        pass
    return ips


def default_gateways(valid_ifaces: list[str] | None = None) -> list[tuple[str, str]]:
    """
    读默认网关，返回 [(网关, 出口接口), ...]。

    Windows 用 `route print -4 0.0.0.0`（数据列全是纯 IP，不受编码影响），
    Linux 读 /proc/net/route。

    valid_ifaces：只保留出口接口在这些地址里的路由。
    拔掉网卡后系统路由表里**仍会残留**指向已消失接口的条目，
    不滤掉就会误判「有多条默认路由」甚至选到死路由。
    """
    found: list[tuple[str, str]] = []
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["route", "print", "-4", "0.0.0.0"],
                                 capture_output=True, timeout=10).stdout
            for line in out.decode("ascii", "replace").splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                    gw, iface = parts[2], parts[3]
                    if re.match(r"^\d+\.\d+\.\d+\.\d+$", gw) and gw != "0.0.0.0":
                        found.append((gw, iface))
        else:
            with open("/proc/net/route", "r", encoding="ascii", errors="replace") as fp:
                for line in fp.readlines()[1:]:
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    iface, dest, gw_hex = parts[0], parts[1], parts[2]
                    if dest != "00000000":
                        continue
                    gw_int = int(gw_hex, 16)
                    gw = ".".join(str((gw_int >> (8 * i)) & 0xFF) for i in range(4))
                    found.append((gw, iface))
    except Exception:
        pass

    if valid_ifaces is not None:
        found = [(g, i) for g, i in found if i in set(valid_ifaces)]
    return found


def pick_gateway(gws: list[tuple[str, str]], ip: str) -> str:
    """从多条默认路由里挑真正的那条：出口接口 = 本机默认出口地址。"""
    if ip:
        for gw, iface in gws:
            if iface == ip:
                return gw
    for gw, _ in gws:
        if gw.startswith(("192.168.", "172.", "10.")):
            return gw
    return gws[0][0] if gws else ""


# ------------------------------------------------------------------ HTTP 探测
def _touch(url: str, timeout: float = 6.0, retries: int = 1) -> dict:
    s = requests.Session()
    s.trust_env = False          # 绕过系统代理，校园网内网直连
    s.verify = False
    s.headers.update({"User-Agent": "CampusLink/1.0"})
    for attempt in range(retries + 1):
        try:
            r = s.get(url, timeout=timeout, allow_redirects=True)
            return {"ok": True, "status": r.status_code, "final_url": r.url,
                    "location": r.headers.get("Location", ""),
                    "body": r.content[:400].decode("utf-8", "replace"),
                    "length": len(r.content)}
        except Exception as exc:
            last = f"{type(exc).__name__}"
    return {"ok": False, "error": last}


def _judge(name: str, url: str, expect: str, r: dict) -> str:
    """返回 clean（真联网）/ hijacked（被门户劫持）/ unreachable。"""
    if not r.get("ok"):
        return "unreachable"

    final = r.get("final_url", "") or ""
    if final.rstrip("/") != url.rstrip("/"):
        host = re.sub(r"^https?://", "", final).split("/")[0].split(":")[0]
        try:
            if ipaddress.ip_address(host).is_private:
                return "hijacked"
        except ValueError:
            pass
        if name == "generate_204":
            return "hijacked"

    if expect == "204-empty":
        return "clean" if (r["status"] == 204 and r["length"] == 0) else "hijacked"
    if expect.startswith("text:"):
        return "clean" if expect[5:] in r.get("body", "") else "hijacked"

    host = re.sub(r"^https?://", "", final).split("/")[0].split(":")[0]
    try:
        if ipaddress.ip_address(host).is_private:
            return "hijacked"
    except ValueError:
        pass
    return "clean"


# ------------------------------------------------------------------ 对外接口
def detect(timeout: float = 6.0) -> NetReport:
    """
    体检一次，回答「现在是什么状态、门户在哪」。

    GUI 的「网络状态」显示和自动认证的决策都基于这个结果。
    """
    rep = NetReport()
    rep.local_ip = local_ipv4()
    ips = local_ipv4_all()
    rep.gateways = default_gateways(valid_ifaces=ips or None)
    gw = pick_gateway(rep.gateways, rep.local_ip)

    probes: dict[str, str] = {}
    portal = ""
    for name, url, expect in HIJACK_PROBES:
        r = _touch(url, timeout=timeout)
        v = _judge(name, url, expect, r)
        probes[name] = v
        if v == "hijacked" and not portal:
            loc = r.get("location", "")
            cand = requests.compat.urljoin(url, loc) if loc else r.get("final_url", "")
            if cand and cand.rstrip("/") != url.rstrip("/"):
                portal, rep.found_by = cand, "hijack"

    rep.probes = probes
    global_clean = probes.get("generate_204") == "clean"

    if global_clean:
        rep.state = NetworkState.ONLINE
    elif any(v == "hijacked" for v in probes.values()):
        rep.state = NetworkState.NEED_LOGIN
    elif rep.local_ip:
        rep.state = NetworkState.NEED_LOGIN      # 有 IP 但探测都不通，当作需要认证
        rep.note = "探测均不可达，可能刚连上网络"
    else:
        rep.state = NetworkState.OFFLINE

    # 兜底：没抓到劫持证据时试一下网关。
    # 但 ONLY 在「确实不在线」时才试 —— 否则在正常家庭/办公网络下会把
    # 普通路由器当成认证门户（上一版真的这么把 192.168.1.1 写进过配置）。
    if not portal and not global_clean and gw:
        for scheme in ("http", "https"):
            u = f"{scheme}://{gw}/"
            r = _touch(u, timeout=3.0)
            if r.get("ok") and r["status"] < 500 and r["length"] > 200:
                portal, rep.found_by = u, "gateway"
                break

    rep.portal_url = portal
    return rep


def probe_candidates(candidates: list[str], timeout: float = 4.0) -> str:
    """
    依次探已知候选地址，返回第一个看起来是登录页的。

    判据不只是「能连上」——还要拿到的确实是登录页：
    响应正常、长度像样、并且出现了登录表单的标志性元素
    （password 输入框，或本门户特有的 SSO 字段）。

    为什么需要这一步见 PortalProfile.portal_candidates 的注释。
    """
    looks_login = ("type=\"password\"", "type='password'", "SSOGUITID",
                   "SSOACC", "login", "Login", "username", "password")
    for url in candidates:
        if not url:
            continue
        r = _touch(url, timeout=timeout)
        if not r.get("ok") or r["status"] >= 400:
            continue
        body = r.get("body", "")
        # body 只截了前 400 字节，命中概率不高；再按长度兜一层
        if any(k in body for k in looks_login) or r.get("length", 0) > 3000:
            return r.get("final_url") or url
    return ""


def is_online(timeout: float = 5.0) -> bool:
    """
    单点判断：现在能不能上外网。

    比 detect() 轻得多，自动重连的轮询循环用它。
    判据同样是 HTTP 内容，不是 TCP。

    **双点并行 + 保守判定（两点都通过才算在线）**：
    校园网普遍会把 gstatic 这类连通性检查域名**放行不劫持**（防止设备
    反复弹认证窗），未认证时 gstatic 也返回真 204 —— 单点判定会把
    「未认证」误报成「已联网」（2026-10-19 实测踩坑：CLI 首次配置被
    「已联网」短路跳过）。两个探测点**并行**发出（总耗时 = 最慢单点，
    不叠加），**全部**返回正确内容才确认真的出了外网；宁可多走一次
    认证，也不冒「误判在线跳过认证」的险。
    """
    probes = (
        ("http://connectivitycheck.gstatic.com/generate_204",
         lambda r: r.get("ok") and r["status"] == 204 and r["length"] == 0),
        ("http://www.msftconnecttest.com/connecttest.txt",
         lambda r: r.get("ok") and r["status"] == 200
                   and "Microsoft Connect Test" in r.get("body", "")),
    )

    def _probe(item):
        url, okf = item
        return okf(_touch(url, timeout=timeout))

    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        results = list(pool.map(_probe, probes))
    return all(results)
