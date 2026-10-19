"""PyInstaller 打包入口：无控制台窗口。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from campusnet.main import main

if __name__ == "__main__":
    raise SystemExit(main())
