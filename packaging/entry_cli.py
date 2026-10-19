# -*- coding: utf-8 -*-
"""CampusLinkCLI 的打包入口：无人值守认证，跑完即退。"""
import sys

from campuslink.cli_headless import main

if __name__ == "__main__":
    sys.exit(main())
