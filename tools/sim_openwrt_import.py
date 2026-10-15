# -*- coding: utf-8 -*-
"""
模拟 OpenWrt 环境验证：剥离 site-packages（假装没有 requests 等），
只留标准库 + 项目源码 + vendored 依赖，确认 `import campuslink.router`
能成功 —— 这就是真机上 "No module named 'requests'" 的复现与回归测试。
"""
import os
import sys

# 1) 假装没有 pip 装过任何东西：把 site-packages 从 sys.path 摘掉
sys.path = [p for p in sys.path
            if "site-packages" not in p and "dist-packages" not in p]

# 2) 项目根加进 path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 3) 模拟 procd 设的环境变量
os.environ["CAMPUSLINK_VENDOR"] = "1"

# 4) 现在导入 —— 若 vendor 注入时机对，requests 会从 _vendor/packages 解析
import campuslink.router  # noqa: E402
import requests  # noqa: E402

vendor_marker = "router/_vendor/packages/requests"
assert vendor_marker in requests.__file__.replace("\\", "/"), \
    f"requests 没有从 vendor 解析，而是 {requests.__file__}"

import urllib3  # noqa: E402
assert "router/_vendor/packages/urllib3" in urllib3.__file__.replace("\\", "/")

from campuslink.router import ApiServer, AuthController, RouterContext, TaskManager  # noqa: E402

# 5) 确认控制面能真正构造（不含 core 的认证部分也应能起）
tm = TaskManager()
ctx = RouterContext(taskmanager=tm, auth=AuthController(tm), portal_name="test")
print("OK: 无 site-packages 环境下 campuslink.router 正常导入")
print("     requests 来源:", requests.__file__)
print("     urllib3 来源 :", urllib3.__file__)
print("     RouterContext 构造成功，端口待绑定")
