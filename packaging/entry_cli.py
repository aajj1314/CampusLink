# -*- coding: utf-8 -*-
"""CampusLinkCLI 的打包入口：无人值守认证，跑完即退。

bootstrap 阶段（import / main 早期）的异常也要留痕 —— windowed 下
这些异常只会变成「闪退」，没有任何窗口能看。全写进启动追踪文件。
"""
import sys
import traceback
from pathlib import Path


def _bootstrap_trace(msg: str) -> None:
    try:
        from campuslink import paths
        from datetime import datetime
        p = paths.data_dir() / "cli_startup_trace.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%m-%d %H:%M:%S")
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {msg}\n")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        from campuslink.cli_headless import main
    except Exception:
        _bootstrap_trace("import 阶段崩溃：\n" + traceback.format_exc())
        raise
    try:
        code = main()
    except Exception:
        _bootstrap_trace("main 未捕获异常：\n" + traceback.format_exc())
        raise
    _bootstrap_trace(f"进程退出，退出码 {code}")
    sys.exit(code)
