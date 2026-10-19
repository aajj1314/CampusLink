# -*- mode: python ; coding: utf-8 -*-
r"""
CampusLinkCLI 打包规格 —— 无人值守认证（headless）。

构建：
    pyinstaller packaging/CampusLinkCLI.spec --noconfirm --clean --distpath dist-cli --workpath build-cli

与 GUI 版（CampusLink.spec）的关键差异：

1. **console=True + 安装器生成 silent_run.vbs。**
   手动双击桌面图标：控制台直接显示进度与结果。
   开机静默：Startup 快捷方式指向 wscript.exe + silent_run.vbs，
   vbs 以隐藏窗口（Run 0）启动 exe，黑框完全不可见。
   **不要改回 windowed + 程序内 AllocConsole**：标准流重定向在
   windowed 下是坑窝（TextIOWrapper 参数 / CONIN$ 交互 / 编码），
   已实际踩崩一次（2026-10-19）。

2. **excludes 掉 PySide6 / keyring / cryptography / ddddocr / cv2。**
   CLI 不用 Qt（-96MB）；密码明文存 json 不用凭据管理器（-16MB）；
   算式识别走专用模型不用 ddddocr/cv2（早已剥离）。

3. **模型随包分发**：ml/models/crnn_math.onnx 存在才打（静默场景
   必须全自动识别，没有模型 = 无法工作）。
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent

APP_NAME = "CampusLinkCLI"
ENTRY = str(ROOT / "packaging" / "entry_cli.py")
ICON = str(ROOT / "campuslink" / "gui" / "assets" / "app.ico")

# ------------------------------------------------------------------ 数据文件
datas = [
    # 内置门户档案
    (str(ROOT / "campuslink" / "profiles"), "campuslink/profiles"),
]

# 模型随包分发（静默场景必须有自动识别）
_crnn_model = ROOT / "ml" / "models" / "crnn_math.onnx"
if _crnn_model.is_file():
    datas.append((str(_crnn_model), "ml/models"))
    print(f"[spec] 已包含算式模型 {_crnn_model} "
          f"({_crnn_model.stat().st_size // 1024 // 1024} MiB)")
else:
    print("[spec] 未找到 ml/models/crnn_math.onnx -> CLI 将无法自动认证！")

binaries = []
hiddenimports = ["campuslink.cli_headless"]
_missing = []
for pkg in ("onnxruntime",):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:
        _missing.append(f"{pkg}: {exc}")
if _missing:
    print("[spec] collect_all 失败:", "; ".join(_missing))

hiddenimports += [
    "campuslink.core.ocr",
    "campuslink.core.ocr_backend",
    "campuslink.core.captcha_math",
    "campuslink.core.crnn_math",
]

excludes = [
    # ---- GUI（CLI 完全不用，也是体积大头）----
    "PySide6", "shiboken6",
    # ---- CLI 用明文 json，不需要凭据管理器 ----
    "keyring", "cryptography",
    # ---- 字符型验证码的可选依赖（CLI 面向算式门户）----
    "ddddocr", "cv2", "cv2.*",
    # ---- 其它用不到的大件 ----
    "tkinter", "unittest", "pydoc", "doctest", "test",
    "matplotlib", "scipy", "pandas", "IPython", "PIL.ImageQt",
    "setuptools", "pip",
]

a = Analysis(
    [ENTRY],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # UPX 压缩过的 exe 更容易被杀软误报
    console=True,               # 原生控制台：手动模式显示进度结果；
                                # 静默模式由 silent_run.vbs 以隐藏窗口启动，
                                # 黑框不可见（勿改回 windowed+AllocConsole）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
    version=str(ROOT / "packaging" / "version_info.txt")
    if (ROOT / "packaging" / "version_info.txt").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
