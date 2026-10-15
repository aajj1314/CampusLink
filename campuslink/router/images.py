# -*- coding: utf-8 -*-
"""
图像格式与尺寸校验 —— 纯标准库，Router 上不依赖 opencv/PIL。

02 文档约束：PNG/JPEG、≤150 KB、宽 ≤400 px、高 ≤160 px。
这里只做「魔数嗅探 + 头部解析尺寸」，不真正解码像素，足够拦住超限请求。
"""
from __future__ import annotations

import struct

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"

MAX_BYTES = 150 * 1024
MAX_WIDTH = 400
MAX_HEIGHT = 160


def sniff_format(data: bytes) -> str:
    """返回 'png' / 'jpeg' / ''（未知）。"""
    if not data:
        return ""
    if data[:8] == PNG_MAGIC:
        return "png"
    if data[:3] == JPEG_MAGIC:
        return "jpeg"
    return ""


def _png_size(data: bytes) -> tuple[int, int] | None:
    """从 IHDR 读宽高（big-endian，偏移 16..24）。"""
    if len(data) >= 24 and data[:8] == PNG_MAGIC and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """扫描 JPEG SOF 标记读宽高（纯解析，不解码）。"""
    i = 2
    n = len(data)
    while i + 4 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # SOF 标记：携带帧尺寸
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if i + 9 < n:
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return int(w), int(h)
            return None
        # 无长度段标记（RST/EOI/SOI 等）
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            i += 2
            continue
        # 带长度段的标记：跳过该段
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        if seglen < 2:
            return None
        i += 2 + seglen
    return None


def image_size(data: bytes) -> tuple[int, int] | None:
    """返回 (width, height)，无法解析返回 None。"""
    fmt = sniff_format(data)
    if fmt == "png":
        return _png_size(data)
    if fmt == "jpeg":
        return _jpeg_size(data)
    return None


def validate(data: bytes) -> str:
    """
    校验图像是否满足 02 文档约束。返回空串表示通过，否则返回错误码：

      E_UNSUPPORTED_MEDIA —— 非 PNG/JPEG
      E_IMAGE_TOO_LARGE  —— 字节数超限或宽高超限
    """
    if not sniff_format(data):
        return "E_UNSUPPORTED_MEDIA"
    if len(data) > MAX_BYTES:
        return "E_IMAGE_TOO_LARGE"
    size = image_size(data)
    if size is None:
        return "E_IMAGE_TOO_LARGE"     # 打不开当超限/坏图处理
    w, h = size
    if w > MAX_WIDTH or h > MAX_HEIGHT:
        return "E_IMAGE_TOO_LARGE"
    return ""
