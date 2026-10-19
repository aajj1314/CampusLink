# -*- coding: utf-8 -*-
"""验证修复：pythonw 下 ensure_console 应返回 True 且 stdout 可写。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from campuslink import cli_headless as H

steps = []
ok = H.ensure_console()
steps.append(f"ensure_console={ok}")
steps.append(f"stdout={sys.stdout!r}")

result = "FAIL"
if ok and sys.stdout is not None:
    try:
        sys.stdout.write("[verify] 中文写入正常\n")
        sys.stdout.flush()
        result = "PASS"
    except Exception as e:
        steps.append(f"write exc: {e!r}")
else:
    steps.append("stdout 仍不可用")

steps.insert(0, f"RESULT={result}")
Path("_diag_result.txt").write_text("\n".join(steps), encoding="utf-8")
