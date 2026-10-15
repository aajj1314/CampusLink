# 契约变更提案日志（contract-change-log）

> 依据任务书「全局约束 2」：`02-API规范.md` 与 `03-核心数据结构.md` 是唯一事实源，
> 任何 Agent 不得直接修改；认为契约有误/不完备时，在本文件追加提案，交人工裁决后再改。
>
> 本文件由 Agent 1（Router 端）创建。

## 状态

**当前无待裁决提案。**

Agent 1 在实现过程中逐条核对 02/03 文档，未发现需要修改契约的差异。
以下是实现时所做的**非契约变更**澄清（不构成提案，仅记录实现选择，供后续 Agent 核对）：

| # | 事项 | 处理方式 | 是否改契约 |
|---|---|---|---|
| 1 | 图像尺寸约束（PNG/JPEG、≤150KB、宽≤400、高≤160）的**强制执行点**：契约无「上传图像」端点，任务图像由 Router 认证编排器从门户抓取后创建。 | 在 `TaskManager.create()` 处校验，不合规抛 `E_IMAGE_TOO_LARGE` / `E_UNSUPPORTED_MEDIA`；`RemoteVisionBackend` 捕获后返回空文本让 authenticator 重试。契约测试以 `images.validate()` + `create()` 抛错覆盖 413/415。 | 否 |
| 2 | `nonce` 的签发时机：03 文档将 `nonce` 列为 VisionTask 字段，但为满足「防重放」「无 nonce 复用」，实现为**每次领取（claim）时签发**，任务重派（assigned→pending）后下次领取换新 nonce。 | 领取响应含 nonce；提交时校验并立即作废。 | 否 |
| 3 | 「全局 pending ≤ 8」的不变式解释：实现为**在途（非终态）任务数 ≤ 8**（pending ⊆ 在途），避免 assigned 任务重派回 pending 时突破上限。 | `TaskManager.create()` 按在途数限流。 | 否 |
| 4 | `GET /health` 定义为「不返回任何信息，仅状态码 200」。 | **已裁决（2026-10-15）**：按契约正文返回 **200** 空体，实现已从 204 改为 200。 | 否 |
| 5 | 未知路径 `404` 与认证 `401` 的优先级：未认证请求打到未知路径时，实现返回 **404**（先判路由已知性，再判认证）。 | 02 文档未明示，按「资源不存在优先」处理。 | 否 |
| 6 | 敏感字段扫描口径：配对响应 `pair/confirm` 的 `device_token`/`token_expires_at`/`note` 为合法例外；其余响应体（含错误体）不得含 cookie/password/token 字样。 | 实现中错误文案也刻意规避「token」等词。 | 否 |

### 裁决记录

- **#4（2026-10-15）**：`/health` 返回 **200**。已按契约正文落实。

---

## Agent 2 追加（2026-10-15）

`OcrBackend` / `OcrResult` 协议的权威落点是 `campuslink/core/ocr_backend.py`（§A1）。
Agent 1 当时为「非阻塞」在 `campuslink/router/remotecomplete.py` 内自包含复刻了同签名
定义；Agent 2 落地后已做 reconcile —— `remotecomplete.py` 改为 `from campuslink.core.ocr_backend
import OcrBackend, OcrResult`，删除自包含定义。**不改契约**（字段逐一对齐 §A1，仅消除重复实现）。

另记录 `core/authenticator.py` 的一处实现性改动（属 §A1 明确授权的 Agent 2 改动）：
移除顶层 `from . import ocr as ocr_mod`，OCR 调用改走可注入的 `OcrBackend`
（默认 `LocalDdddocrBackend`，行为逐字段不变）。效果：`import campuslink.core`
不再连带 cv2/numpy/ddddocr，OpenWrt 上 core 可 import（视觉下沉到 Desktop/Mobile 的前提）。

**连带 reconcile**：Agent 1 的 `campuslink/router/auth.py` 原用运行时桥
`install_ocr_backend`（patch `authenticator.ocr_mod`）注入 RemoteVisionBackend。
Agent 2 移除 `ocr_mod` 后该桥失效（patch 一个不存在的变量，authenticator 会
退回本地 OCR）。已同步改为 `Authenticator(profile, ocr_backend=self._backend)`
直接传参，并删除 `install_ocr_backend`/`restore_ocr_module`/`_OcrFacade`
及 `router/__init__.py` 中的相应导出。**不改契约**。

---

## 提案 #1 · 凭据下发端点（方案 A）—— 已裁决：采纳

**背景**：`python -m campuslink.router` 的 `_wire_auth()` 从本地 `AppConfig` + keyring 读凭据。
OpenWrt 上无 keyring 后端 → 密码恒为空 → `/auth/*` 一直「未配置」。Router 端认证
无法真机跑通。需补一条凭据下发链路。

**方案 A（采纳）**：契约 v1 新增端点，仅允许**已配对**设备下发凭据，Router 只存内存、
重启即失（与「密码永不落盘」一致）。

### 契约变更（02 文档 §4 追加）

```
PUT /api/v1/auth/credentials     （需已配对设备 Bearer）
  请求: {"student_id": "...", "account": "...", "operator": "...", "password": "..."}
  响应 200: {"configured": true, "student_id": "2024****001", "operator": "..."}
  400: student_id 或 password 缺失
  401: 未配对/未认证

DELETE /api/v1/auth/credentials  （需已配对）
  响应 204：清空内存中的凭据
```

**安全约束**：
- 响应**永不回显密码**，只回脱敏的 student_id
- 密码写入前 `register_secret()` 脱敏登记；日志/错误体不得出现密码（沿用敏感字段扫描）
- 凭据只存内存，进程退出即失

### 实现落点

- `campuslink/router/auth.py`：新增 `AuthController.set_credentials()` / `clear_credentials()`
- `campuslink/router/apiserver.py`：新增 `do_PUT`、路由 `["auth","credentials"]`（PUT/DELETE）、
  加入 `_known_protected`、限速沿用 `/auth/*` 规则
- `tests/contract/test_credentials.py`：7 条契约测试（脱敏、401、400、DELETE、敏感扫描、404 优先）

### 待办（下游）

- Desktop 侧「把本机账号同步到路由器」入口（GUI 按钮 / visionnode client 方法）——
  建议随 Phase 2 联调一起做，非本提案阻塞项
- 02 文档正文同步更新（本提案记录即变更依据，待下次文档修订时并入）


