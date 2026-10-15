# CampusLink Router · 部署说明（OpenWrt）

> 对应任务书 Agent 1 交付物「`router/` 部署目录」。目标硬件：小米 AC2100（MT7621A / mipsel_24kc / 128 MB RAM / 128 MB NAND / 无 USB）。

## 1. 目录布局（/opt/campuslink）

```
/opt/campuslink/
├── campuslink/            # 代码树（含 router/ 与 _vendor/packages）
│   └── router/_vendor/packages/   # requests/urllib3/bs4 纯 Python 依赖
└── etc/campuslink/        # 低频配置（设备表、门户档案）—— 唯一写 flash 处
```

- 依赖 vendoring 由 `tools/vendor_deps.py` 在**构建机**上生成（不在设备上跑 pip）。
- `python3-lxml` 走 OpenWrt feed：`opkg install python3-lxml`；feed 缺失时
  `_vendor.install()` 会自动把 bs4 的 `"lxml"` 退回 `html.parser`（本门户页面仅 ~5 KB，性能无碍）。

## 2. 安装步骤（真机，人工门禁）

```sh
# 1) 依赖（OpenWrt feed）
opkg update
opkg install python3-light python3-logging python3-json python3-urllib python3-lxml

# 2) 代码树（从构建机拷贝，含 vendored 依赖）
scp -r campuslink root@ROUTER:/opt/campuslink/

# 3) 服务脚本
cp router/campuslink.init /etc/init.d/campuslink
chmod +x /etc/init.d/campuslink
/etc/init.d/campuslink enable
/etc/init.d/campuslink start

# 4) 验证
curl -s http://127.0.0.1:7700/api/v1/health        # 期望 200
curl -s http://127.0.0.1:7700/api/v1/capabilities  # 期望 JSON
```

## 3. tmpfs 与 NAND 保护（决策 D5/D8）

- 日志、任务队列、验证码图片**全部在内存/tmpfs**（`HOME=/tmp/campuslink`），不写 flash。
- 写 flash 的只有：配对/吊销设备（`/etc/campuslink/devices.json`）、门户档案、开关配置 —— 均为低频人工操作。
- `/api/v1/system/status` 的 `storage.overlay_free_mb` 用于预警 flash 写满。

## 4. 认证编排的接缝（重要）

控制面（配对 / 视觉任务 / 状态查询）只依赖标准库，OpenWrt 上**立即可用**。
认证编排需要 `core.Authenticator`，而 `core` 当前的 OCR 调用硬依赖
`cv2/numpy/ddddocr`（OpenWrt 上没有）——这是 **Agent 2 的 `core/ocr_backend.py`
交付项**要解掉的（把 OCR 调用改走可注入的 `OcrBackend`）。落地后：

- `python -m campuslink.router` 的 `_wire_auth()` 会自动加载配置档案 + 凭据，
  认证时由 RemoteVisionBackend 经 `/api/v1/vision/tasks/*` 分发给 Desktop/Mobile 节点。
- 在此之前，`/auth/*` 返回「未配置」，其余端点照常 —— 与任务书
  「Agent 1 非阻塞、Agent 2 的 ocr_backend 落地前用本地 stub 实现同一协议」一致。

## 5. 密码与凭据（03 文档 §5）

- 密码**永不落盘**：由 Desktop/Mobile 每次启动下发一次，仅存 Router 内存。
- device_token 只存 SHA-256（`/etc/campuslink/devices.json`），泄漏配置文件也拿不到有效凭据。
- Portal Cookie 永不出 Router 进程内存。

## 6. 常用命令

```sh
# 本地 PC 先跑模拟器联调（Agent 2/3 依赖）
python -m campuslink.cli router-serve --sim --demo-node --port 7700

# 触发一次认证（用已配对设备的 token）
curl -X POST -H "Authorization: Bearer <token>" \
     http://127.0.0.1:7700/api/v1/auth/login -d '{"wait": false}'

# 看认证状态
curl -H "Authorization: Bearer <token>" http://127.0.0.1:7700/api/v1/auth/status
```
