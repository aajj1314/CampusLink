"""程序入口：解析参数、加载配置、启动监测。"""

from __future__ import annotations

import argparse
import atexit
import logging
import os
import sys

from . import __version__
from .autostart import PID_FILE
from .config import ConfigError, load_config, missing_account_fields
from .logger import setup_logging
from .single_instance import SingleInstance


def _remove_pid_file() -> None:
    try:
        PID_FILE.unlink()
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校园网自动认证后台服务")
    parser.add_argument("--test-captcha", action="store_true", help="抓一张验证码并识别，不提交")
    parser.add_argument("--once", action="store_true", help="执行一次检测/认证后退出")
    parser.add_argument("--check-config", action="store_true", help="只检查 config.json 是否填写完整")
    parser.add_argument("--console", action="store_true", help="同时输出到控制台")
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        logging.basicConfig(level=logging.ERROR)
        logging.error("%s", exc)
        return 2

    if args.check_config:
        missing = missing_account_fields(config)
        if missing:
            if sys.stdout is not None:
                print("CONFIG_MISSING: " + ", ".join(missing))
            return 3
        if sys.stdout is not None:
            print("CONFIG_OK")
        return 0

    console = args.console or sys.stdout is not None
    log = setup_logging(config, console=console)

    # 测试模式允许在服务运行时执行；其余模式必须保证单实例
    lock: SingleInstance | None = None
    if not args.test_captcha:
        lock = SingleInstance()
        if not lock.acquired:
            log.info("已有实例在运行，本进程退出")
            return 0

    # 延迟导入第三方依赖，保证 --check-config 在极简环境也能运行
    from .captcha import CaptchaSolver
    from .monitor import Monitor
    from .portal import PortalClient

    portal_config = config["portal"]
    portal = PortalClient(
        base_url=str(portal_config["base_url"]),
        student_id=str(config["account"]["student_id"]),
        username=str(config["account"]["username"]),
        password=str(config["account"]["password"]),
        timeout=float(config["monitor"]["request_timeout_seconds"]),
        isp=str(portal_config["isp"]),
        save_login_info=bool(portal_config["save_login_info"]),
    )

    captcha_config = config["captcha"]
    try:
        solver = CaptchaSolver(
            str(captcha_config["model_path"]),
            float(captcha_config["confidence_threshold"]),
        )
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 4

    if args.test_captcha:
        try:
            portal.begin_login()
            png = portal.fetch_captcha()
            expression, confidence = solver.read(png)
            answer = solver.solve(expression) if confidence >= solver.threshold else None
        except Exception as exc:
            log.error("验证码测试失败：%s", exc)
            return 5
        log.info("识别结果：%r（置信度 %.3f），答案：%s", expression, confidence, answer)
        return 0 if answer is not None else 1

    missing = missing_account_fields(config)
    if missing:
        log.error("config.json 尚未填写：%s，程序退出", "、".join(missing))
        return 3

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(_remove_pid_file)

    log.info("校园网自动认证服务 v%s 启动", __version__)
    monitor = Monitor(config, portal, solver, logger=log)
    if args.once:
        monitor.tick()
        return 0

    monitor.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
