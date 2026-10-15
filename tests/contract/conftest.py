# -*- coding: utf-8 -*-
"""契约测试公共夹具（tests/contract）。"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from campuslink.router import (ApiServer, AuthController, PairingManager,
                               RouterContext, TaskManager)  # noqa: E402


# ---------------------------------------------------------------- 工具
def make_png(width: int, height: int, rgb=(0, 0, 0)) -> bytes:
    """用纯标准库生成一张合法 PNG（测试图像尺寸校验用）。"""
    def chunk(typ: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)   # 8bit RGB
    row = b"\x00" + bytes(rgb) * width
    idat = zlib.compress(row * height, 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def scan_forbidden(obj, allow_keys: frozenset = frozenset()) -> list[str]:
    """递归扫描 JSON 结构里的敏感字样（cookie/password/token 等）。"""
    hits: list[str] = []
    forbidden = ("cookie", "password", "passwd", "token", "secret",
                 "authorization", "credential")
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = k.lower()
            if kl not in allow_keys and any(f in kl for f in forbidden):
                hits.append(f"key:{k}")
            hits.extend(scan_forbidden(v, allow_keys))
    elif isinstance(obj, list):
        for v in obj:
            hits.extend(scan_forbidden(v, allow_keys))
    elif isinstance(obj, str):
        low = obj.lower()
        if any(f in low for f in forbidden):
            hits.append(f"value:{obj[:40]}")
    return hits


class Router:
    """对一个运行中控制面的 HTTP 客户端封装。"""

    def __init__(self, base: str, ctx: RouterContext, srv: ApiServer):
        self.base = base
        self.ctx = ctx
        self.srv = srv

    def get(self, path, **kw):
        return requests.get(self.base + path, **kw)

    def post(self, path, **kw):
        return requests.post(self.base + path, **kw)

    def put(self, path, **kw):
        return requests.put(self.base + path, **kw)

    def delete(self, path, **kw):
        return requests.delete(self.base + path, **kw)


@pytest.fixture()
def router():
    """每个用例独立的控制面（未配置认证），避免跨用例状态泄漏。"""
    tm = TaskManager()
    ctx = RouterContext(taskmanager=tm, pairing=PairingManager(),
                        auth=AuthController(tm), portal_name="测试门户")
    srv = ApiServer(ctx, port=0)
    s = srv.start()
    base = f"http://127.0.0.1:{s.server_address[1]}"
    yield Router(base, ctx, srv)
    srv.shutdown()


@pytest.fixture()
def paired(router: Router) -> dict:
    """一个已配对设备（跟随 router 夹具，函数级）。"""
    pin = router.post("/api/v1/pair/start").json()["pin"]
    r = router.post("/api/v1/pair/confirm", json={
        "pin": pin, "device_name": "测试设备", "device_kind": "desktop",
        "capabilities": ["ocr", "manual"],
    }).json()
    return {"token": r["device_token"], "device_id": r["device_id"],
            "headers": {"Authorization": "Bearer " + r["device_token"]}}


@pytest.fixture(scope="session")
def captcha_png() -> bytes:
    return (ROOT / "tests" / "fixtures" / "captcha_sample.png").read_bytes()
