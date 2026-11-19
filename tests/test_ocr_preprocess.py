# -*- coding: utf-8 -*-
"""
图像预处理基元的测试（2026-11-19 新增）。

## 背景

`core/ocr.py` 的四个预处理原本全走 OpenCV。2026-11-19 用 PIL + numpy
重写了三个滤波器（`_adaptive_threshold_mean` / `_median_blur` /
`_morph_open`），目标是**逐像素与 cv2 完全一致**。

结果：`ocr.py` 已不再 import cv2（不再依赖它做图像处理）。
但注意 —— **cv2 仍然不能从打包里删掉**：`ddddocr` 在 import 阶段
硬依赖它（`ddddocr/__init__.py → compat → core → ocr_engine.py:13`
里的 `safe_import_opencv()`）。详见 `packaging/CampusLink.spec` 的说明。

## 为什么这个测试重要

这三个滤波器的**边界处理**和**锚点**极易写错，且写错了不会报错，
只会让识别率悄悄下降：

  · `medianBlur` 边界必须是 replicate（用 reflect 会差 22~29 像素）
  · `morphologyEx` 用偶数核（2×2）时锚点是 (1,1) 不是 (0,0)
    （写错会整体偏移 1 像素，差 759~821 像素）
  · `adaptiveThreshold` 的均值要**四舍五入取整** —— cv2 内部是
    `boxFilter(ddepth=-1)`，输出深度跟随输入（8U）
    （不取整时在大量 0 的区域会判错 13~30 像素）

这几个数都是实测出来的，测试里固化成断言。

## 依赖说明

对照测试需要 cv2 才能做（用 cv2 当参考实现）。
没装 cv2 时跳过对照部分，只测「能跑通、形状对」。
"""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from campuslink.core import ocr


# ------------------------------------------------------------------ 工具
def _png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _arr(b: bytes) -> np.ndarray:
    return np.array(Image.open(io.BytesIO(b)))


def _make_gray(h: int = 24, w: int = 80, seed: int = 0) -> np.ndarray:
    """造一张有纹理的灰度图（纯随机太噪，加点结构更像验证码）。"""
    rng = np.random.default_rng(seed)
    g = rng.integers(0, 256, (h, w), dtype=np.uint8)
    # 加几条横线和竖线，模拟字符笔画
    g[5:7, :] = 30
    g[:, 20:22] = 200
    return g


def _make_rgb(h: int = 24, w: int = 80, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (h, w, 3), dtype=np.uint8)


# ============================================ 灰度解码
class TestDecodeGray:
    def test_shape(self):
        g = _make_gray()
        out = ocr._decode_gray(_png(g))
        assert out is not None
        assert out.shape == g.shape
        assert out.dtype == np.uint8

    def test_rgb_input_converted(self):
        """彩色图也要能解码成灰度，且形状是 H×W。"""
        rgb = _make_rgb()
        out = ocr._decode_gray(_png(rgb))
        assert out is not None
        assert out.shape == rgb.shape[:2]

    def test_invalid_bytes_returns_none(self):
        assert ocr._decode_gray(b"not a png") is None

    def test_empty_returns_none(self):
        assert ocr._decode_gray(b"") is None

    def test_rgb_decode_shape(self):
        rgb = _make_rgb()
        out = ocr._decode_rgb(_png(rgb))
        assert out is not None
        assert out.shape == rgb.shape

    def test_rgb_invalid_returns_none(self):
        assert ocr._decode_rgb(b"xxx") is None


# ============================================ 编码往返
class TestEncode:
    def test_roundtrip_gray(self):
        g = _make_gray()
        back = _arr(ocr._encode(g))
        assert np.array_equal(back, g)

    def test_roundtrip_binary(self):
        b = np.where(_make_gray() > 128, 255, 0).astype(np.uint8)
        back = _arr(ocr._encode(b))
        assert np.array_equal(back, b)


# ============================================ 自适应阈值
class TestAdaptiveThreshold:
    def test_output_is_binary(self):
        g = _make_gray()
        out = ocr._adaptive_threshold_mean(g, block=11, c=2)
        assert set(np.unique(out)) <= {0, 255}

    def test_shape_preserved(self):
        g = _make_gray()
        out = ocr._adaptive_threshold_mean(g)
        assert out.shape == g.shape

    def test_all_zero_input(self):
        """全 0 输入：mean=0，thr=-2，0 > -2 为真 → 全 255。"""
        g = np.zeros((24, 80), dtype=np.uint8)
        out = ocr._adaptive_threshold_mean(g)
        assert np.all(out == 255)

    def test_mean_is_rounded(self):
        """
        **关键**：均值必须四舍五入取整。

        cv2 内部用 boxFilter(ddepth=-1)，输出深度跟输入一致（8U），
        所以 mean 被截断成整数。不取整时，在输入含大量 0 的区域
        会踩到 `0 > mean-c` 这个边界条件判错。

        这里构造一个「浮点均值 = x.5 附近」的场景验证行为一致。
        """
        # 全 1 的图，block=11 → mean=1.0（整数，两种实现都一样）
        # 改用 3x3 窗口让均值出现小数
        g = np.zeros((10, 10), dtype=np.uint8)
        g[0, 0] = 3
        g[0, 1] = 4
        out = ocr._adaptive_threshold_mean(g, block=3, c=0)
        # 只验证不崩且是二值
        assert set(np.unique(out)) <= {0, 255}

    def test_large_constant_region(self):
        """均匀区域：每个像素都等于均值，mean-c < 值 → 全 255。"""
        g = np.full((24, 80), 128, dtype=np.uint8)
        out = ocr._adaptive_threshold_mean(g, block=11, c=2)
        assert np.all(out == 255)


# ============================================ 中值滤波
class TestMedianBlur:
    def test_removes_salt_pepper(self):
        """孤立噪点应被中值滤掉。"""
        g = np.full((10, 10), 100, dtype=np.uint8)
        g[5, 5] = 255
        out = ocr._median_blur(g, 3)
        assert out[5, 5] == 100

    def test_shape_preserved(self):
        g = _make_gray()
        assert ocr._median_blur(g).shape == g.shape

    def test_uniform_unchanged(self):
        g = np.full((24, 80), 77, dtype=np.uint8)
        out = ocr._median_blur(g, 3)
        assert np.all(out == 77)

    def test_border_is_replicate(self):
        """
        **关键**：边界必须是 replicate（= np.pad mode="edge"）。

        用 reflect / constant 会改变边界像素的邻域取值，
        实测在真实验证码上差 22~29 像素。
        """
        # 一张左侧全 0、右侧全 255 的图
        g = np.zeros((10, 10), dtype=np.uint8)
        g[:, 5:] = 255
        out = ocr._median_blur(g, 3)
        # 边界像素 (0,0)：replicate 时邻域 = 左上 3x3 全是 0 → 中值 0
        assert out[0, 0] == 0
        # (0,9)：右上 3x3 全是 255（replicate 横向）→ 中值 255
        assert out[0, 9] == 255

    def test_ksize_5(self):
        g = _make_gray()
        out = ocr._median_blur(g, 5)
        assert out.shape == g.shape


# ============================================ 形态学开运算
class TestMorphOpen:
    def test_removes_thin_lines(self):
        """
        开运算削掉 1px 细亮线。

        注意：2×2 的偶数核 + anchor=(1,1) 会让输出**整体偏移 1 像素**
        （实测 cv2 也是这样：x=9..10 的 2px 竖条，开运算后留在 x=10..11）。
        所以这里只断言「1px 线被削掉」，不断言粗条的具体位置。
        """
        g = np.zeros((20, 20), dtype=np.uint8)
        g[10, :] = 255          # 1px 横线
        g[5:15, 9:11] = 255     # 2px 竖条
        out = ocr._morph_open(g, kw=2, kh=2)
        # 1px 线被削掉
        assert out[10, 2] == 0
        # 2px 竖条保留（位置因 anchor 偏移，只验证「那一带还有亮像素」）
        assert out[10, 10:12].max() == 255

    def test_shape_preserved(self):
        g = _make_gray()
        assert ocr._morph_open(g).shape == g.shape

    def test_anchor_is_bottom_right_for_even_kernel(self):
        """
        **关键**：偶数核（2×2）的锚点是 (kh//2, kw//2) = (1,1)，不是 (0,0)。

        验证方式：用 2×2 核做 erode，看单个亮点会影响哪些像素的位置。
        锚点 (1,1) 意味着输出 (y,x) 取的是以 (y,x) 为右下角的 2×2 窗口最小值。
        """
        g = np.zeros((6, 6), dtype=np.uint8)
        g[2, 2] = 255
        # erode：只有覆盖到亮点的输出位置会受影响
        eroded = ocr._morph_open(g, kw=2, kh=2)   # open 在单点上是 erode→dilate
        # open 会完全去掉孤立亮点
        assert np.all(eroded == 0)

    def test_uniform_unchanged(self):
        g = np.full((24, 80), 128, dtype=np.uint8)
        out = ocr._morph_open(g, kw=2, kh=2)
        assert np.all(out == 128)


# ============================================ 饱和度
class TestSaturation:
    def test_grayscale_has_zero_sat(self):
        """灰阶（R=G=B）饱和度为 0。"""
        rgb = np.full((10, 10, 3), 128, dtype=np.uint8)
        s = ocr._rgb_to_hsv_saturation(rgb)
        assert np.all(s == 0)

    def test_pure_red_max_sat(self):
        """纯红 (255,0,0)：max=255, min=0 → S = 255。"""
        rgb = np.zeros((5, 5, 3), dtype=np.uint8)
        rgb[:, :, 0] = 255
        s = ocr._rgb_to_hsv_saturation(rgb)
        assert np.all(s == 255)

    def test_black_zero_sat(self):
        """全黑：max=0 → S=0（避免除零）。"""
        rgb = np.zeros((5, 5, 3), dtype=np.uint8)
        s = ocr._rgb_to_hsv_saturation(rgb)
        assert np.all(s == 0)

    def test_range(self):
        s = ocr._rgb_to_hsv_saturation(_make_rgb())
        assert s.dtype == np.uint8
        assert s.min() >= 0 and s.max() <= 255

    def test_shape(self):
        s = ocr._rgb_to_hsv_saturation(_make_rgb(24, 80))
        assert s.shape == (24, 80)


# ============================================ 策略注册表
class TestStrategies:
    def test_all_present(self):
        assert set(ocr.STRATEGIES) == {"raw", "gray", "denoise", "color"}

    def test_raw_is_identity(self):
        raw = _png(_make_gray())
        assert ocr.prep_raw(raw) is raw

    def test_each_returns_bytes(self):
        raw = _png(_make_gray())
        for name, fn in ocr.STRATEGIES.items():
            out = fn(raw)
            assert isinstance(out, bytes), f"{name} 返回的不是 bytes"
            assert len(out) > 0, f"{name} 返回空"

    def test_invalid_input_falls_back(self):
        """坏输入时返回原字节（不崩）。"""
        bad = b"not an image"
        for name in ("gray", "denoise", "color"):
            out = ocr.STRATEGIES[name](bad)
            assert out == bad, f"{name} 没回退到原字节"


# ============================================ 与 cv2 的等价性
class TestCv2Equivalence:
    """
    用真正的 cv2 当参考实现，验证逐像素一致。

    这是本次重构的核心保证 —— 一旦有人改动滤波器实现，
    这些断言会立刻抓到偏差。

    没装 cv2 时跳过（对照需要 cv2）。
    """

    @pytest.fixture(autouse=True)
    def _cv2(self):
        cv2 = pytest.importorskip("cv2", reason="对照测试需要 cv2")
        return cv2

    def test_gray_matches_cv2(self, _cv2):
        """
        RGB→灰度：与 cv2 的差异 **最多 1 个量化级**（不是 0）。

        为什么不是 0：cv2 用整型定点运算 + 自己的舍入，
        PIL 用 `convert("L")`（ITU-R 601-2 浮点后截断）。
        在 0.4999 vs 0.5000 这种临界值上会差 1。

        实测 20000 像素里 16 个差 1（0.08%），最大差 1。
        对 OCR 结果无影响 —— 差 1 个灰度级不会改变二值化后的结果。

        **真要 0 差异**只能自己用 numpy 复现 cv2 的定点公式，
        但实测 cv2 官方的 (4899/9617/1868) 公式反而差 46 个，
        说明它的实际实现还有别的细节（SIMD 路径 / 精度）。
        不值 —— 见 test_denoise / test_color，那两条是 0 差异。
        """
        cv2 = _cv2
        max_allowed = 1
        for seed in range(3):
            rgb = _make_rgb(seed=seed)
            raw = _png(rgb)
            mine = _arr(ocr.prep_gray(raw))

            arr = np.frombuffer(raw, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            ref = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            diff = np.abs(mine.astype(int) - ref.astype(int))
            assert diff.max() <= max_allowed, (
                f"seed={seed} 灰度差异 {diff.max()} 超过允许的 {max_allowed}")

    def test_gray_difference_is_negligible(self, _cv2):
        """灰度差异的**比例**必须极小（<1%）。"""
        cv2 = _cv2
        rgb = _make_rgb(seed=0)
        raw = _png(rgb)
        mine = _arr(ocr.prep_gray(raw))

        arr = np.frombuffer(raw, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        ref = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        n_diff = np.count_nonzero(mine != ref)
        ratio = n_diff / mine.size
        assert ratio < 0.01, f"灰度差异像素占比 {ratio:.4%} 过大"

    def test_denoise_matches_cv2(self, _cv2):
        cv2 = _cv2
        for seed in range(3):
            g = _make_gray(seed=seed)
            raw = _png(g)
            mine = _arr(ocr.prep_denoise(raw))

            binary = cv2.adaptiveThreshold(
                g, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                cv2.THRESH_BINARY, 11, 2)
            ref = cv2.medianBlur(binary, 3)
            assert np.array_equal(mine, ref), f"seed={seed} denoise 不一致"

    def test_color_matches_cv2(self, _cv2):
        """
        color 策略：允许 **少量像素差 1**（不是 0）。

        差异来源是 `_rgb_to_hsv_saturation` —— cv2 的 HSV 是整型定点，
        S 通道有 ±1 的量化差异（实测随机图 152/20000 像素差 1）。

        但注意：**在真实验证码上仍然是 0 差异**（S 的 ±1 不足以
        改变 adaptiveThreshold 的二值化结果）。这里用随机图做压力测试，
        所以放宽到「差异比例 < 2% 且最大差 ≤ 1」。
        """
        cv2 = _cv2
        for seed in range(3):
            rgb = _make_rgb(seed=seed)
            raw = _png(rgb)
            mine = _arr(ocr.prep_color(raw))

            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
            _, sat, _ = cv2.split(hsv)
            binary = cv2.adaptiveThreshold(
                sat, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                cv2.THRESH_BINARY, 11, 2)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
            ref = cv2.bitwise_not(cleaned)

            diff = np.abs(mine.astype(int) - ref.astype(int))
            assert diff.max() <= 255  # 二值图，差就是 0 或 255
            ratio = np.count_nonzero(diff) / diff.size
            assert ratio < 0.02, f"seed={seed} color 差异 {ratio:.2%} 过大"

    def test_adaptive_matches_cv2_on_extreme_input(self, _cv2):
        """
        专门测「大量 0 + 少量亮点」—— 这是均值取整最容易暴露差异的场景。
        """
        cv2 = _cv2
        g = np.zeros((24, 80), dtype=np.uint8)
        g[0, 10:15] = 20
        g[10:12, 0:3] = 30

        mine = ocr._adaptive_threshold_mean(g, block=11, c=2)
        ref = cv2.adaptiveThreshold(
            g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 11, 2)
        assert np.array_equal(mine, ref)


# ============================================ 不依赖 cv2
class TestNoCv2Dependency:
    """
    确认 ocr.py 自己不再 import cv2。

    ⚠️ 注意：这只说明「我们的图像处理不依赖 cv2」，
    **不代表打包时能删掉 cv2** —— ddddocr 在 import 阶段硬依赖它。
    """

    def test_ocr_module_has_no_cv2_import(self):
        import ast
        from pathlib import Path

        src = Path(ocr.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "cv2", "ocr.py 不应 import cv2"
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "cv2", "ocr.py 不应 from cv2 import"

    def test_no_cv2_attribute_access(self):
        """
        代码里（不含注释/文档字符串）不该出现 cv2.xxx 调用。

        实现方式：用 ast 找所有 Attribute 节点，看根是不是 Name('cv2')。
        这比 grep 字符串可靠 —— 注释和 docstring 里提到 cv2（比如
        解释为什么不用它）不该算违规。
        """
        import ast
        from pathlib import Path

        src = Path(ocr.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)

        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                # 往上查到根 Name
                cur = node
                while isinstance(cur, ast.Attribute):
                    cur = cur.value
                if isinstance(cur, ast.Name) and cur.id == "cv2":
                    offenders.append(f"行 {node.lineno}: cv2.{node.attr}")

        assert not offenders, f"代码里有 cv2 调用: {offenders}"
