# -*- coding: utf-8 -*-
"""
把 CampusLink Router 打成 OpenWrt 通用插件（.ipk）。

格式（重要，踩过坑）：**经典 opkg 的 .ipk 是「外层 gzip tar」，不是 ar/deb 格式**。

    campuslink-router.ipk  (gzip tar)
      ├── debian-binary        "2.0\n"
      ├── control.tar.gz       (内含 ./control ./postinst ./prerm)
      └── data.tar.gz          (内含 ./opt/campuslink/...)

初版做成了 ar 归档（.deb 风格），在部分 opkg 上报
"Malformed package file" —— 老版 opkg（OpenWrt ≤21.02 及多数国产改版）
只认外层 tar.gz。外层 tar.gz 是 opkg 最长寿、兼容性最广的格式，
现代 opkg 同样读得懂，所以一律用它。

产物：dist/campuslink-router_<version>_all.ipk
      Architecture=all（纯 Python 与架构无关，任何 OpenWrt 目标可装）。

用法：
    python tools/build_ipk.py            # 打包
    python tools/build_ipk.py --arch mipsel_24kc
    python tools/build_ipk.py --list     # 只列出文件，不产出
    python tools/build_ipk.py --verify   # 产出后立即校验结构
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "campuslink"
INIT = ROOT / "router" / "campuslink.init"
LAUNCHER = ROOT / "router" / "campuslink-run.sh"

# 不打进插件的东西（重物 / 平台专有 / 缓存）。
# 注意：campuslink/router/ 是**运行代码**（含 _vendor/packages），必须打包；
# 部署脚本（init 脚本）在仓库根目录的 router/，本就在 campuslink/ 之外，不会误收。
EXCLUDE_DIRS = {"gui", "__pycache__"}
EXCLUDE_FILES = {"ocr.py"}                        # cv2 依赖，OpenWrt 上无
INSTALL_PREFIX = Path("opt/campuslink")           # 代码树装到 /opt/campuslink


def version() -> str:
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']',
                  (PKG / "__init__.py").read_text(encoding="utf-8"))
    return m.group(1) if m else "1.0.0"


def collect_files() -> list[tuple[Path, str]]:
    """
    返回 [(本地路径, ipk 内路径)]。data 内路径以 "./" 开头（tar 约定）。
    """
    out: list[tuple[Path, str]] = []
    for p in sorted(PKG.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        parts = rel.parts
        if parts and parts[0] == "campuslink":
            sub = parts[1:]
            if sub and sub[0] in EXCLUDE_DIRS:
                continue
            if sub and sub[-1] in EXCLUDE_FILES:
                continue
            if any(part in EXCLUDE_DIRS or part.endswith(".pyc")
                   for part in sub):
                continue
            dest = Path(INSTALL_PREFIX) / "campuslink" / Path(*sub)
            out.append((p, "./" + dest.as_posix()))
    if INIT.exists():
        out.append((INIT, "./etc/init.d/campuslink"))
    if LAUNCHER.exists():
        out.append((LAUNCHER, "./opt/campuslink/campuslink-run.sh"))
    return out


# ---------------------------------------------------------------- tar 工具
def _add_parent_dirs(tf: tarfile.TarFile, paths: list[str]) -> None:
    """
    给 tar 补显式目录条目。

    opkg 解包时**不会自动创建父目录**，只写文件 —— 若 tar 里只有
    "./opt/campuslink/campuslink/__init__.py" 而没有 "./opt/campuslink/campuslink/"
    目录条目，就会报 "wfopen ... No such file or directory"。
    经典 opkg-build（`tar czf data.tar.gz -C $dir .`）会带上目录条目，这里手动补齐。
    """
    dirs: set[str] = set()
    for arc in paths:
        parts = arc.split("/")
        # arc 形如 "./a/b/c"，parts=["." "a" "b" "c"]；i 从 2 到 len-1 枚举父目录
        for i in range(2, len(parts)):
            dirs.add("/".join(parts[:i]) + "/")
    for d in sorted(dirs):
        info = tarfile.TarInfo(d)
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        info.uid = info.gid = 0
        info.mtime = int(time.time())
        tf.addfile(info)


def _tar_gz(files: list[tuple[Path, str]],
            add: dict[str, str] | None = None) -> bytes:
    """把 (本地路径, 归档内路径) 打成 gzip tar。add = {归档内路径: 文本内容}。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tf:
        _add_parent_dirs(tf, [a for _, a in files])
        for src, arc in files:
            info = tf.gettarinfo(str(src), arc)
            info.mode = 0o755 if ("init.d" in arc or arc.endswith(".sh")) else 0o644
            info.uid = info.gid = 0
            with open(src, "rb") as fh:
                tf.addfile(info, fh)
        for arc, content in (add or {}).items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(arc)
            info.size = len(data)
            info.mode = 0o755 if arc.endswith(("postinst", "prerm")) else 0o644
            info.mtime = int(time.time())
            info.uid = info.gid = 0
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def build_ipk(arch: str) -> tuple[bytes, list[str]]:
    files = collect_files()
    names = [a for _, a in files]

    ver = version()
    control = (
        f"Package: campuslink-router\n"
        f"Version: {ver}\n"
        f"Architecture: {arch}\n"
        f"Section: net\n"
        f"Priority: optional\n"
        f"Depends: python3\n"
        f"Maintainer: CampusLink\n"
        f"Description: CampusLink Router control plane\n"
        f" 校园网 Portal 认证的局域网控制面：管理认证会话，\n"
        f" 并把验证码识别任务分发给桌面/手机视觉节点。\n"
    )
    postinst = ("#!/bin/sh\n"
                "[ -x /etc/init.d/campuslink ] && "
                "/etc/init.d/campuslink enable 2>/dev/null || true\n"
                "exit 0\n")
    prerm = ("#!/bin/sh\n"
             "/etc/init.d/campuslink stop 2>/dev/null || true\n"
             "/etc/init.d/campuslink disable 2>/dev/null || true\n"
             "exit 0\n")

    control_tar = _tar_gz([], {"./control": control,
                               "./postinst": postinst,
                               "./prerm": prerm})
    data_tar = _tar_gz(files)

    # 外层：gzip tar，含三个成员（经典 opkg .ipk 格式）
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tf:
        for name, data in (("debian-binary", b"2.0\n"),
                           ("control.tar.gz", control_tar),
                           ("data.tar.gz", data_tar)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = int(time.time())
            info.mode = 0o644
            info.uid = info.gid = 0
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue(), names


def verify(ipk_path: Path) -> None:
    """校验产出：外层 tar.gz → 两个内层 tar.gz → control 可读。"""
    print(f"[verify] {ipk_path}")
    with tarfile.open(ipk_path, "r:gz") as outer:
        members = outer.getnames()
        print(f"  外层成员: {members}")
        assert members == ["debian-binary", "control.tar.gz", "data.tar.gz"], \
            "外层成员不符合经典 ipk 约定"
        db = outer.extractfile("debian-binary").read()
        assert db == b"2.0\n", f"debian-binary 内容异常 {db!r}"

        ct = tarfile.open(fileobj=io.BytesIO(
            outer.extractfile("control.tar.gz").read()), mode="r:gz")
        ctl = ct.extractfile("./control").read().decode()
        print("  control 头部:")
        for line in ctl.strip().splitlines():
            print(f"    {line}")
        assert "Package: campuslink-router" in ctl
        assert "Depends: python3" in ctl

        dt = tarfile.open(fileobj=io.BytesIO(
            outer.extractfile("data.tar.gz").read()), mode="r:gz")
        dn = dt.getnames()
        print(f"  data 文件数: {len(dn)}")
        assert any("taskmanager.py" in n for n in dn)
        assert any("init.d/campuslink" in n for n in dn)
        # 关键：必须有显式目录条目，否则 opkg 解包会 "wfopen ... No such file or directory"
        dirs = [m.name for m in dt.getmembers() if m.isdir()]
        assert "./opt/campuslink/campuslink" in dirs, "缺少目录条目！"
        print(f"  目录条目: {len(dirs)} 个（含 ./opt/campuslink/campuslink）")
    print("  [OK] 结构合法")


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 CampusLink Router .ipk")
    ap.add_argument("--arch", default="all", help="Architecture（默认 all=通用）")
    ap.add_argument("--list", action="store_true", help="只列出将打进去的文件")
    ap.add_argument("--verify", action="store_true", help="打包后校验结构")
    ap.add_argument("--out", default="", help="输出目录（默认 dist/）")
    args = ap.parse_args()

    files = collect_files()
    if not files:
        print("[ERROR] 没收集到任何文件")
        return 1

    if args.list:
        for _, arc in files:
            print(f"  {arc}")
        print(f"\n  共 {len(files)} 个文件")
        return 0

    data, names = build_ipk(args.arch)
    outdir = Path(args.out) if args.out else (ROOT / "dist")
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"campuslink-router_{version()}_{args.arch}.ipk"
    out.write_bytes(data)

    print(f"[OK] {out}")
    print(f"     {len(data) / 1048576:.2f} MB · {len(names)} 个文件 · "
          f"Architecture={args.arch}")
    if args.verify:
        verify(out)
    print()
    print("  安装到路由器：")
    print(f"    scp {out.name} root@192.168.1.1:/tmp/")
    print("    opkg install /tmp/" + out.name)
    print("    /etc/init.d/campuslink start")
    print()
    print("  卸载： opkg remove campuslink-router")
    return 0


if __name__ == "__main__":
    sys.exit(main())
