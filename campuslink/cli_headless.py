# -*- coding: utf-8 -*-
"""
无人值守 CLI（headless）—— 给「装完就忘」场景的极简发行形态。

## 交互设计（用户拍板，2026-10-19）

  · 密码明文存 json（`cli_config.json`）—— 单机自用，用户明确选择
  · 开机自启（Startup 里指向 silent_run.vbs 的快捷方式）：以**隐藏窗口**
    方式运行（vbs Run 0），黑框完全不可见；成功退出码 0，失败写日志退出码 1
  · 桌面图标（无参数）：console 打包自带控制台，显示认证进度与结果，
    结束前停留 2 秒让用户看到
  · 首次运行（无配置文件）：分配控制台，依次输入
    学(工)号 / 上网账号 / 密码，生成 json，并提示安装器已创建的快捷方式
  · 断网不自动重连：掉线后由用户手动点击桌面图标再认证

## 与 GUI 版的关系

认证核心完全复用（Authenticator / crnn_math / captcha_math / profiles），
本模块只是「无人值守」的壳。**不 import PySide6**，
打包 excludes 硬排除 PySide6 / keyring / cryptography / ddddocr / cv2。

## 打包注意（CampusLinkCLI.spec）

`console=True`（原生控制台）。手动模式直接显示结果；静默模式靠
silent_run.vbs 以隐藏窗口启动（安装器生成），黑框不可见。
**不要改回 windowed + AllocConsole**：标准流重定向在 windowed 下
是坑窝（TextIOWrapper 参数、CONIN$ 交互、编码），已实际踩崩一次。
"""
from __future__ import annotations

import json
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
def _trace(msg: str) -> None:
    """
    启动追踪：每个关键步骤记录一条 debug 日志。

    日志文件（%APPDATA%\\CampusLink\\logs\\）是无人值守程序唯一
    验证过可靠的诊断通道——独立 trace 文件曾出现静默不落盘（原因未明，
    已被日志覆盖替代）。main 在 setup() 之后调用本函数，handler 已就绪。
    """
    log.debug("TRACE %s", msg)


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。返回进程退出码。"""
    silent = "--silent" in (argv or sys.argv[1:])

    # 日志永远开着：静默模式的失败全靠它。必须在最前面初始化，
    # 之后所有 _trace/log 调用才有落点。
    setup(console=False)
    _trace(f"启动（silent={silent}，argv={argv or sys.argv[1:]}）")
    out = _open_console_out(silent)
    if out is None:
        # 双保险：控制台分配失败时也不能让 out 是 None（windowed 下
        # sys.stderr 也是 None）。所有 write 走空实现，输出全在日志里。
        out = _NullOut()

    cfg = CliConfig.load()
    _trace(f"配置加载：{'存在' if cfg else '不存在'}")
    # 首次/重新配置的**唯一判据**：json 是否存在且三字段齐全（学号/账号/密码）。
    # 与网络状态无关——已联网也要先把账号存进 json，断网时才能静默使用。
    if cfg is None or not cfg.complete():
        if cfg is not None and not cfg.complete():
            _trace("配置存在但字段不完整 → 重新配置")
        if silent:
            # 开机自启 + 未配置：不能交互，提示一次让用户手动完成
            _trace("静默启动未配置 → MessageBox 提示")
            _messagebox("CampusLink 尚未完成初始配置\n"
                        "请双击桌面图标，按提示输入账号信息。")
            log.error("静默启动时发现未配置，已提示用户")
            return EXIT_SETUP_REQUIRED
        _trace("进入首次配置交互")
        cfg = run_setup(out)
        _trace(f"首次配置结果：{'成功' if cfg else '失败/中断'}")
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

    try:
        profile = profiles.load_profile(cfg.profile_name or DEFAULT_PROFILE_NAME)
        _trace(f"档案加载：{profile.name}（mode={profile.captcha_mode}）")
    except Exception as exc:
        _trace(f"档案加载失败：{exc!r}")
        log.exception("档案加载失败")
        out.write(f"✗ 门户档案加载失败：{exc}")
        _pause()
        return EXIT_BAD_CONFIG

    if check_already_online(profile, out):
        _trace("已在线，跳过认证")
        return EXIT_OK

    _trace("开始认证")
    ok = run_auth(cfg, profile, verbose=not silent, out=out)
    _trace(f"认证结束：{'成功' if ok else '失败'}")

    if not silent:
        out.write("\n" + ("✓ 完成" if ok else "✗ 认证失败，详情见日志"))
        _pause()
    _trace(f"退出码 {EXIT_OK if ok else EXIT_AUTH_FAILED}")
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
    返回用户可见的输出流。

    console 打包自带控制台：手动双击 = 窗口显示进度与结果；
    --silent = 输出走日志（Startup 里由 vbs 以隐藏窗口方式启动，黑框不可见）。
    stdout 意外不可用时退化为 _NullOut，绝不返回 None（已踩坑）。
    """
    if silent:
        return None
    try:
        if sys.stdout and sys.stdout.writable():
            return sys.stdout
    except Exception:
        pass
    return _NullOut()


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
