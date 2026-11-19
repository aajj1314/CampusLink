# -*- coding: utf-8 -*-
"""
验证码识别 —— 字符型门户（text 模式）的识别链路。

迁移时保留的东西（都是上一版实测换来的）：
  · 四种预处理策略全部保留。默认只用 raw+gray，因为对
    桂林信息科技学院这个门户，实测 20 张真实样本 raw 对了 18 张，
    而 color/denoise 几乎全错（会输出「产」「了」「;」这类字符）。
    但换一个学校结论可能相反，所以策略本身不能删。
  · 投票时把非字母数字的结果剔除（CHAR_RE 校验）。
  · 投票按小写归并 —— ddddocr 对大小写区分很弱，
    同一张图不同策略给出 '945e' 和 '945E' 其实是同一个答案。
  · 不设置 set_ranges：实测 ddddocr 1.6.1 上它是负优化。

## 2026-11-19 重构：去掉 OpenCV 依赖

原来四个预处理函数全走 cv2（`cvtColor` / `adaptiveThreshold` /
`medianBlur` / `morphologyEx`），为了一个 `prep_gray` 拖进来 **82 MB 的 cv2.pyd**。

实测发现：
  · 本门户是**算式型**，登录走 `crnn_math.py`（CRNN 模型），**完全不碰 cv2**
  · 注销走 `recognize_with_confidence()`，直接把原始字节给 ddddocr，
    ddddocr 内部只 import cv2 但**一个函数都没调用**
  · 我们自己的 cv2 调用只剩 `prep_gray`（默认策略里唯一的一个）

所以用 PIL + numpy 重写了三个滤波器，**逐像素与 cv2 完全一致**
（实测 3 张真实样本，差异 0 像素）：

| cv2 | 替换实现 | 像素差异 |
|---|---|---|
| `cvtColor(BGR2GRAY)` | `Image.convert("L")` | 0（ITU-R 601-2 同权重）|
| `adaptiveThreshold(MEAN_C, 11, 2)` | 积分图 box-mean | 0 |
| `medianBlur(3)` | `np.median` 3×3，`mode="edge"` | 0（边界必须 replicate）|
| `morphologyEx(OPEN, 2×2)` | erode→dilate，**anchor=(1,1)** | 0（偶数核的锚点是右下）|

两个踩过的坑（这两处弄错就会静默改变识别结果）：
  · `medianBlur` 的边界是 **replicate**，不是 reflect/constant
  · `getStructuringElement(MORPH_RECT,(2,2))` 是**偶数**核，
    cv2 默认 anchor = (kw//2, kh//2) = **(1,1)**，不是左上

平台无关：只依赖 numpy / Pillow / ddddocr（三者都有 ARM 版本）。
2.0 起 ddddocr 为**可选依赖**：算式门户已改用专用模型
（core/crnn_math.py），本模块只剩字符型门户的识别链路；缺失时
上游会给出友好报错而不是崩溃。
为将来移植到 OpenWrt 留了余地（那边实现一个同样签名的 recognize() 即可）。
"""
from __future__ import annotations

import io
import re
import threading
from typing import Callable

import numpy as np
from PIL import Image


# 只允许字母数字进入投票 —— 排除掉模型幻觉出来的汉字和符号
CHAR_RE = re.compile(r"^[0-9A-Za-z]+$")


# ------------------------------------------------------------------ 图像基元
def _decode_gray(img_bytes: bytes) -> np.ndarray | None:
    """
    解码成灰度 ndarray（uint8，形状 H×W）。

    为什么用灰度起步：原来 cv2 的 `_decode` 是 IMREAD_COLOR（BGR），
    但四个预处理里只有 prep_color 需要彩色（走 HSV 取饱和度），
    其余都是先转灰度。这里拆成「灰度解码」+「彩色解码」两个入口，
    避免每次多一次 BGR→GRAY。
    """
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("L")
    except Exception:
        return None
    return np.asarray(img, dtype=np.uint8)


def _decode_rgb(img_bytes: bytes) -> np.ndarray | None:
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return None
    return np.asarray(img, dtype=np.uint8)


def _encode(arr: np.ndarray) -> bytes:
    """ndarray → PNG 字节。"""
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _adaptive_threshold_mean(src: np.ndarray, block: int = 11,
                             c: int = 2, maxval: int = 255) -> np.ndarray:
    """
    等价于 cv2.adaptiveThreshold(..., ADAPTIVE_THRESH_MEAN_C,
                                THRESH_BINARY, block, c)。

    用积分图算 block 窗口均值（O(1) 每像素），边界 replicate。

    ⚠️ 两个必须照抄的细节（弄错就会在少数像素上出错）：
      1. **边界是 replicate**（不是 reflect / constant）
      2. **均值要四舍五入取整** —— cv2 内部用 `boxFilter(ddepth=-1)`，
         输出深度跟输入一致（8U），均值被截断成整数。
         不取整时，输入含大量 0 的区域（比如饱和度图）会在
         `0 > mean-c` 这个边界条件下判错（实测 13~30 像素）。

    实测 3 张真实样本，与 cv2 差异 0 像素。
    """
    srcf = src.astype(np.float32)
    pad = block // 2
    padded = np.pad(srcf, pad, mode="edge")

    # 积分图：ii[y, x] = sum(padded[:y, :x])
    ii = padded.cumsum(0).cumsum(1)
    ii = np.pad(ii, ((1, 0), (1, 0)), mode="constant")

    h, w = src.shape
    win = (ii[block:block + h, block:block + w]
           - ii[0:h, block:block + w]
           - ii[block:block + h, 0:w]
           + ii[0:h, 0:w])
    # 关键：四舍五入（模拟 8U 输出的截断）
    mean = np.round(win / float(block * block))

    return np.where(srcf > mean - c, maxval, 0).astype(np.uint8)


def _median_blur(src: np.ndarray, ksize: int = 3) -> np.ndarray:
    """
    等价于 cv2.medianBlur(src, ksize)。

    ⚠️ 边界必须用 mode="edge"（= cv2 的 BORDER_REPLICATE）。
       换成 reflect / constant 都会在边缘产生差异（实测 22~29 像素）。
    """
    pad = ksize // 2
    p = np.pad(src, pad, mode="edge")
    h, w = src.shape
    stack = np.stack([p[dy:dy + h, dx:dx + w]
                      for dy in range(ksize) for dx in range(ksize)], axis=0)
    return np.median(stack, axis=0).astype(np.uint8)


def _morph_open(src: np.ndarray, kw: int = 2, kh: int = 2) -> np.ndarray:
    """
    等价于 cv2.morphologyEx(src, MORPH_OPEN,
                          getStructuringElement(MORPH_RECT, (kw, kh)))。
    OPEN = erode → dilate。

    ⚠️ 偶数尺寸核的锚点：cv2 默认 anchor=(-1,-1) 表示「核中心」，
       偶数核时中心 = (kh//2, kw//2) = **(1,1)**，不是 (0,0)。
       锚点写错会让输出整体偏移 1 像素（实测 759~821 像素不同）。
    """
    ay, ax = kh // 2, kw // 2
    h, w = src.shape

    def _filter(img: np.ndarray, fill: int, reducer) -> np.ndarray:
        p = np.pad(img, ((ay, kh - 1 - ay), (ax, kw - 1 - ax)),
                   mode="constant", constant_values=fill)
        return reducer(
            np.stack([p[dy:dy + h, dx:dx + w]
                      for dy in range(kh) for dx in range(kw)], axis=0),
            axis=0)

    eroded = _filter(src, 255, np.min)     # 补 255 不改变 erode 结果
    return _filter(eroded, 0, np.max).astype(np.uint8)   # 补 0 不改变 dilate


def _rgb_to_hsv_saturation(rgb: np.ndarray) -> np.ndarray:
    """
    RGB → HSV 的 S 通道，用于 prep_color 的饱和度分离。

    S = (max - min) / max * 255（max == 0 时 S = 0）

    ⚠️ 与 cv2 的差异：**最多 1 个量化级**（不是 0）。

    cv2 的 `cvtColor(RGB2HSV)` 是整型定点实现，在 S 的舍入上
    有自己的规则。实测 20000 个随机像素：

        本办法（整数四舍五入）：152 个差 1
        浮点截断：            9851 个差 1
        PIL convert("HSV")：  9851 个差 1
        cv2 官方定点公式：     无法完全复现（SIMD 路径）

    所以这里用「整数四舍五入」把差异压到最小（152/20000 = 0.76%，
    最大差 1）。对最终识别没有影响 —— S 差 1 不会改变
    adaptiveThreshold 的二值化结果（实测 denoise/color 链路
    在真实验证码上仍是 0 差异，见 tests/test_ocr_preprocess.py）。
    """
    rgb_i = rgb.astype(np.int32)
    mx = rgb_i.max(axis=2)
    mn = rgb_i.min(axis=2)
    diff = mx - mn

    sat = np.zeros(mx.shape, dtype=np.int32)
    nz = mx > 0
    # 整数四舍五入：(diff * 255 + mx/2) / mx
    sat[nz] = (diff[nz] * 255 + mx[nz] // 2) // mx[nz]
    return np.clip(sat, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ 预处理
def prep_raw(img_bytes: bytes) -> bytes:
    """原图直接送去识别。别小看它，多数验证码上它就是最准的。"""
    return img_bytes


def prep_gray(img_bytes: bytes) -> bytes:
    """
    灰度。和 raw 表现接近，常能作为第二个投票者。

    实测与 cv2 的 cvtColor(BGR2GRAY) **逐像素相同**
    （两者都用 ITU-R 601-2：Y = 0.299R + 0.587G + 0.114B）。
    """
    arr = _decode_gray(img_bytes)
    if arr is None:
        return img_bytes
    return _encode(arr)


def prep_denoise(img_bytes: bytes) -> bytes:
    """
    灰度 + 自适应阈值 + 中值滤波。

    适合作笔画较细、背景干净的验证码；对本项目这个门户反而有害
    （会打断笔画导致少认字符），所以默认不启用。

    三个步骤分别对应 cv2 的 cvtColor / adaptiveThreshold(MEAN_C,11,2)
    / medianBlur(3)，逐像素等价（见模块头的对照表）。
    """
    arr = _decode_gray(img_bytes)
    if arr is None:
        return img_bytes
    binary = _adaptive_threshold_mean(arr, block=11, c=2)
    return _encode(_median_blur(binary, 3))


def prep_color(img_bytes: bytes) -> bytes:
    """
    饱和度分离 + 形态学开运算。

    思路：彩色笔画饱和度高、浅色背景饱和度低，所以走 HSV 的 S 通道能
    把字符和背景分开；再做开运算，1px 的彩色干扰线会被整条削掉，
    而粗笔画字符只是略微变瘦。

    这条链路对「粗字 + 细线」的验证码很强（最初的参考图上它是唯一读对的），
    但对笔画偏细的验证码会破坏字形。要不要用，靠真实样本实测决定。
    """
    rgb = _decode_rgb(img_bytes)
    if rgb is None:
        return img_bytes
    sat = _rgb_to_hsv_saturation(rgb)
    binary = _adaptive_threshold_mean(sat, block=11, c=2)
    cleaned = _morph_open(binary, kw=2, kh=2)
    return _encode(255 - cleaned)      # bitwise_not


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


def recognize_with_confidence(
    img_bytes: bytes,
    beta: bool = False,
) -> tuple[str, float, dict]:
    """
    单次识别，返回真实置信度（2026-11-18 新增，给免验证码注销用）。

    与 recognize() 的差异：

      1. **不做多策略投票**。注销页的 /CheckCode 每次请求都是新图
         （实测 30 张 md5 无一重复），拿不准换一张就行，不需要投票。
      2. **用 ddddocr 的 probability 输出**。它内部对每个字符位置给出
         概率分布，confidence 是最弱字符的概率 —— 这才是真实置信度，
         比 recognize()+confidence() 的「策略一致率」准确得多。

    实测（注销验证码 80×24）：
        probability 模式返回 {'text': 'dl2m', 'confidence': 0.997, ...}
        conf < 0.90 的样本极少，且基本都是真的认错了

    降级：ddddocr 版本不支持 probability 参数时（TypeError），
          退回普通模式，confidence 返回 1.0（视为可信，不阻塞流程）。

    返回 (text, confidence, detail)
        text       : 识别结果；空串表示没认出来
        confidence : 0.0~1.0；降级时为 1.0
        detail     : 诊断信息（不含敏感内容）
    """
    ocr = get_ocr(beta)

    try:
        result = ocr.classification(img_bytes, probability=True)
    except TypeError:
        # ddddocr < 1.5 没有 probability 参数
        text = (ocr.classification(img_bytes) or "").strip()
        return text, 1.0, {"_mode": "legacy", "text": text}
    except Exception as exc:
        return "", 0.0, {"_error": f"{type(exc).__name__}: {exc}"}

    if isinstance(result, dict):
        text = (result.get("text") or "").strip()
        try:
            conf = float(result.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        detail = {"_mode": "probability", "text": text,
                  "confidence": round(conf, 4)}
        return text, conf, detail

    # 某些版本即使传 probability 也只返回 str
    text = str(result or "").strip()
    return text, 1.0 if text else 0.0, {"_mode": "str", "text": text}




# ------------------------------------------------------------------
# 说明：算式验证码的传统启发式识别（recognize_math 及其辅助函数）已于
# 2.0 移除 —— CRNN 专用模型（core/crnn_math.py）全面替代后，该引擎
# 13% 正确 / 17% 错答的期望收益为负。模型弃权（识别不出）时上层直接
# 换图重试，比交给低质引擎更安全。历史实现见 git 历史（<= f08d403）。
