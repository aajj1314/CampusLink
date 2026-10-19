"""无窗口启动入口（开机自启使用）。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from campusnet.main import main

if __name__ == "__main__":
    raise SystemExit(main())
