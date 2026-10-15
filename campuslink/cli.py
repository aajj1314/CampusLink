# -*- coding: utf-8 -*-
"""
命令行入口。

存在的意义有两条：
  1. **验证 core 真的没被 GUI 污染** —— 这个文件不 import 任何 Qt，
     能跑起来就说明认证核心是可独立使用的（OpenWrt 移植的前提）。
  2. 排查问题时比 GUI 好用：能看到每一步的细节，也能进定时任务。

    python -m campuslink.cli netcheck          网络体检 + 门户发现
    python -m campuslink.cli dry-run           只解析登录页、识别验证码，不提交
    python -m campuslink.cli login             用已保存的凭据认证一次
    python -m campuslink.cli info              运行环境与凭据状态
    python -m campuslink.cli loop --interval 60  常驻自动重连
    python -m campuslink.cli router-serve --sim --port 7700  启动 Router 控制面（PC 模拟器）
"""
from __future__ import annotations

import argparse
import sys
import time

from . import __version__, autostart, paths, profiles, secrets
from .config import AppConfig
from .core import netdetect
from .core.authenticator import Authenticator
from .core.models import Credentials, NetworkState, STATE_TEXT
from .core.portal import PortalClient
from .logging_setup import get_logger, register_secret, setup as setup_logging

log = get_logger("cli")


# ------------------------------------------------------------------ 工具
def _load() -> tuple[AppConfig, object]:
    cfg = AppConfig.load()
    prof = profiles.load_profile(cfg.profile_name)
    return cfg, prof


def _credentials(cfg: AppConfig) -> Credentials:
    pwd = cfg.get_password()
    if pwd:
        register_secret(pwd)
    return Credentials(student_id=cfg.student_id, account=cfg.account,
                       password=pwd, operator=cfg.operator).normalized()


# ------------------------------------------------------------------ 命令
def cmd_info(_args) -> int:
    print("=" * 62)
    print(f" CampusLink {__version__}")
    print("=" * 62)
    print(f"  {paths.describe()}")
    print(f"  配置文件   : {paths.config_file()}")
    print(f"  日志目录   : {paths.log_dir()}")
    print()
    print(f"  凭据后端   : {secrets.status_text()}")
    print(f"  开机启动   : {'支持' if autostart.supported() else '不支持'}"
          f"{'（已开启）' if autostart.is_enabled() else '（未开启）'}")
    print()
    print("  可用门户档案:")
    for p in profiles.list_profiles():
        print(f"    - {p.name}  ({p.portal_url})")
    print()
    cfg, prof = _load()
    print("  当前配置:")
    for k, v in cfg.summary().items():
        print(f"    {k:10s}: {v}")
    print()
    return 0


def cmd_netcheck(_args) -> int:
    print("正在检测网络…")
    rep = netdetect.detect()
    print()
    print(f"  本机 IP    : {rep.local_ip or '（无）'}")
    for gw, iface in rep.gateways:
        print(f"  默认网关   : {gw}  (出口接口 {iface})")
    print()
    print("  HTTP 探测:")
    for name, v in rep.probes.items():
        label = {"clean": "正常（真联网）", "hijacked": "被劫持到内网",
                 "unreachable": "不可达"}.get(v, v)
        print(f"    {name:14s} {label}")
    print()
    print(f"  判定状态   : {rep.summary}")
    if rep.portal_url:
        print(f"  门户地址   : {rep.portal_url}")
        print(f"  发现方式   : {rep.found_by}")
    if rep.note:
        print(f"  备注       : {rep.note}")
    print()
    return 0


def cmd_dryrun(_args) -> int:
    """只走到「识别出验证码」为止，不提交 —— 调试验证码识别率用。"""
    cfg, prof = _load()
    if not prof.portal_url or prof.portal_url == "auto":
        rep = netdetect.detect()
        if not rep.portal_url:
            print("没能自动发现门户地址，请检查网络或门户配置。")
            return 1
        prof.portal_url = rep.portal_url
        print(f"自动发现门户: {prof.portal_url}")

    client = PortalClient(prof)
    for i in (1, 2):
        print(f"\n--- 第 {i} 次 ---")
        try:
            page = client.open_login_page()
        except Exception as exc:
            print(f"  打开登录页失败: {getattr(exc, 'user_message', exc)}")
            return 1
        print(f"  提交目标 : {page.method} {page.action}")
        print(f"  字段数   : {len(page.fields)}")
        visible = [k for k in page.fields
                   if not k.startswith("__") and "VIEWSTATE" not in k]
        print(f"  非隐藏域 : {', '.join(visible[:6])}")
        img = client.fetch_captcha(page)
        if not img:
            print("  该门户无需验证码")
            return 0
        from .core import ocr
        text, detail = ocr.recognize(img, strategies=prof.ocr_strategies,
                                     length=prof.ocr_length)
        print(f"  验证码   : {text!r}  明细 {detail}")
    print("\ndry-run 结束：解析与识别链路正常。")
    return 0


def cmd_login(args) -> int:
    cfg, prof = _load()
    cred = _credentials(cfg)
    if not cred.is_complete():
        print("账号信息不完整。请先运行图形界面填写，或检查配置：")
        print(f"  {paths.config_file()}")
        return 1

    def say(msg: str) -> None:
        print(f"  {msg}")

    auth = Authenticator(prof, verbose=True)
    res = auth.login(cred, on_progress=say)
    print()
    print(f"  结果: {'成功' if res.ok else '失败'} | {res.message}")
    if res.raw_message and res.raw_message != res.message:
        print(f"  门户提示: {res.raw_message}")
    return 0 if res.ok else 2


def cmd_loop(args) -> int:
    cfg, prof = _load()
    interval = max(15, int(args.interval))
    print(f"常驻模式，每 {interval} 秒检查一次。Ctrl+C 退出。")
    auth = Authenticator(prof)
    while True:
        try:
            stamp = time.strftime("%H:%M:%S")
            if netdetect.is_online(timeout=5):
                print(f"[{stamp}] 在线")
            else:
                cred = _credentials(cfg)
                if not cred.is_complete():
                    print(f"[{stamp}] 掉线，但账号信息不完整，跳过")
                else:
                    print(f"[{stamp}] 掉线，正在重新认证…")
                    res = auth.login(cred, on_progress=lambda m: print(f"    {m}"))
                    print(f"[{stamp}] {'已恢复' if res.ok else '失败: ' + res.message}")
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n已退出。")
            return 0


def cmd_router_serve(args) -> int:
    """
    启动 Router 控制面（/api/v1 服务端）。

    --sim：PC 模拟器模式（内置模拟门户 + 可选本地演示视觉节点）；
    不带 --sim：以真实门户配置运行（仅控制面，认证走远程视觉节点）。
    """
    if getattr(args, "sim", False):
        import importlib.util
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "tools" / "router_sim.py"
        spec = importlib.util.spec_from_file_location("_router_sim", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        argv = ["--port", str(args.port)]
        if getattr(args, "demo_node", False):
            argv.append("--demo-node")
        if getattr(args, "user", None):
            argv += ["--user", args.user, "--pwd", args.pwd or "test-password"]
        return mod.main(argv)

    # --- 真实模式：控制面 + 已保存的门户档案与凭据 ---
    from .config import AppConfig
    from .core.models import Credentials
    from .logging_setup import setup as _setup_log
    from .router import (ApiServer, AuthController, PairingManager,
                         RouterContext, TaskManager)
    from .router import _vendor
    _vendor.install()          # OpenWrt：按 CAMPUSLINK_VENDOR=1 注入 vendored 依赖

    _setup_log(console=True)
    cfg = AppConfig.load()
    prof = profiles.load_profile(cfg.profile_name)
    pwd = cfg.get_password()
    if pwd:
        register_secret(pwd)
    cred = Credentials(student_id=cfg.student_id, account=cfg.account,
                       password=pwd, operator=cfg.operator).normalized()

    tm = TaskManager()
    ctx = RouterContext(
        taskmanager=tm, pairing=PairingManager(),
        auth=AuthController(tm, profile=prof, credentials=cred),
        portal_name=prof.name,
    )
    srv = ApiServer(ctx, host=getattr(args, "host", "0.0.0.0"),
                    port=args.port)
    print(f"CampusLink Router 控制面已启动 :{args.port}  (Ctrl+C 退出)")
    print(f"  门户: {prof.portal_url}")
    print("  密码仅存内存；视觉节点经 /api/v1/vision/tasks/* 领取验证码任务。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    return 0


# ------------------------------------------------------------------ 入口
def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    ap = argparse.ArgumentParser(
        prog="campuslink", description="CampusLink 校园网认证命令行")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("info", help="运行环境与凭据状态").set_defaults(fn=cmd_info)
    sub.add_parser("netcheck", help="网络体检 + 门户发现").set_defaults(fn=cmd_netcheck)
    sub.add_parser("dry-run", help="只解析页面+识别验证码，不提交").set_defaults(fn=cmd_dryrun)
    sub.add_parser("login", help="认证一次").set_defaults(fn=cmd_login)

    p_loop = sub.add_parser("loop", help="常驻自动重连")
    p_loop.add_argument("--interval", type=int, default=60, help="检查间隔秒数")
    p_loop.set_defaults(fn=cmd_loop)

    p_router = sub.add_parser("router-serve", help="启动 Router 控制面（/api/v1）")
    p_router.add_argument("--sim", action="store_true",
                          help="PC 模拟器模式（内置模拟门户）")
    p_router.add_argument("--port", type=int, default=7700, help="API 监听端口")
    p_router.add_argument("--host", default="0.0.0.0", help="API 监听地址")
    p_router.add_argument("--demo-node", action="store_true",
                          help="（仅 --sim）起一个本地演示视觉节点")
    p_router.add_argument("--user", default=None, help="（仅 --sim）模拟门户账号")
    p_router.add_argument("--pwd", default=None, help="（仅 --sim）模拟门户密码")
    p_router.set_defaults(fn=cmd_router_serve)

    args = ap.parse_args(argv)
    setup_logging(console=True)

    if not getattr(args, "fn", None):
        ap.print_help()
        return 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
