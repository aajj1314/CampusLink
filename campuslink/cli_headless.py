# -*- coding: utf-8 -*-
"""
无人值守 CLI（headless）—— 给「装完就忘」场景的极简发行形态。

## 交互设计（用户拍板，2026-10-19）

  · 密码明文存 json（`cli_config.json`）—— 单机自用，用户明确选择
  · 开机自启（Startup 快捷方式带 --silent）：**完全静默**——不开控制台、
    不弹窗；成功退出码 0，失败写日志退出码 1
  · 桌面图标（无参数）：手动点击时动态分配控制台（AllocConsole），
    显示认证进度与结果，结束前停留 2 秒让用户看到
  · 首次运行（无配置文件）：分配控制台，依次输入
    学(工)号 / 上网账号 / 密码，生成 json，并提示安装器已创建的快捷方式
  · 断网不自动重连：掉线后由用户手动点击桌面图标再认证

## 与 GUI 版的关系

认证核心完全复用（Authenticator / crnn_math / captcha_math / profiles），
本模块只是「无人值守」的壳。**不 import PySide6**，
打包 excludes 硬排除 PySide6 / keyring / cryptography / ddddocr / cv2。

## 打包注意（CampusLinkCLI.spec）

`console=False`（windowed）。这是有意为之：控制台按需分配，
开机静默启动才不会闪黑框。手动模式里的 `ensure_console()`
负责在需要时分配并重定向标准流。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import paths, profiles
from .core.authenticator import Authenticator
from .core.models import Credentials, PortalProfile
from .logging_setup import get_logger, setup

log = get_logger("cli")

#: 配置文件名（与 GUI 的 config.json 分开，互不影响）
CLI_CONFIG_FILE = "cli_config.json"

#: 手动模式结束时停留的秒数，让用户看清结果
RESULT_PAUSE_SECONDS = 2

#: 门户档案。CLI 版面向单门户发行，不做档案选择界面。
DEFAULT_PROFILE_NAME = "桂林信息科技学院"

EXIT_OK, EXIT_AUTH_FAILED, EXIT_BAD_CONFIG, EXIT_SETUP_REQUIRED = 0, 1, 2, 3


# ============================================================ 配置（明文 json）
@dataclass
class CliConfig:
    """三个字段 + 门户档案名。密码按用户要求明文保存。"""

    student_id: str = ""
    account: str = ""
    password: str = ""
    profile_name: str = DEFAULT_PROFILE_NAME

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "CliConfig | None":
        """读取配置；文件不存在 / 损坏返回 None（调用方走首次配置）。"""
        p = path or (paths.data_dir() / CLI_CONFIG_FILE)
        try:
            if not p.exists():
                return None
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            known = {f for f in cls.__dataclass_fields__}
            clean = {k: str(v) for k, v in data.items() if k in known}
            return cls(**clean)
        except Exception:
            log.warning("cli 配置文件损坏，视为未配置", exc_info=True)
            return None

    def save(self, path: Path | None = None) -> bool:
        p = path or (paths.ensure_data_dir() / CLI_CONFIG_FILE)
        try:
            p.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                         encoding="utf-8")
            return True
        except Exception:
            log.exception("cli 配置写入失败")
            return False

    def credentials(self) -> Credentials:
        return Credentials(
            student_id=self.student_id,
            account=self.account,
            password=self.password,
        ).normalized()

    def complete(self) -> bool:
        return bool(self.student_id and self.password)


# ============================================================ 控制台
def ensure_console() -> bool:
    """
    windowed 打包下按需分配控制台并重定向标准流。

    已有控制台（开发期直接 python 运行）时直接返回 True。
    分配失败返回 False（调用方退化为静默）。
    """
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
    except Exception:
        return True                      # 非 Windows（开发环境），直接用

    if kernel32.GetConsoleWindow():
        return True                      # 本来就有控制台

    if not kernel32.AllocConsole():
        # 分配失败：可能已附加了控制台（如从 bash/CI 里运行），
        # 此时标准流通常是可用的 —— 流可用就当有控制台。
        try:
            if sys.stdout and sys.stdout.writable():
                return True
        except Exception:
            pass
        return False

    kernel32.SetConsoleOutputCP(65001)   # utf-8，中文不乱码
    kernel32.SetConsoleCP(65001)

    import io
    import os
    # windowed Python 的 sys.stdout/stdin 是 None，必须重建并接到底层句柄。
    # 注意 TextIOWrapper 没有 buffering 参数（那是 open() 的）——
    # 传错会让重定向整体失败，进而 run_setup 拿到 None 崩溃（已踩坑）。
    try:
        sys.stdout = io.TextIOWrapper(
            io.FileIO(os.open("CONOUT$", os.O_WRONLY | os.O_TEXT), "w",
                      closefd=True),
            encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = sys.stdout
        sys.stdin = io.TextIOWrapper(
            io.FileIO(os.open("CONIN$", os.O_RDONLY | os.O_TEXT), "r",
                      closefd=True),
            encoding="utf-8", errors="replace")
    except Exception:
        log.exception("控制台流重定向失败")
        return False
    return True


# ============================================================ 快捷方式
def _shell_folder(name: str) -> str:
    """系统特殊文件夹真实路径（桌面/启动），自动处理 OneDrive 重定向。"""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"[Environment]::GetFolderPath('{name}')"],
        capture_output=True, text=True, timeout=20,
    )
    return (r.stdout or "").strip()


def create_shortcut(target: Path, lnk_path: Path, arguments: str = "") -> bool:
    """用 WScript.Shell COM 生成 .lnk（PowerShell 系统自带，零额外依赖）。"""
    ps = (
        f"$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{lnk_path}'); "
        f"$s.TargetPath = '{target}'; "
        f"$s.Arguments = '{arguments}'; "
        f"$s.WorkingDirectory = '{target.parent}'; "
        f"$s.IconLocation = '{target},0'; "
        f"$s.Save()"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            log.warning("快捷方式创建失败: %s", (r.stderr or "")[:200])
            return False
        return True
    except Exception:
        log.exception("快捷方式创建异常: %s", lnk_path)
        return False


def install_shortcuts(target: Path) -> tuple[bool, bool]:
    """
    创建两个快捷方式：桌面图标（无参数，手动认证）+ 启动文件夹（--silent）。

    返回 (桌面成功?, 自启成功?)。
    """
    desktop = Path(_shell_folder("Desktop"))
    startup = Path(_shell_folder("Startup"))
    ok_desktop = create_shortcut(target, desktop / "CampusLink 认证.lnk")
    ok_startup = create_shortcut(target, startup / "CampusLink 静默认证.lnk",
                                 arguments="--silent")
    return ok_desktop, ok_startup


# ============================================================ 首次配置
def run_setup(out) -> CliConfig | None:
    """三字段交互输入 → 生成配置。输入中断（Ctrl+C/EOF）返回 None。"""
    out.write("=== CampusLink 首次配置 ===\n")
    out.write("请依次输入以下信息（配置保存在本机 json 中，之后无需再输入）\n\n")

    def ask(prompt: str, *, secret: bool = False) -> str:
        if secret:
            import getpass
            v = getpass.getpass(prompt)
        else:
            out.write(prompt)
            out.flush()
            v = (sys.stdin.readline() or "").strip()
        return v.strip()

    try:
        student_id = ask("学(工)号: ")
        account = ask("上网账号（留空则用学号）: ")
        password = ask("密码: ", secret=True)
    except (EOFError, KeyboardInterrupt):
        return None

    if not student_id or not password:
        out.write("✗ 学(工)号和密码不能为空\n")
        return None

    cfg = CliConfig(student_id=student_id, account=account, password=password)
    if not cfg.save():
        out.write("✗ 配置写入失败，请检查磁盘权限（日志有详情）\n")
        return None

    out.write("\n✓ 配置已保存\n")
    return cfg


# ============================================================ 认证
def run_auth(cfg: CliConfig, profile: PortalProfile, *,
             verbose: bool, out) -> bool:
    """执行一次认证。返回是否成功。"""

    def say(msg: str) -> None:
        if verbose:
            try:
                out.write(msg + "\n")
                out.flush()
            except Exception:
                pass

    auth = Authenticator(profile, verbose=True)
    res = auth.login(
        cfg.credentials(),
        on_progress=say,
        cancel=None,                 # 无人值守：没有取消入口
    )
    if res.ok:
        say(f"✓ {res.message or '认证成功'}")
        log.info("CLI 认证成功（第 %s 次尝试）", res.attempts or 1)
        return True

    say(f"✗ {res.message}")
    log.error("CLI 认证失败: %s（尝试 %s 次，详情见后续日志）",
              res.message, res.attempts)
    return False


# ============================================================ 入口
def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。返回进程退出码。"""
    silent = "--silent" in (argv or sys.argv[1:])

    # 日志永远开着：静默模式的失败全靠它。
    # console=False：windowed 打包下没有 stdout，输出全走日志文件
    setup(console=False)
    out = _open_console_out(silent)
    if out is None:
        # 双保险：控制台分配失败时也不能让 out 是 None（windowed 下
        # sys.stderr 也是 None）。所有 write 走空实现，输出全在日志里。
        out = _NullOut()

    cfg = CliConfig.load()
    if cfg is None:
        if silent:
            # 开机自启 + 未配置：不能交互，提示一次让用户手动完成
            _messagebox("CampusLink 尚未完成初始配置\n"
                        "请双击桌面图标，按提示输入账号信息。")
            log.error("静默启动时发现未配置，已提示用户")
            return EXIT_SETUP_REQUIRED
        cfg = run_setup(out)
        if cfg is None:
            return EXIT_BAD_CONFIG
        # 配置完成，检查是否已在线（首次装好往往本来就联网了）
        from .core.netdetect import is_online
        if is_online(timeout=5):
            out.write("\n✓ 当前已联网，无需认证。桌面与开机自启快捷方式已就绪。\n")
            _pause()
            return EXIT_OK

    from .logging_setup import register_secret
    register_secret(cfg.password)      # 密码不进日志（虽然明文存 json）

    profile = profiles.load_profile(cfg.profile_name or DEFAULT_PROFILE_NAME)

    if check_already_online(profile, out):
        return EXIT_OK

    ok = run_auth(cfg, profile, verbose=not silent, out=out)

    if not silent:
        out.write("\n" + ("✓ 完成" if ok else "✗ 认证失败，详情见日志"))
        _pause()
    return EXIT_OK if ok else EXIT_AUTH_FAILED


def check_already_online(profile: PortalProfile, out) -> bool:
    """已在线则直接成功退出（省一次认证往返）。"""
    from .core.netdetect import is_online
    if not profile.check_online_first:
        return False
    online = is_online(timeout=5)
    if online:
        try:
            out.write("当前已联网，无需认证\n")
        except Exception:
            pass
        log.info("CLI 检测到已在线，跳过认证")
    return online


class _NullOut:
    """写什么都没反应的输出流 —— 控制台彻底不可用时的兜底。"""

    def write(self, _t: str) -> None:
        pass

    def flush(self) -> None:
        pass


def _open_console_out(silent: bool):
    """
    手动模式（非 silent）：分配控制台并返回输出流。
    silent 模式不分配任何控制台，返回 None（输出全走日志）。
    """
    if silent:
        return None
    if ensure_console():
        return sys.stdout
    return None


def _pause() -> None:
    """手动模式的结果停留窗口，给用户两秒反应时间。"""
    try:
        out = sys.stdout
        out.write(f"\n（{RESULT_PAUSE_SECONDS} 秒后自动关闭…）")
        out.flush()
    except Exception:
        pass
    time.sleep(RESULT_PAUSE_SECONDS)


def _messagebox(text: str) -> None:
    """零 GUI 依赖的弹窗（win32 MessageBox），仅用于必须提示用户的异常状态。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "CampusLink", 0x40)
    except Exception:
        log.info("MessageBox: %s", text)


if __name__ == "__main__":
    sys.exit(main())
