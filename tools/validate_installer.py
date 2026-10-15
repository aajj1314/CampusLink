# -*- coding: utf-8 -*-
"""
检查 Inno Setup 脚本（.iss）的配置，不需要 ISCC。

在没有装 Inno Setup 的机器上也能跑这个，提前发现：
  · 引用的源文件路径不存在（最常见：忘了先 build_exe）
  · 图标文件缺失
  · 「开机自启」注册表项的键名 / 参数跟程序里的 autostart.py 对不上
    （对不上会导致安装时设了开关、程序内却显示「关闭」）
  · AppId 不是合法 GUID

用法:
    python tools/validate_installer.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ISS = ROOT / "packaging" / "CampusLink.iss"

PASS = FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {desc}")
    else:
        FAIL += 1
        print(f"  [FAIL] {desc}" + (f"  --  {detail}" if detail else ""))


def _resolve_defines(text: str) -> str:
    """
    展开 Inno 的 #define 常量，让后续检查能看到真实值。

    例：#define MyAppId "{{8F3A...}}"
        AppId={#MyAppId}  ->  AppId={{8F3A...}}
    """
    defines: dict[str, str] = {}
    for m in re.finditer(r'#define\s+(\w+)\s+"([^"]*)"', text):
        defines[m.group(1)] = m.group(2)
    out = text
    for name, value in defines.items():
        out = out.replace("{#" + name + "}", value)
    return out


def main() -> int:
    if not ISS.exists():
        print(f"找不到 {ISS}")
        return 1
    raw = ISS.read_text(encoding="utf-8-sig")  # 跳过 BOM
    text = _resolve_defines(raw)               # 展开 #define

    print("=" * 64)
    print(" Inno Setup 脚本预检")
    print("=" * 64)
    print()

    # ---- AppId ----
    m = re.search(r"AppId=([^\r\n]+)", text)
    check("AppId 已定义", bool(m), m.group(1) if m else "未找到")
    if m:
        # Inno 里字面 { 要写成 {{，所以 #define 里 GUID 是双花括号
        guid = m.group(1).strip().strip('"').replace("{{", "{").replace("}}", "}")
        check("AppId 是合法 GUID 格式",
              bool(re.match(r"^\{[0-9A-Fa-f-]{36}\}$", guid)), guid)

    # ---- 图标 ----
    m = re.search(r"SetupIconFile=([^\r\n]+)", text)
    if m:
        icon = (ROOT / m.group(1).replace("{#ProjectRoot}\\", "")).resolve()
        check("SetupIconFile 存在", icon.exists(), str(icon))

    # ---- 主要源文件 ----
    print()
    print("  -- 引用的源文件 --")
    sources = re.findall(r'Source:\s*"([^"]+)"', text)
    expanded = []
    for s in sources:
        # 展开 Inno 的预处理器变量
        s2 = s.replace("{#DistDir}", str(ROOT / "dist" / "CampusLink"))
        s2 = s2.replace("{#ProjectRoot}", str(ROOT))
        expanded.append((s, s2))
    for orig, full in expanded:
        p = Path(full)
        # 通配的情况（含 *）只检查父目录
        if "*" in full:
            parent = p.parent
            check(f"{orig} (通配父目录)", parent.exists(), str(parent))
        else:
            check(f"{orig}", p.exists(), str(p) if not p.exists() else "")

    # ---- 跟程序代码对齐 ----
    print()
    print("  -- 与程序代码的一致性 --")

    # 注册表键名要跟 autostart.py 的 VALUE_NAME 一致
    from campuslink import autostart
    m = re.search(r'ValueName:\s*"([^"]+)"', text)
    iss_value_name = m.group(1) if m else ""
    check("注册表键名 == autostart.VALUE_NAME",
          iss_value_name == autostart.VALUE_NAME,
          f".iss={iss_value_name!r} vs py={autostart.VALUE_NAME!r}")

    # 参数要跟 autostart.STARTUP_ARGS 一致
    # ValueData 在 .iss 里常带连续三个引号（Inno 的转义），且可能跨行，
    # 所以把续行符先拍平，再用「非贪婪到行尾」匹配
    flat = re.sub(r"\s*\\\s*\n\s*", " ", text)
    m = re.search(r'ValueData:\s*"(.+?)"\s*(?:;|$)', flat, re.M)
    iss_args = m.group(1) if m else ""
    check("启动参数包含 autostart.STARTUP_ARGS",
          autostart.STARTUP_ARGS in iss_args,
          f".iss={iss_args!r} 需含 {autostart.STARTUP_ARGS!r}")

    # Run 键路径要跟 autostart.RUN_KEY 一致
    m = re.search(r'Subkey:\s*"(Software\\Microsoft\\Windows\\CurrentVersion\\Run)"', text)
    check("注册表路径 == autostart.RUN_KEY",
          bool(m) and m.group(1) == autostart.RUN_KEY, autostart.RUN_KEY)

    # ---- 产物是否就绪 ----
    print()
    print("  -- 构建产物 --")
    exe = ROOT / "dist" / "CampusLink" / "CampusLink.exe"
    check("dist/CampusLink/CampusLink.exe 已构建", exe.exists(),
          "先跑 packaging/build_exe.bat" if not exe.exists() else "")

    # ---- 输出 ----
    print()
    print("=" * 64)
    print(f" 通过 {PASS}   失败 {FAIL}")
    print("=" * 64)
    if FAIL:
        print()
        print("  修复以上问题后再编译安装包：")
        print("    packaging\\build_installer.bat")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
