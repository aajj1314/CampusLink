"""带控制台的启动入口，便于调试。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from campusnet.main import main

if __name__ == "__main__":
    arguments = sys.argv[1:]
    if "--console" not in arguments:
        arguments = ["--console", *arguments]
    raise SystemExit(main(arguments))
