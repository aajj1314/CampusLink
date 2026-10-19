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

from . import captcha_math

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


# ================================================================== 算式验证码
#
# 2026-10 起本门户的验证码改版了：不再是 4 位字符，而是
#     6乘12=?    7加9=?    28÷4=?    57-0=?
# 表单标签写明「输入计算结果」，所以整条链路要的是**答案**而不是识别文本。
#
# 这种图的难点（实测 30 张样本得出）：
#   · 主字符颜色随机（青/蓝/绿/紫/灰都有），亮度和饱和度都跨很大范围
#   · 干扰线是**深色**的细斜线，直接穿过字符，去线必伤字
#   · 干扰字符是细笔画小字，比主字符淡，但也在 V<190 内
#   · 字符本身带随机倾斜，同一行也会互相压边
#
# 因此这里不做「一次识别整行」—— 整行喂 ddddocr 会把 `=?` 当成操作数的一部分
# （实测 `8加4=?` 被读成 '8加42'，`6乘1=?` 被读成 '6乘79'），答案必然算错。
# 改成：按连通域切成单字块，逐块识别，再交给 captcha_math 解析计算。
#
# 可靠性策略：多种预处理 × 双模型投票，票数不够就返回空串。
# **返回空串 = 让上层换一张图**，绝不提交一个猜出来的答案 ——
# 猜错会消耗一次登录尝试，还可能触发门户的风控。

#: 前景判定：到白色点的欧氏距离。主字符离白远，干扰字离白近。
_MATH_DIST_THRESHOLDS: tuple[int, ...] = (140, 152, 165)

#: 算式验证码只可能出现这些字符。
#: 限定字符集是本方案里**性价比最高的一招**：不加限制时 ddddocr 会把
#: `乘` 输出成 '和云弈'、把无关笔画输出成 'u'/'l'/'神'，加了之后这些幻觉
#: 全被挡在门外（实测解出率 23% → 33%，且文本干净很多）。
MATH_CHARSET = "0123456789加减乘除÷×+-"

_math_ocr_lock = threading.Lock()
_math_ocr_cache: dict[bool, object] = {}


def get_math_ocr(beta: bool = False):
    """
    取用于算式验证码的 OCR 实例。

    **必须是独立实例**：`set_ranges` 是实例级状态，如果直接在 `get_ocr()`
    返回的共享实例上调，字符型验证码的识别行为会被一起改掉（那是既有
    44 条测试覆盖的路径，不能动）。
    """
    with _math_ocr_lock:
        if beta not in _math_ocr_cache:
            import ddddocr

            inst = ddddocr.DdddOcr(beta=beta, show_ad=False)
            inst.set_ranges(MATH_CHARSET)
            _math_ocr_cache[beta] = inst
        return _math_ocr_cache[beta]


def _math_foreground(img_bytes: bytes, dist_thr: int, blur: int = 0) -> np.ndarray | None:
    """
    生成算式验证码的前景 mask。

    用「到白色点的距离」而不是亮度/饱和度：主字符虽然是深色，但颜色跨度大
    （深青、深蓝、深紫的 HSV 值差别很大），而它们共同的特征是**离白很远**。
    """
    if not img_bytes:
        return None
    try:
        img = _decode(img_bytes)
    except Exception:
        # cv2.imdecode 对空/损坏的 buffer 会抛 C++ 断言，不是 Python 异常，
        # 只有包一层才兜得住
        return None
    if img is None:
        return None
    if blur:
        img = cv2.medianBlur(img, blur)
    diff = 255.0 - img.astype(np.float32)
    dist = np.sqrt((diff ** 2).sum(axis=2))
    mask = (dist > dist_thr).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return mask


def _math_blocks(mask: np.ndarray,
                 min_h: int = 16, min_area: int = 40) -> list[tuple[int, int, int, int]]:
    """
    把 mask 切成单字块，按从左到右返回 (x, y, w, h)。

    过滤条件只用「高度」和「面积」：主字符高 28~45px，干扰小字高 15~25px，
    细干扰线高度往往 < 6px。阈值取 16 是两者的分界带的下沿 ——
    宁可多留几个候选块（噪声由解析器兜），也别把真字符漏掉。
    """
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    boxes = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if h >= min_h and a >= min_area:
            boxes.append((x, y, w, h))
    boxes.sort(key=lambda b: b[0])

    # 同一字符被开运算断成几截时，x 区间重叠的就并回去
    merged: list[tuple[int, int, int, int]] = []
    for x, y, w, h in boxes:
        if merged:
            px, py, pw, ph = merged[-1]
            if x < px + pw - 2:
                nx0, ny0 = min(px, x), min(py, y)
                nx1, ny1 = max(px + pw, x + w), max(py + ph, y + h)
                merged[-1] = (nx0, ny0, nx1 - nx0, ny1 - ny0)
                continue
        merged.append((x, y, w, h))
    return merged


def _math_glyph(mask: np.ndarray, box: tuple[int, int, int, int],
                pad: int = 6, scale: int = 4) -> bytes:
    """把单个字符块裁出来，做成 ddddocr 喜欢的「白底黑字 + 四周留白」。"""
    x, y, w, h = box
    sub = (1 - mask[y:y + h, x:x + w]) * 255
    sub = cv2.copyMakeBorder(sub, pad, pad, pad, pad,
                             cv2.BORDER_CONSTANT, value=255)
    sub = cv2.resize(sub, (sub.shape[1] * scale, sub.shape[0] * scale),
                     interpolation=cv2.INTER_LANCZOS4)
    return _encode(sub)


def recognize_math(
    img_bytes: bytes,
    *,
    min_votes: int = 2,
    min_block_h: int = 18,
    dist_thresholds: tuple[int, ...] = _MATH_DIST_THRESHOLDS,
) -> tuple[str, dict[str, str]]:
    """
    识别算式验证码并**算出答案**。

    返回 (answer, detail)
        answer : 答案字符串（如 '7'）；无法可靠求解时为空字符串
        detail : 每个预处理变体的「识别文本 → 答案」，只用于排障

    为什么要 min_votes：
        单次识别会错（`1`→`7`、`乘`→'和云弈' 之类）。让多个预处理 ×
        两个模型各自独立给出答案再投票，一致票数不足就放弃这一张。
        放弃比猜错好 —— 猜错要付一次登录尝试的代价，还可能触发门户风控。

    已知局限（实测 30 张真实样本）：
        这套启发式能解出约 1/3 的图，所以必须靠 `Authenticator` 的多轮
        重试兜底（max_retry=8 时整体成功率约 95%），或者改用人工输入。
        要稳定 90%+ 得靠专用模型，见 docs/design/08-验证码改版应对.md。

    幂等性：纯函数，不写文件、不缓存图片（验证码图属敏感物料，禁落盘）。
    """
    detail: dict[str, str] = {}
    std = get_math_ocr(False)
    try:
        beta = get_math_ocr(True)
    except Exception:            # beta 模型缺失时降级，不影响主流程
        beta = None

    variants: list[tuple[str, int, int]] = []
    for d in dist_thresholds:
        variants.append((f"bin{d}", d, 0))
    variants.append((f"bin{dist_thresholds[1]}blur", dist_thresholds[1], 3))

    votes: dict[str, list[str]] = {}     # 答案 -> 贡献它的变体名
    for name, d, blur in variants:
        mask = _math_foreground(img_bytes, d, blur)
        if mask is None:
            detail[name] = "<decode-failed>"
            continue
        boxes = _math_blocks(mask, min_h=min_block_h)
        if not boxes:
            detail[name] = "<no-block>"
            continue

        texts: list[str] = []
        for box in boxes:
            glyph = _math_glyph(mask, box)
            for eng in (std, beta):
                if eng is None:
                    continue
                try:
                    t = (eng.classification(glyph) or "").strip()
                except Exception:
                    continue
                if t:
                    texts.append(t)
                    break            # 单字块取第一个能出结果的模型即可

        joined = "".join(texts)
        # 第二个操作数恒为 1 位；尾部的 `=?` 常被认成数字，必须截断
        answer = captcha_math.solve(joined, max_b_digits=1)
        detail[name] = f"{joined} -> {answer}" if joined else "<empty>"
        if answer is not None:
            votes.setdefault(answer, []).append(name)

    if not votes:
        return "", detail

    best_answer, who = max(votes.items(), key=lambda kv: len(kv[1]))
    if len(who) < min_votes:
        # 票数不足：当作没认出来，让上层换图，不要赌
        detail["_rejected"] = f"{best_answer} (only {len(who)} vote)"
        return "", detail

    detail["_answer"] = best_answer
    return best_answer, detail
