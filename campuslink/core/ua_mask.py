# -*- coding: utf-8 -*-
"""
HTTP User-Agent 归一化（2026-11-18 新增）—— Windows 侧的 ua2f 替代品。

## 为什么需要它

校园网的私接/多设备检测不只看 TTL，还会看 HTTP 请求里的 User-Agent：

    同一出口 IP 下出现
      Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/131   ← 台式机
      Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 ...)              ← 手机
      Mozilla/5.0 (Linux; Android 13; SM-S918B) ...             ← 另一台手机
      Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) ...   ← Firefox
    → DPI 一眼看出"这个 IP 后面不止一台设备"

把这些请求的 UA 统一改成一个值，出口看起来就是一台机器。

## 为什么只在 Windows 侧做

路由器方案（见 `docs/design/09`）用的是 ua2f（C 语言，nfqueue 原地改包），
效果好得多。但有两个场景路由器管不到：

  1. Windows 开移动热点共享（`core/hotspot.py` 那套）
  2. 临时插网线直连校园网做测试

这两个场景下流量根本不经过路由器，只能在 Windows 本机改。

## 关键限制（与 ua2f 完全一致，不是实现偷懒）

**不能改变 TCP 包的长度。** 原因：

    改长度 = 后续所有包的 TCP 序列号都要平移 = 必须拦截整条连接
    = 要做完整的 TCP 重组（那是 tcpdump 级别的工作量，不是改包的活）

所以采用**原地替换 + 空格填充**：

    原 UA  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/131"  (120 字节)
    新 UA  "Mozilla/5.0                                                "  (120 字节)

HTTP 规范规定 UA 值两端的空白必须被忽略（RFC 7231 §5.5.3），
所以服务端解析出来的就是干净的 `Mozilla/5.0`。

**这带来一个硬限制：新 UA 不能比原 UA 长。** 短 UA 会被截断：

    原 UA  "curl/8.0"   (8 字节)
    目标   "Mozilla/5.0" (11 字节)  → 只能写入 "Mozilla/"（截断）

实测（详见 docs/design/09）：
    · 真实浏览器 UA 普遍 90~130 字节 → 完整替换 ✅
    · curl / wget / 脚本类 8~15 字节 → 截断 ⚠️
    · 恶意构造的超短 UA 无法处理（本来就少见）

## 只处理 HTTP，不处理 HTTPS

443 端口的流量在 TLS 加密层里，UA 是密文的一部分，改不了。
这是协议决定的，任何工具都做不到（除非做 TLS 中间人，那需要装根证书）。

所以本模块的实际覆盖面是**明文 HTTP 流量**，主要价值在于：
  · 校园网认证页 / 门户（通常是 HTTP）
  · 老式检测探针
  · 运营商 HTTP 劫持/插播
"""
from __future__ import annotations

import re
import threading

#: 默认目标 UA。12 字节，能覆盖绝大多数真实浏览器 UA。
#:
#: 为什么这么短：见模块开头的「关键限制」。越短覆盖面越广。
#: 如果你想伪装成具体浏览器版本，可以改这个值，但要接受
#: 「短 UA 会被截断」的后果 —— 例如设成完整 Chrome UA（111 字节）时，
#: 原 UA 只有 8 字节的 curl 请求会变成 "Mozilla/5.0 ("。
DEFAULT_UA = "Mozilla/5.0"

#: 匹配 HTTP 请求头里的 User-Agent 行。
#:
#: 要点：
#:   · 只匹配行首（^）后跟 User-Agent，避免误中 body 里的字符串
#:   · 用 \r\n 界定行尾，HTTP 头必须以 CRLF 分隔
#:   · re.IGNORECASE 因为头字段名大小写不敏感（RFC 7230 §3.2）
#:   · 用 MULTILINE 让 ^ 匹配每行开头
UA_LINE_RE = re.compile(
    rb"^(User-Agent[ \t]*:[ \t]*)([^\r\n]*)(\r\n)",
    re.IGNORECASE | re.MULTILINE,
)

#: 跳过这些 UA —— 它们是「非浏览器」的标志，改了反而更可疑。
#:
#: 为什么：一个正常家庭网络里，手机/电脑都是浏览器 UA，
#: 但如果把所有东西（包括 Windows 更新、Office、各种 SDK）
#: 都改成 Mozilla/5.0，反而会出现「一台机器只有一种 UA」的异常。
#:
#: 实测取舍：这些客户端本来就少，留着它们更自然。
SKIP_PREFIXES = (
    "Microsoft-",       # Windows Update / Defender
    "Mozilla/4.0 (compatible; MSIE",  # 老 IE，极少
    "Wget/",
    "python-requests",
    "curl/",
    "Go-http-client",
    "Java/",
    "okhttp/",
)


def is_http_request(payload: bytes) -> bool:
    """
    判断 payload 是不是 HTTP **请求**（不是响应）。

    为什么必须区分：响应的 header 里也可能有 User-Agent？
    不会 —— UA 只在请求里。但要判断方向，否则会误改响应。
    简易判据：请求行形如 `METHOD SP PATH SP HTTP/1.x CRLF`。
    """
    if len(payload) < 16:
        return False
    # 常见的 HTTP 方法
    for m in (b"GET ", b"POST ", b"HEAD ", b"PUT ", b"DELETE ",
              b"OPTIONS ", b"PATCH ", b"CONNECT ", b"TRACE "):
        if payload.startswith(m):
            return True
    return False


def should_skip(ua: bytes) -> bool:
    """这个 UA 是否应该放过（非浏览器客户端）。"""
    try:
        text = ua.decode("latin-1")
    except Exception:
        return True
    for p in SKIP_PREFIXES:
        if text.startswith(p):
            return True
    return False


def build_replacement(old_len: int, target: str = DEFAULT_UA) -> bytes | None:
    """
    构造与 old_len 等长的替换值。

    返回 None 表示「这个长度没法处理」（目标比原值长太多，截断后无意义）。

    规则：
      · 原 UA 长度 >= 目标长度 → 目标 + 空格填充
      · 原 UA 长度 <  目标长度 → 截断目标（与 ua2f 行为一致）
      · 原 UA 长度 <  3        → 放弃（截断后只剩一两个字，反而更假）
    """
    tgt = target.encode("latin-1", "replace")

    if old_len < 3:
        return None

    if old_len >= len(tgt):
        return tgt + b" " * (old_len - len(tgt))

    # 比目标短：截断（ua2f 同样行为）
    return tgt[:old_len]


def mask_ua_in_payload(payload: bytes, target: str = DEFAULT_UA) -> tuple[bytes, bool]:
    """
    在 HTTP 请求 payload 里原地替换 User-Agent。

    返回 (新 payload, 是否改过)。
    没改过时返回原对象（调用方可以据此跳过 recalculate_checksums）。

    注意：一个 TCP 段里可能有多条 HTTP 请求（pipelining），
    也可能只有半个请求（分包）。这里只处理**完整的 UA 行**，
    跨包的 UA 头会被漏掉 —— 这是可接受的：
      · 实际 UA 行极少跨包（通常 < 200 字节，远小于 MSS）
      · 漏掉个别请求不影响整体指纹（大部分还是改掉了）
    """
    if not is_http_request(payload):
        return payload, False

    changed = False
    out = payload

    # 从后往前替换：保证前面匹配的偏移不受影响
    matches = list(UA_LINE_RE.finditer(out))
    for m in reversed(matches):
        ua = m.group(2)
        if should_skip(ua):
            continue
        new_ua = build_replacement(len(ua), target)
        if new_ua is None or new_ua == ua:
            continue
        out = out[:m.start(2)] + new_ua + out[m.end(2):]
        changed = True

    return out, changed


# ============================================================ pydivert 集成
#: 只拦 outbound 的 80 端口。inbound 不用管（响应里没有 UA）。
FILTER_UA = "outbound and tcp.DstPort == 80 and tcp.PayloadLength > 0"


def pydivert_available() -> tuple[bool, str]:
    """与 hotspot.py 里的同名函数保持一致的行为。"""
    try:
        import pydivert  # noqa: F401
        return True, ""
    except Exception as exc:
        return False, f"pydivert 不可用（{type(exc).__name__}）"


def is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class UaMasker:
    """
    起一条 WinDivert 过滤器改 HTTP User-Agent。

    结构与 hotspot.TtlFixer 一致：
      · daemon 线程，stop() 置事件
      · 异常自动 5 秒重试
      · 计数器无锁自增

    **与 TtlFixer 分开的原因**：两者的 WinDivert 过滤器不同，
    如果合成一个 handle，过滤器表达式会变复杂（or 连接），
    而且无法单独关闭其中一项。分开更清晰，代价是多一个句柄（开销可忽略）。
    """

    def __init__(self, target_ua: str = DEFAULT_UA) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._target = target_ua
        self.packets_masked = 0     # 改过的包数
        self.packets_seen = 0       # 见过的 HTTP 请求包数
        self.last_error = ""
        self.last_miss = ""         # 最近一个被跳过的 UA（诊断用）

    @property
    def running(self) -> bool:
        return self._started and not self._stop.is_set()

    @property
    def target_ua(self) -> str:
        return self._target

    def set_target(self, ua: str) -> None:
        """
        改目标 UA。**下次改动立即生效**，不需要重启。

        为什么能做到：_loop 每处理一个包都读 self._target，
        不是启动时捕获的局部变量。
        """
        self._target = ua or DEFAULT_UA

    def start(self) -> tuple[bool, str]:
        if self.running:
            return True, "已在运行"
        ok, why = pydivert_available()
        if not ok:
            return False, why
        if not is_admin():
            return False, "需要管理员权限"

        self._stop.clear()
        self._started = True
        self._thread = threading.Thread(
            target=self._loop, name="ua-mask", daemon=True)
        self._thread.start()
        return True, "UA 归一已启动"

    def stop(self) -> None:
        self._stop.set()
        self._started = False

    def _loop(self) -> None:
        import pydivert

        while not self._stop.is_set():
            try:
                with pydivert.WinDivert(FILTER_UA) as w:
                    for packet in w:
                        if self._stop.is_set():
                            break
                        self._handle(packet, w)
            except Exception as exc:
                if self._stop.is_set():
                    break
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._stop.wait(5.0)

    def _handle(self, packet, w) -> None:
        """
        处理单个包。异常必须吞掉 —— 一个包出错不能让整个线程重启。

        注意：**无论如何都要 send 回去**。WinDivert 拦下来的包
        如果不 send，等于把连接挂死。
        """
        try:
            payload = packet.payload
            if not payload:
                w.send(packet)
                return

            self.packets_seen += 1
            new_payload, changed = mask_ua_in_payload(payload, self._target)

            if changed:
                packet.payload = new_payload
                # 改 payload 必须重算校验和，否则服务端认为是坏包
                packet.recalculate_checksums()
                self.packets_masked += 1

        except Exception as exc:
            self.last_error = f"handle: {type(exc).__name__}: {exc}"
        finally:
            try:
                w.send(packet)
            except Exception:
                pass


# ============================================================ 状态快照
def describe(target_ua: str = DEFAULT_UA) -> dict:
    """
    给 UI / 日志用的一句话状态。

    返回 dict 而不是格式化字符串，让调用方自己决定怎么显示
    （GUI 要中文、日志要英文时可以各取所需）。
    """
    ok, why = pydivert_available()
    return {
        "pydivert_ok": ok,
        "pydivert_reason": why,
        "admin": is_admin(),
        "target_ua": target_ua,
        "target_len": len(target_ua),
        "covers": "仅明文 HTTP（80 端口）；HTTPS 的 UA 在 TLS 层，改不了",
        "limit": "新 UA 不能长于原 UA；短 UA 会被截断（与 ua2f 一致）",
    }
