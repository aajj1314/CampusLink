# -*- mode: python ; coding: utf-8 -*-
r"""
PyInstaller 打包规格。

构建用法（一般不用直接调，走 packaging/build.ps1）：
    pyinstaller packaging/CampusLink.spec --noconfirm --clean

几个关键决定：

1. **onedir 而不是 onefile。**
   onefile 每次运行都要把几百 MB 解压到 %TEMP%，启动要十几秒。
   本项目是常驻托盘的程序，onedir 启动不到 2 秒，明显更合适。

2. **必须 --collect-all onnxruntime / keyring。**
   onnxruntime 的原生 DLL 是 data 文件，PyInstaller 的静态分析看不见，
   不收集的话编译能过、运行时才报 "No such file or directory: common.onnx"。
   （2.0 起 ddddocr / cv2 已剥离，见 excludes 段的说明。）

3. **排除用不到的 Qt 模块。**
   PySide6 完整安装有 650 MB（含 WebEngine/Chromium）。我们只用
   QtCore/QtGui/QtWidgets/QtNetwork，排除掉其余的能让包小一大截。

4. **console=False。** GUI 程序，不能弹黑框。
   副作用是没有 stdout —— 代码里所有 print 都已改成 logging，
   日志写文件，见 campuslink/logging_setup.py。
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# 项目根目录（本文件在 packaging/ 下）
ROOT = Path(SPECPATH).resolve().parent

APP_NAME = "CampusLink"
ENTRY = str(ROOT / "packaging" / "entry_gui.py")
ICON = str(ROOT / "campuslink" / "gui" / "assets" / "app.ico")

# ------------------------------------------------------------------ 数据文件
datas = [
    # 内置门户档案：换学校靠它，必须打进去
    (str(ROOT / "campuslink" / "profiles"), "campuslink/profiles"),
    # 图标（托盘/窗口图标在代码里是画出来的，这里是为了「关于」等场景备用）
    (str(ROOT / "campuslink" / "gui" / "assets"), "campuslink/gui/assets"),
]

# 2.0：算式验证码专用模型（ml/models/crnn_math.onnx，训练工程在 ml/ 下）。
# 存在才打进去；不存在时构建照样过，运行时静默降级到启发式引擎。
# 放 `ml/models/` 是为了让 crnn_math.find_model_path() 从 _MEIPASS 往上找时命中。
_crnn_model = ROOT / "ml" / "models" / "crnn_math.onnx"
if _crnn_model.is_file():
    datas.append((str(_crnn_model), "ml/models"))
    print(f"[spec] 已包含算式模型 {_crnn_model} ({_crnn_model.stat().st_size // 1024 // 1024} MiB)")
else:
    print("[spec] 未找到 ml/models/crnn_math.onnx -> 产物将只走启发式引擎（1.x 行为）")

# ------------------------------------------------------------------ 隐藏导入
hiddenimports = [
    "campuslink",
    "campuslink.core",
    "campuslink.core.ocr",
    "campuslink.core.ocr_backend",
    "campuslink.core.captcha_math",
    "campuslink.core.crnn_math",     # 2.0：算式专用模型推理侧（ocr_backend 内延迟导入）
    "campuslink.core.portal",
    "campuslink.core.authenticator",
    "campuslink.core.netdetect",
    "campuslink.config",
    "campuslink.secrets",
    "campuslink.profiles",
    "campuslink.autostart",
    "campuslink.paths",
    "campuslink.logging_setup",
    # 视觉节点（Agent 2）：client/node/watcher 由 main_window 静态 import，
    # 但显式列出更稳（watcher 里的动态 import 等边缘路径不会被漏掉）
    "campuslink.visionnode",
    "campuslink.visionnode.client",
    "campuslink.visionnode.node",
    "campuslink.visionnode.watcher",
    # 动态 import 的模块，静态分析看不到
    "PySide6.QtNetwork",        # QLocalServer / QLocalSocket（单实例）
    "keyring.backends.Windows",  # 凭据管理器后端，运行时才按平台选择
    "keyring.backends.SecretService",
    "keyring.backends.fail",
    "bs4",
    "lxml",
    "lxml.etree",
]

# ------------------------------------------------------------------ 排除项
excludes = [
    # ---- 用不到的 Qt 模块（这是体积大头）----
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D", "PySide6.QtQuickControls2",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras", "PySide6.Qt3DLogic",
    "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtSvgWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtStateMachine", "PySide6.QtTextToSpeech",
    "PySide6.QtSpatialAudio", "PySide6.QtGraphs",
    # ---- 其它用不到的大件 ----
    "tkinter", "unittest", "pydoc", "doctest", "test",
    "matplotlib", "scipy", "pandas", "IPython", "PIL.ImageQt",
    "setuptools", "pip",
    # ---- 2.0 剥离的验证码识别依赖（算式门户由专用模型识别）----
    # core/ocr.py 里还有函数内的延迟 import，静态分析会试图收集，
    # 这里硬排除；字符型门户用户需自行安装 ddddocr + opencv-python
    "ddddocr", "cv2", "cv2.*",
]

# ---- 收集带 data 文件 / 原生库的包 ----
# 2.0：ddddocr / cv2 已剥离（算式门户由专用模型识别，见 core/crnn_math.py），
# 产物体积明显下降；两者仍被 core/ocr.py 的字符型链路延迟引用，
# 用 excludes 硬排除，防止静态分析把它们拽回来。
binaries = []
_missing = []
for pkg in ("onnxruntime", "keyring"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:                      # 单个包失败不能把整个构建搞挂
        _missing.append(f"{pkg}: {exc}")

if _missing:
    for m in _missing:
        print(f"[spec] WARNING collect_all 失败 -> {m}")

print(f"[spec] datas={len(datas)} binaries={len(binaries)} "
      f"hiddenimports={len(hiddenimports)} excludes={len(excludes)}")

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
    upx=False,                  # UPX 压缩过的 exe 更容易被杀软误报，得不偿失
    console=False,              # GUI 程序，不要黑框
    disable_windowed_traceback=False,   # 崩溃时给出可读堆栈
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
