# 02 · API 接口规范

> 版本：v1（本设计稿）。所有端点均在 LAN 监听，路径前缀 `/api/v1`。
> 认证：除标注「公开」外一律要求 `Authorization: Bearer <device_token>`。

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 传输 | HTTP/1.1，UTF-8 JSON；图像为二进制 body |
| 版本 | URL 路径版本。不兼容变更 → `/api/v2`，v1 至少共存 2 个发布周期 |
| 错误格式 | 统一 `{"error": {"code": "E_XXX", "message": "...", "retryable": true}}` |
| 时间 | 全部 ISO 8601 UTC（`2026-10-15T03:21:00Z`） |
| 幂等 | 写操作接受 `Idempotency-Key` 头；任务结果用 `nonce` 保证 |
| 限速 | 视觉端点每节点 1 并发（长轮询语义天然保证）；`/auth/*` 每设备 10 req/min |
| CORS | 不开放跨域（没有 Web 前端）；未来加 Web UI 时再评估 |

### 错误码总表

| HTTP | code | retryable | 场景 |
|---|---|---|---|
| 400 | `E_BAD_REQUEST` | 否 | 参数缺失/格式错 |
| 401 | `E_UNAUTHORIZED` | 否 | token 缺失/无效 |
| 403 | `E_FORBIDDEN` | 否 | token 有效但无此权限（如只读设备请求写操作） |
| 404 | `E_NOT_FOUND` | 否 | 资源不存在 |
| 409 | `E_DUPLICATE` | 否 | 重复提交（nonce 已用）/ 任务已被其它节点领取 |
| 410 | `E_TASK_EXPIRED` | 否 | 任务超过 TTL |
| 413 | `E_IMAGE_TOO_LARGE` | 否 | 超过图像尺寸限制 |
| 415 | `E_UNSUPPORTED_MEDIA` | 否 | 图像格式非 PNG/JPEG |
| 422 | `E_INVALID_RESULT` | 否 | 结果长度/字符集不符合任务要求 |
| 429 | `E_RATE_LIMITED` | 是 | 触发限速 |
| 500 | `E_INTERNAL` | 是 | 服务端异常 |
| 503 | `E_BUSY` | 是 | 无可用任务槽 / 认证进行中 |

## 2. 服务能力查询

### `GET /api/v1/capabilities`（公开）

客户端发现用：确认版本兼容、得知是否需要配对。

```json
{
  "protocol": "campuslink",
  "api_version": "1.0",
  "router_version": "0.1.0",
  "auth": {"mode": "router", "portal": "桂林信息科技学院", "state": "need_login"},
  "vision": {"accepting_nodes": true, "active_nodes": 1, "pending_tasks": 0},
  "features": ["vision_tasks", "manual_input", "pairing"],
  "pairing_required": true
}
```

### `GET /api/v1/health`（公开）

轻量探活，用于 Desktop 的降级决策。**不返回任何信息**，仅状态码 200。
客户端连续 3 次（间隔 2 s）失败即判定 Router 失联。

## 3. 设备配对与身份验证

一次性 PIN：Router 生成 6 位数字码（有效期 60 s、单次使用），设备用 PIN 换长期 `device_token`。

### `POST /api/v1/pair/start`（公开）

Router 上操作（由 Desktop/Mobile 弹窗让用户在 Router 侧确认，或 Router 无 UI 时由已配对设备代发）。

```json
请求: {}
响应 200: {"pin": "483920", "expires_at": "2026-10-15T03:22:00Z", "pair_url": "/api/v1/pair/confirm"}
```

### `POST /api/v1/pair/confirm`（公开，凭 PIN）

```json
请求: {"pin": "483920", "device_name": "我的手机", "device_kind": "mobile",
       "capabilities": ["ocr", "manual"]}
响应 200: {"device_token": "b3f1...", "device_id": "dev_7f3a",
           "token_expires_at": null,
           "note": "token 请妥善保存；可在路由器上随时吊销"}
响应 400: {"error": {"code": "E_BAD_REQUEST", "message": "PIN 错误或已过期"}}
```

约束：同一 PIN 只能用一次；每 Router 最多 8 台已配对设备。

### `DELETE /api/v1/pair/devices/{device_id}`（需已配对）

吊销某设备（含自己）。返回 204。被吊销设备的后续请求一律 401。

### `GET /api/v1/pair/devices`（需已配对）

列出已配对设备（id、名称、类型、最近活跃时间、能力）。

## 4. 认证会话（控制面，Desktop/Mobile 只读为主）

### `GET /api/v1/auth/status`

```json
{
  "state": "online",                    // unknown|offline|need_login|online|checking
  "portal_url": "http://10.6.6.6/login",
  "last_result": {"ok": true, "message": "认证成功", "at": "2026-10-15T03:20:11Z"},
  "session": {"established": true, "since": "...", "expires_hint": null},
  "account": {"student_id": "2353****416", "operator": "自动选择"},   // 掩码
  "auto_reconnect": true
}
```

**注意**：`account` 永远掩码输出；`session` 里**没有也不允许有** cookie 字段（见 03 文档的敏感边界）。

### `POST /api/v1/auth/login`

触发一次认证（Router 的认证编排器串行执行，进行中再调用返回 503 `E_BUSY`）。

```json
请求: {"wait": false}                    // true = 阻塞至完成（最长 60 s）
响应 202: {"task_id": "auth_9c1f", "state": "checking"}
```

### `GET /api/v1/auth/tasks/{task_id}`

查询一次认证的进度与结果（结构同 03 文档 `AuthTask`）。

### `POST /api/v1/auth/reconnect`

开关「断线自动重连」。`{"enabled": true}` → 200。

## 5. 视觉任务（数据面，Router ↔ 视觉节点）

任务由 **Router 的认证编排器**在需要 OCR 时创建，**不由外部创建**（无 POST 创建端点）。

### 任务生命周期

```
pending ──领取──> assigned ──提交──> submitted ──校验通过──> verified
   │                  │                │
   │超时TTL            │节点失联超时      │结果校验失败/长度不符
   ▼                  ▼                ▼
expired            pending(重新入队)   rejected ──(重新生成任务)──> pending

任意状态 <──显式取消── cancelled
```

- `TTL = 90 s`（门户验证码本身约 1~2 分钟失效，取保守值）
- `assigned` 后节点 30 s 未提交 → 任务回 `pending` 重新入队
- 认证流程超时（`max_retry` 用尽）→ 所有未完成任务转 `cancelled`

### `GET /api/v1/vision/tasks/next?wait=25`

**长轮询领取**。有任务立即返回；无任务挂起至多 `wait` 秒（默认 25，上限 55）。这是实现"每节点 1 并发"的机制：领到任务前不会再次领取。

```json
响应 200: {
  "task_id": "cap_a81e",
  "nonce": "9f3c...",                     // 提交时原样带回，防重放
  "created_at": "...",
  "expires_at": "...",
  "image_url": "/api/v1/vision/tasks/cap_a81e/image",
  "image_meta": {"format": "png", "width": 96, "height": 34, "bytes": 1204},
  "length": 4,                            // 期望字符数
  "charset_hint": "alnum",                // digits|lower|upper|alnum
  "allow_manual": false,                  // true 时客户端可展示人工输入 UI
  "strategies_hint": ["raw", "gray"]      // 与门户档案一致，节点可作为参考
}
响应 204: 无任务（长轮询超时）
```

### `GET /api/v1/vision/tasks/{task_id}/image`

返回图像二进制（`Content-Type: image/png`）。**一次性语义**：已过期/已取消返回 410。图像不在磁盘缓存（D5）。

### `POST /api/v1/vision/tasks/{task_id}/result`

```json
请求: {"nonce": "9f3c...", "text": "e5s3", "source": "ocr",
       "confidence": 1.0, "detail": {"raw": "e5s3", "gray": "e5s3"},
       "elapsed_ms": 240}
响应 200: {"accepted": true}
响应 409: {"error": {"code": "E_DUPLICATE", "message": "该任务已有结果"}}
响应 410: {"error": {"code": "E_TASK_EXPIRED"}}
响应 422: {"error": {"code": "E_INVALID_RESULT", "message": "期望 4 位字母数字"}}
```

`source` 取值：`ocr` | `manual`。`detail` 仅用于 Router 日志与统计，**不得包含**图像以外的敏感内容（节点没有敏感内容，这里是防御性约定）。

### `DELETE /api/v1/vision/tasks/{task_id}`

取消任务（节点侧主动放弃；仅 `assigned` 状态可取消，回 `pending`）。

### `GET /api/v1/vision/tasks/{task_id}`

查询任务状态（调试与节点恢复用）。

## 6. 系统状态（Desktop 主窗口展示用）

### `GET /api/v1/system/status`

```json
{
  "uptime_s": 86400,
  "network": {"state": "online", "local_ip": "10.6.6.88", "portal": "http://10.6.6.6/login"},
  "vision_nodes": [{"id": "dev_7f3a", "name": "我的手机", "busy": false, "last_seen": "..."}],
  "tasks": {"pending": 0, "verified_24h": 3, "failed_24h": 0, "manual_24h": 1},
  "storage": {"overlay_free_mb": 41.2}     // 用于预警 flash 写满
}
```

## 7. 关键约束的实现映射

| 任务书要求 | 协议上的落点 |
|---|---|
| 图像、任务 ID、Cookie、会话状态正确关联 | 任务由认证编排器创建时绑定到当次认证尝试（`auth_task_id` 内部关联，不外泄）；图像 URL 含 task_id 且一次性 |
| 不把 Cookie/密码交给视觉节点 | 视觉端点的响应结构里**不存在**这些字段（03 文档的 schema 层面保证）；图像一次性获取 |
| 会话过期 | `/auth/status` 的 `state` 即权威；任务 `expires_at` 与验证码时效对齐 |
| 重复提交 | `nonce` + 409 |
| 并发任务 | 每节点 1 并发（长轮询）、全局 pending ≤ 8 |
| 客户端离线 | 任务回 `pending` 重派；Router 自身超时兜底转 `cancelled` |
| 图像尺寸限制 | `E_IMAGE_TOO_LARGE` / `E_UNSUPPORTED_MEDIA`；Router 侧再校验实际解码尺寸 |
