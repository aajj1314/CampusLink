# -*- coding: utf-8 -*-
"""
验证码识别 —— 从现有 captcha-login/ocr_engine.py 迁移而来，逻辑保持一致。

迁移时保留的东西（都是上一版实测换来的）：
  · 四种预处理策略全部保留。默认只用 raw+gray，因为对
    桂林信息科技学院这个门户，实测 20 张真实样本 raw 对了 18 张，
    而 color/denoise 几乎全错（会输出「产」「了」「;」这类字符）。
    但换一个学校结论可能相反，所以策略本身不能删。
  · 投票时把非字母数字的结果剔除（CHAR_RE 校验）。
  · 投票按小写归并 —— ddddocr 对大小写区分很弱，
    同一张图不同策略给出 '945e' 和 '945E' 其实是同一个答案。
  · 不设置 set_ranges：实测 ddddocr 1.6.1 上它是负优化。

平台无关：只依赖 numpy / opencv / ddddocr，三者都有 ARM 版本，
为将来移植到 OpenWrt 留了余地（那边如果跑不动 opencv，
只要实现一个同样签名的 recognize() 即可替换本模块）。
"""
from __future__ import annotations

import io
import re
import threading
from typing import Callable

import cv2
import numpy as np

# 只允许字母数字进入投票 —— 排除掉模型幻觉出来的汉字和符号
CHAR_RE = re.compile(r"^[0-9A-Za-z]+$")


# ------------------------------------------------------------------ 预处理
def prep_raw(img_bytes: bytes) -> bytes:
    """原图直接送去识别。别小看它，多数验证码上它就是最准的。"""
    return img_bytes


def _decode(img_bytes: bytes) -> np.ndarray | None:
    arr = np.frombuffer(img_bytes, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _encode(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes() if ok else b""


def prep_gray(img_bytes: bytes) -> bytes:
    """灰度。和 raw 表现接近，常能作为第二个投票者。"""
    img = _decode(img_bytes)
    if img is None:
        return img_bytes
    return _encode(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))


def prep_denoise(img_bytes: bytes) -> bytes:
    """
    灰度 + 自适应阈值 + 中值滤波。

    适合作笔画较细、背景干净的验证码；对本项目这个门户反而有害
    （会打断笔画导致少认字符），所以默认不启用。
    """
    img = _decode(img_bytes)
    if img is None:
        return img_bytes
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                   cv2.THRESH_BINARY, 11, 2)
    return _encode(cv2.medianBlur(binary, 3))


def prep_color(img_bytes: bytes) -> bytes:
    """
    饱和度分离 + 形态学开运算。

    思路：彩色笔画饱和度高、浅色背景饱和度低，所以走 HSV 的 S 通道能
    把字符和背景分开；再做开运算，1px 的彩色干扰线会被整条削掉，
    而粗笔画字符只是略微变瘦。

    这条链路对「粗字 + 细线」的验证码很强（最初的参考图上它是唯一读对的），
    但对笔画偏细的验证码会破坏字形。要不要用，靠真实样本实测决定。
    """
    img = _decode(img_bytes)
    if img is None:
        return img_bytes
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    _, sat, _ = cv2.split(hsv)
    binary = cv2.adaptiveThreshold(sat, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                   cv2.THRESH_BINARY, 11, 2)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    return _encode(cv2.bitwise_not(cleaned))


STRATEGIES: dict[str, Callable[[bytes], bytes]] = {
    "raw": prep_raw,
    "gray": prep_gray,
    "denoise": prep_denoise,
    "color": prep_color,
}


# ------------------------------------------------------------------ ddddocr
_ocr_lock = threading.Lock()
_ocr_cache: dict[tuple[bool, int | None], object] = {}


def get_ocr(beta: bool = False):
    """
    获取（并缓存）ddddocr 实例。

    首次调用会加载 ONNX 模型（几十 MB），大约要 1～2 秒，
    所以 GUI 里必须在工作线程里预热，不能放在主线程卡界面。

    加锁是因为 QThread 里可能并发调用；ddddocr 的实例不是线程安全的。
    """
    key = (beta, None)
    with _ocr_lock:
        if key not in _ocr_cache:
            import ddddocr

            try:
                inst = ddddocr.DdddOcr(beta=beta, show_ad=False)
            except TypeError:              # 老版本没有 show_ad 参数
                inst = ddddocr.DdddOcr(beta=beta)
            _ocr_cache[key] = inst
        return _ocr_cache[key]


def warmup() -> None:
    """预加载模型，供 GUI 在后台线程启动时调用。"""
    get_ocr(False)


# ------------------------------------------------------------------ 识别
def recognize(
    img_bytes: bytes,
    strategies: tuple[str, ...] | list[str] = ("raw", "gray"),
    length: int | None = 4,
    beta: bool = False,
) -> tuple[str, dict[str, str]]:
    """
    多策略识别 + 投票。

    返回 (best, detail)
        best   : 最终结果；所有策略都没给出合法结果时为空字符串
        detail : {策略名: 原始输出}，用于排查（不含敏感信息，可以进日志）
    """
    ocr = get_ocr(beta)
    detail: dict[str, str] = {}
    votes: dict[str, int] = {}
    order: list[str] = []
    display: dict[str, str] = {}

    for name in strategies:
        fn = STRATEGIES.get(name)
        if fn is None:
            continue
        try:
            text = ocr.classification(fn(img_bytes))
        except Exception as exc:           # 单个策略失败不影响整体
            detail[name] = f"<error: {type(exc).__name__}>"
            continue

        text = (text or "").strip()
        detail[name] = text

        if not text or not CHAR_RE.match(text):
            continue
        if length and len(text) != length:
            continue

        key = text.lower()
        votes[key] = votes.get(key, 0) + 1
        if key not in order:
            order.append(key)
            display[key] = text

    if not votes:
        return "", detail

    best = max(order, key=lambda t: votes[t])   # 同票按 strategies 顺序优先
    return display[best], detail


def confidence(best: str, detail: dict[str, str]) -> float:
    """
    粗略置信度 = 一致的策略数 / 有效策略数。

    忽略大小写：ddddocr 很容易把同一张图的不同策略输出成
    '945e' 和 '945E'，那其实是同一个答案。
    """
    if not best:
        return 0.0
    valid = [v for v in detail.values() if v and CHAR_RE.match(v)]
    if not valid:
        return 0.0
    needle = best.lower()
    return sum(1 for v in valid if v.lower() == needle) / len(valid)
