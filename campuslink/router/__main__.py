# -*- coding: utf-8 -*-
"""
Router 控制面入口 —— `python -m campuslink.router [--port 7700]`。

这是 OpenWrt 上的标准入口（procd init 脚本调它）。刻意**不**在模块顶层
import `campuslink.core`（那会连带 cv2/numpy/ddddocr，OpenWrt 上没有）。

配对 / 视觉任务 / 状态查询这些控制面能力只依赖标准库，OpenWrt 上立即可用；
认证编排（需要 core + 凭据）在 `_wire_auth()` 里按「core 是否可导入」优雅降级：
  · 可导入（PC / 已装依赖）→ 加载配置档案与凭据，认证走远程视觉节点
  · 不可导入（OpenWrt 裸跑）→ /auth/* 返回「未配置」，其余端点照常
    （core 去 cv2 化是 Agent 2 的 ocr_backend 交付项，落地后此路径自动生效）
"""
from __future__ import annotations

import argparse

from ._vendor import install
from .apiserver import ApiServer, RouterContext
from .auth import AuthController
from .pairing import PairingManager
from .taskmanager import TaskManager


def _wire_auth(tm: TaskManager) -> AuthController | None:
    """尽力加载配置与凭据，失败返回 None（/auth 保持未配置）。"""
    try:
        from campuslink import profiles
        from campuslink.config import AppConfig
        from campuslink.core.models import Credentials
        from campuslink.logging_setup import register_secret

        cfg = AppConfig.load()
        prof = profiles.load_profile(cfg.profile_name)
        pwd = cfg.get_password()
        if pwd:
            register_secret(pwd)
        cred = Credentials(student_id=cfg.student_id, account=cfg.account,
                           password=pwd, operator=cfg.operator).normalized()
        if not prof.portal_url or prof.portal_url == "auto":
            return None
        return AuthController(tm, profile=prof, credentials=cred)
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    install()          # 按 CAMPUSLINK_VENDOR=1 注入 vendored 依赖（OpenWrt）
    ap = argparse.ArgumentParser(prog="python -m campuslink.router",
                                 description="CampusLink Router 控制面")
    ap.add_argument("--port", type=int, default=7700)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args(argv)

    tm = TaskManager()
    auth = _wire_auth(tm)
    ctx = RouterContext(taskmanager=tm, pairing=PairingManager(),
                        auth=auth or AuthController(tm),
                        portal_name=getattr(getattr(auth, "profile", None),
                                            "name", ""))
    srv = ApiServer(ctx, host=args.host, port=args.port)
    print(f"CampusLink Router 控制面已启动 {args.host}:{args.port}")
    print("  认证编排:", "已接入" if auth else "未配置（待凭据下发）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
