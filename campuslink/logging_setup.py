# -*- coding: utf-8 -*-
"""
日志。**敏感信息一律不得落盘。**

任务书明确要求：禁止将密码、Token、Cookie 等输出到日志。
光靠「写代码时小心」是不够的 —— 任何一次疏忽都会把用户密码写进文件，
而日志文件经常被用户随手发给别人排查问题。

所以这里做了两层防护：

1. **运行时登记脱敏词。** 拿到密码、Cookie、token 的那一刻就调用
   `register_secret(value)`，之后**任何**日志记录里出现这个字符串都会被替换成
   `***`。这层的价值是：即使某处代码不小心把 payload 整个打出来了，
   密码也不会泄漏。

2. **正则兜底。** 对 password=/pwd=/token/cookie 这类键值对做模式替换，
   覆盖没登记过的内容。

另外，日志文件超过 1 MB 自动轮转一代，避免无限增长。
"""
from __future__ import annotations

import logging
import logging.handlers
import re
import sys
import threading
from pathlib import Path

from . import paths

LOGGER_NAME = "campuslink"
MAX_BYTES = 1024 * 1024
BACKUP_COUNT = 1

# 运行期登记的敏感字符串（只存哈希前的明文片段，进程内内存）
_secrets: set[str] = set()
_lock = threading.Lock()
MASK = "***"

# 兜底正则：常见敏感键名
_PATTERNS = [
    re.compile(r"(password|passwd|pwd)\s*[=:]\s*[^\s&,;'\"]+", re.I),
    re.compile(r"(token|access_?token|refresh_?token)\s*[=:]\s*[^\s&,;'\"]+", re.I),
    re.compile(r"(cookie|set-cookie)\s*[=:]\s*[^\n\r]+", re.I),
    re.compile(r"(__VIEWSTATE|__EVENTVALIDATION)\s*[=:]\s*[^\s&,;'\"]+", re.I),
]


def register_secret(value: str) -> None:
    """
    登记一个敏感字符串，之后它不会再出现在任何日志里。

    在拿到密码 / Cookie / 验证码令牌时调用。太短的字符串（<4 字符）
    不登记 —— 否则会把正常文本也打成星号。
    """
    if not value or len(value) < 4:
        return
    with _lock:
        _secrets.add(value)


def forget_secrets() -> None:
    """清空登记表（一般不需要，切换账号时可以调）。"""
    with _lock:
        _secrets.clear()


def scrub(text: str) -> str:
    """把文本里的敏感内容替换成 ***。公开出来方便别处复用（例如异常详情）。"""
    if not text:
        return text
    with _lock:
        for s in _secrets:
            if s in text:
                text = text.replace(s, MASK)
    for pat in _PATTERNS:
        text = pat.sub(lambda m: m.group(0).split("=")[0].split(":")[0].strip()
                       + "=" + MASK, text)
    return text


class _ScrubFilter(logging.Filter):
    """在记录进入 handler 之前把消息和参数都洗一遍。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = scrub(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: (scrub(v) if isinstance(v, str) else v)
                                   for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(scrub(a) if isinstance(a, str) else a
                                        for a in record.args)
            if record.exc_text:
                record.exc_text = scrub(record.exc_text)
        except Exception:
            pass
        return True


_configured = False


def setup(level: int = logging.INFO, console: bool = True,
          log_dir: Path | None = None) -> logging.Logger:
    """
    初始化日志。重复调用是安全的。

    console：GUI 打包成 --noconsole 后 stdout 不存在，
             所以为 None 时要自动跳过控制台 handler
             （否则 Python 会抛 AttributeError）。
    """
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")
    flt = _ScrubFilter()

    # --- 文件 ---
    try:
        d = log_dir or paths.log_dir()
        d.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            d / "campuslink.log", maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(flt)
        logger.addHandler(fh)
    except Exception:
        pass

    # --- 控制台 ---
    if console and getattr(sys, "stdout", None) is not None:
        try:
            ch = logging.StreamHandler(sys.stdout)
            ch.setFormatter(fmt)
            ch.addFilter(flt)
            logger.addHandler(ch)
        except Exception:
            pass

    if not logger.handlers:          # 极端情况下至少别静默丢掉日志
        logger.addHandler(logging.NullHandler())

    _configured = True
    logger.info("日志已启动 | %s", paths.describe())
    return logger


def get_logger(name: str = "") -> logging.Logger:
    """取子 logger，例如 get_logger("gui")。"""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)
