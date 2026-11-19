# -*- mode: python ; coding: utf-8 -*-
r"""
PyInstaller 打包规格。

构建用法（一般不用直接调，走 packaging/build.ps1）：
    pyinstaller packaging/CampusLink.spec --noconfirm --clean

几个关键决定：

1. **onedir 而不是 onefile。**
   onefile 每次运行都要把几百 MB 解压到 %TEMP%，启动要十几秒。
   本项目是常驻托盘的程序，onedir 启动不到 2 秒，明显更合适。

2. **必须 --collect-all onnxruntime / keyring / pydivert。**
   onnxruntime 的原生 DLL 是 data 文件，PyInstaller 的静态分析看不见，
   不收集的话编译能过、运行时才报 "No such file or directory: common.onnx"。
   pydivert 同理：WinDivert.dll 和 WinDivert64.sys 是包数据，缺了共享助手
   会报「pydivert 不可用」。
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
    "campuslink.core.hotspot",       # 共享助手（TTL 修复）
    "campuslink.core.ua_mask",       # 2026-11-18：HTTP UA 归一（WinDivert）
    "campuslink.core.proc",          # run_quiet（windowed 子进程不能弹黑框）
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
    # 2026-11-18：注销验证码走 ddddocr，必须显式列出
    # （core/ocr.py 里是函数内延迟 import，静态分析看不全）
    "ddddocr",
    "cv2",
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
    # ---- 以下 2.0 曾剥离，2026-11-18 起恢复 ----
    # ddddocr / cv2 现在**必须保留**：
    #   注销页的验证码(/CheckCode，80×24 的 4 位字符) 走的是 ddddocr，
    #   不是算式模型。剥离掉的话注销功能一到运行时就会报
    #   「字符识别引擎不可用」。
    # 代价是产物大 ~40MB（ddddocr 自带 onnxruntime 模型 + cv2）。
    # 登录侧的算式验证码仍走 CRNN 专用模型，不受影响。
]

# ---- 收集带 data 文件 / 原生库的包 ----
# 2026-11-18：ddddocr / cv2 重新纳入收集 —— 注销功能的验证码走它们。
# ddddocr 自带 onnx 模型（约 20MB），cv2 有原生 DLL，必须 collect_all，
# 否则编译能过、运行时才报模型或 DLL 找不到。
binaries = []
_missing = []
for pkg in ("onnxruntime", "keyring", "pydivert", "ddddocr", "cv2"):
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

# ------------------------------------------------------------------ 体积裁剪
# 2026-11-19：逐项实测过才加的，每条都注明依据和验证方法。
#
# 为什么不在 Analysis 阶段用 excludes 解决：
#   这些是**数据文件 / 原生 DLL**，不是 Python 模块，excludes 管不到。
#   只能从 collect_all 的结果里筛掉。
#
# ⚠️ 加新条目时必须先验证「删掉后目标功能仍正常」，
#    并把验证方法写在这里 —— 这类裁剪错了不会编译报错，只会运行时失效。
#
# 各条的实测依据见下方 _PRUNE_BY_NAME 的注释。

# COLLECT 之前把命中的条目从 datas/binaries 里剔除。
#
# ⚠️ 路径归一化踩过坑（2026-11-19）：
#   collect_all 返回的条目，第二个元素（dest dir）**可能是空字符串**，
#   这时 `join(dest, basename)` 只剩文件名，
#   用 "cv2/opencv_videoio_ffmpeg500_64.dll" 去 endswith 永远匹配不上。
#   结果：第一次构建只裁掉了 datas 里的两个 onnx，
#   binaries 里的 ffmpeg / opengl32sw 全漏了（少省 49 MB）。
#
#   修法：**只用 basename 匹配**（这些文件名足够独特），
#   同时从 src 的真实路径里推出包名做二次确认。
import os.path as _p


def _entry_basename(entry) -> str:
    return _p.basename(entry[0]).lower()


def _entry_pkg(entry) -> str:
    """
    从 src 路径推包名：.../site-packages/<pkg>/... 里的 <pkg>。
    推不出来时返回空串。
    """
    parts = entry[0].replace("\\", "/").split("/")
    for i, seg in enumerate(parts):
        if seg.lower() in ("site-packages", "dist-packages"):
            if i + 1 < len(parts):
                return parts[i + 1].lower()
    return ""


# 键改成 basename（唯一性足够），值里写明所属包便于核对
_PRUNE_BY_NAME = {
    "common.onnx": ("ddddocr", "51.58 MB；beta=False 不加载它（实测删掉无影响）"),
    "common_det.onnx": ("ddddocr", "19.20 MB；只有 det=True 才加载，我们从不传"),
    "opencv_videoio_ffmpeg500_64.dll": ("cv2", "29.45 MB；不做视频 IO"),
    "opengl32sw.dll": ("pyside6", "19.68 MB；QWidget 不需要 OpenGL"),
}


def _should_prune(entry) -> str | None:
    name = _entry_basename(entry)
    hit = _PRUNE_BY_NAME.get(name)
    if hit is None:
        return None
    expect_pkg, why = hit
    actual_pkg = _entry_pkg(entry)
    # 包名能对上才删（防止同名文件误伤别的库）
    if actual_pkg and actual_pkg != expect_pkg:
        return None
    return f"{name} [{actual_pkg or '?'}]  ({why})"


_pruned = []
_seen_src = set()
_new_datas = []
for e in datas:
    hit = _should_prune(e)
    if hit:
        if e[0] not in _seen_src:
            _seen_src.add(e[0])
            _pruned.append((hit, e[0]))
        print(f"[spec] 裁剪 datas   -> {hit}")
    else:
        _new_datas.append(e)

_new_binaries = []
for e in binaries:
    hit = _should_prune(e)
    if hit:
        if e[0] not in _seen_src:
            _seen_src.add(e[0])
            _pruned.append((hit, e[0]))
        print(f"[spec] 裁剪 binaries-> {hit}")
    else:
        _new_binaries.append(e)

datas = _new_datas
binaries = _new_binaries

_pruned_bytes = 0
for _hit, _src in _pruned:
    try:
        _pruned_bytes += _p.getsize(_src)
    except Exception:
        pass
print(f"[spec] 裁剪 {len(_pruned)} 个文件，合计 {_pruned_bytes / 1024 / 1024:.1f} MB")

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

# ---------------------------- 二次裁剪：拦掉 hook 加进来的文件
# 为什么需要两层（2026-11-19 实测）：
#
#   第一层（上面，COLLECT 前筛 datas/binaries）只能拦 collect_all 的产物。
#   但 PySide6 的 hook 会把 opengl32sw.dll 塞进 a.binaries，
#   cv2 的 hook（或 Analysis 阶段的依赖扫描）也会补回
#   opencv_videoio_ffmpeg500_64.dll —— 这两个都不在 collect_all 的返回里，
#   第一层筛了个寂寞（实测：日志说裁了 3 个文件 100.2 MB，
#   但产物里这两个 DLL 还在，实际只省了 70.8 MB）。
#
#   所以要在 Analysis 之后、COLLECT 之前再筛一遍 a.binaries / a.datas。
#
# TOC 条目的形态：
#   a.binaries:  (dest_name, src_path, typecode)    dest 形如 'PySide6\\opengl32sw.dll'
#   a.datas:     (dest_name, src_path, typecode)    dest 形如 'ddddocr\\common.onnx'
# 注意这里的 dest 是**相对输出根**的路径，带包名，可以直接匹配。


def _prune_toc(toc, label):
    kept, dropped = [], []
    for entry in toc:
        dest = str(entry[0]).replace("\\", "/")
        name = dest.rsplit("/", 1)[-1].lower()
        hit = _PRUNE_BY_NAME.get(name)
        if hit is None:
            kept.append(entry)
            continue
        expect_pkg, why = hit
        parent = dest.rsplit("/", 1)[0].lower() if "/" in dest else ""
        # 包名对得上才删，防止误伤
        if parent and not parent.endswith(expect_pkg):
            kept.append(entry)
            continue
        dropped.append((dest, why))
        print(f"[spec] 二次裁剪 {label:<8}-> {dest}  ({why})")
    return kept, dropped


a.binaries, _d1 = _prune_toc(a.binaries, "binaries")
a.datas, _d2 = _prune_toc(a.datas, "datas")


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
