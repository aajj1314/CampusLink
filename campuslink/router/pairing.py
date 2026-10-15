# -*- coding: utf-8 -*-
"""
设备配对与身份验证 —— PIN 换 device_token（03 文档 §1 / 02 文档 §3）。

安全要点：
  · 6 位 PIN，60 s 单次有效
  · PIN 连续错 5 次 → 锁定 5 min（防爆破）
  · device_token 只存 SHA-256，明文只在签发当次返回一次
  · 每 Router 最多 8 台已配对设备
  · 吊销后旧 token 立即 401
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field

PIN_TTL = 60.0            # PIN 有效期（秒）
PIN_LENGTH = 6
MAX_DEVICES = 8
LOCK_AFTER = 5            # 连续错误次数触发锁定
LOCK_SECONDS = 300.0      # 锁定时长（5 min）


@dataclass
class DeviceIdentity:
    device_id: str
    device_name: str
    device_kind: str            # "desktop" | "mobile"
    token_hash: bytes          # SHA-256(token)；不存明文
    capabilities: list[str] = field(default_factory=list)
    created_at: float = 0.0
    last_seen: float = 0.0
    revoked: bool = False

    def public(self) -> dict:
        return {
            "id": self.device_id,
            "name": self.device_name,
            "kind": self.device_kind,
            "capabilities": list(self.capabilities),
            "last_seen": _iso(self.last_seen) if self.last_seen else None,
            "revoked": self.revoked,
        }


@dataclass
class _Pin:
    pin: str
    expires_at: float
    used: bool = False


class PairingError(Exception):
    """配对错误，携带错误码（apiserver 映射 HTTP）。"""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class PairingManager:
    def __init__(self, *, pin_ttl: float = PIN_TTL, max_devices: int = MAX_DEVICES,
                 lock_after: int = LOCK_AFTER, lock_seconds: float = LOCK_SECONDS):
        self._pin_ttl = pin_ttl
        self._max_devices = max_devices
        self._lock_after = lock_after
        self._lock_seconds = lock_seconds
        self._lock = threading.RLock()
        self._devices: dict[str, DeviceIdentity] = {}
        self._by_hash: dict[bytes, str] = {}        # token_hash -> device_id
        self._pin: _Pin | None = None
        self._failures = 0
        self._locked_until = 0.0

    # ------------------------------------------------------------ PIN
    def start_pairing(self) -> dict:
        """签发一个 6 位 PIN。返回 {pin, expires_at, pair_url}。"""
        with self._lock:
            if self._pin and not self._pin.used and time.time() < self._pin.expires_at:
                # 已有一个有效 PIN，沿用（避免旧 PIN 悬空）
                return self._pin_payload(self._pin)
            pin = "".join(str(secrets.randbelow(10)) for _ in range(PIN_LENGTH))
            self._pin = _Pin(pin=pin, expires_at=time.time() + self._pin_ttl)
            return self._pin_payload(self._pin)

    @staticmethod
    def _pin_payload(p: _Pin) -> dict:
        return {
            "pin": p.pin,
            "expires_at": _iso(p.expires_at),
            "pair_url": "/api/v1/pair/confirm",
        }

    def confirm(self, pin: str, *, device_name: str, device_kind: str,
                capabilities: list[str]) -> dict:
        """
        用 PIN 换 device_token。成功返回 {device_token, device_id, ...}。
        失败抛 PairingError。
        """
        with self._lock:
            now = time.time()
            if now < self._locked_until:
                raise PairingError("E_RATE_LIMITED", "配对尝试过于频繁，请稍后再试",
                                   retryable=True)
            p = self._pin
            if p is None or p.used or now >= p.expires_at or not _safe_eq(p.pin, pin):
                self._failures += 1
                if self._failures >= self._lock_after:
                    self._locked_until = now + self._lock_seconds
                    self._failures = 0
                    raise PairingError("E_RATE_LIMITED", "PIN 错误次数过多，已锁定 5 分钟",
                                       retryable=True)
                raise PairingError("E_BAD_REQUEST", "PIN 错误或已过期")
            # 成功：重置失败计数、作废 PIN
            self._failures = 0
            p.used = True
            if len([d for d in self._devices.values() if not d.revoked]) >= self._max_devices:
                raise PairingError("E_BUSY", "已配对设备数量已达上限", retryable=True)

            token = secrets.token_hex(24)          # 48 hex
            device_id = "dev_" + secrets.token_hex(3)   # 6 hex
            dev = DeviceIdentity(
                device_id=device_id,
                device_name=device_name or "未命名设备",
                device_kind=device_kind or "desktop",
                token_hash=_hash(token),
                capabilities=[c for c in (capabilities or []) if c in ("ocr", "manual")],
                created_at=now,
                last_seen=now,
            )
            self._devices[device_id] = dev
            self._by_hash[dev.token_hash] = device_id
            return {
                "device_token": token,
                "device_id": device_id,
                "token_expires_at": None,
                "note": "token 请妥善保存；可在路由器上随时吊销",
            }

    # ------------------------------------------------------------ 认证
    def authenticate(self, token: str) -> DeviceIdentity | None:
        """校验 Bearer token，返回对应设备（已吊销/未知返回 None）。"""
        if not token:
            return None
        with self._lock:
            dev_id = self._by_hash.get(_hash(token))
            if dev_id is None:
                return None
            dev = self._devices[dev_id]
            if dev.revoked:
                return None
            return dev

    def touch(self, device_id: str) -> None:
        with self._lock:
            dev = self._devices.get(device_id)
            if dev is not None:
                dev.last_seen = time.time()

    # ------------------------------------------------------------ 设备管理
    def list_devices(self) -> list[dict]:
        with self._lock:
            return [d.public() for d in self._devices.values() if not d.revoked]

    def revoke(self, device_id: str) -> bool:
        """吊销设备（含自己）。返回是否找到。"""
        with self._lock:
            dev = self._devices.get(device_id)
            if dev is None or dev.revoked:
                return False
            dev.revoked = True
            self._by_hash.pop(dev.token_hash, None)
            return True

    def count(self) -> int:
        with self._lock:
            return len([d for d in self._devices.values() if not d.revoked])


# ---------------------------------------------------------------- 工具
def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _safe_eq(a: str, b: str) -> bool:
    return secrets.compare_digest(a or "", b or "")


def _iso(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(
        ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
