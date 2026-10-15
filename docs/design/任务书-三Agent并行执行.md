# CampusLink 跨平台落地 · 三 Agent 并行任务书

> 版本 1.0 · 2026-10-15
> 依据：`CampusLink/docs/design/`（00–06 已定稿，本任务书不重复设计内容，只做拆分与派工）
> 总体目标：实现跨平台协同的 **Phase 1（Router MVP）+ Phase 2（Vision API + Desktop 接入）+ Phase 3（Mobile）**，
> 使三平台能按 `docs/design/02-API规范.md` 定义的 `/api/v1` 契约完成配对、任务分发与认证托管。

---

## 0. 全局约束（三个 Agent 都必须遵守）

1. **Windows 稳定版不可破坏**：现有 44 条 pytest（`tests/test_portal.py`、`test_config_and_secrets.py`、`test_mock_e2e.py`）必须**零修改**保持全绿。任何 Agent 不得改动既有测试文件。
2. **契约冻结**：`docs/design/02-API规范.md` 与 `03-核心数据结构.md` 是唯一事实源。**任何 Agent 不得直接修改这两个文件**；认为契约有误或不完备时，在 `docs/design/contract-change-log.md`（自行创建）追加提案（问题描述 + 建议修改 + 影响面），交由人工裁决后再改。
3. **合规红线**：单账号、不规避门户并发终端限制（超限时如实透传错误）、不伪造 MAC / 设备指纹、不做账号共享。代码评审按此 grep 清单检查。
4. **敏感边界**（设计 03 文档 §5）：Portal Cookie 永不出 Router 进程内存；密码永不落盘；验证码图片不持久化；日志一律走 `campuslink.logging_setup`（自带脱敏），禁止 print。
5. **路径所有权**（防止合并冲突，越界改动一律拒绝）：

| 路径 | 所有者 | 其它 Agent |
|---|---|---|
| `campuslink/core/**`（除 `ocr_backend.py`） | **只读** | 禁改 |
| `campuslink/core/ocr_backend.py` | Agent 2 新建 | 只读（签名已冻结，见 §A） |
| `campuslink/router/**`、`router/**`、`tests/contract/**`、`tools/router_sim.py`、`campuslink/cli.py`（仅新增 `router` 子命令） | Agent 1 | 禁改 |
| `campuslink/visionnode/**`、`campuslink/gui/**`、`tests/test_ocr_backend.py`、`tests/test_visionnode.py` | Agent 2 | 禁改 |
| `mobile/**`（全新 Android 工程） | Agent 3 | 禁改 |
| `docs/design/**`、`requirements.txt` | 只读 | 依赖变更走提案，**禁止直接加依赖**（Agent 1 的 Router 依赖走 vendor，不进 requirements.txt） |

6. **人工门禁**：真机刷机（R2100）、48h 长稳、真实门户登录不在 Agent 职责内，由用户执行。各 Agent 的交付止于「模拟器/夹具环境全绿」。

---

## A. 冻结接口（三 Agent 共同依赖，签名不得更改）

### A1. OCR 后端协议（落点：`campuslink/core/ocr_backend.py`，所有者 Agent 2）

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol

@dataclass(frozen=True)
class OcrResult:
    text: str                 # 识别文本；失败/超时为空串
    confidence: float         # 0.0~1.0；manual 来源恒为 1.0
    detail: dict[str, str]    # 各策略输出，诊断用，无敏感内容
    source: str               # "local" | "remote" | "manual"
    elapsed_ms: int = 0

class OcrBackend(Protocol):
    def recognize(self, image: bytes, *, length: int,
                  strategies: tuple[str, ...]) -> OcrResult: ...
```

- Agent 2 实现 `LocalDdddocrBackend`（包装现有 `core/ocr.recognize/confidence`，行为与今天完全一致）。
- Agent 1 在 Router 侧实现 `RemoteVisionBackend`（由其 TaskManager 支撑：提交图像 → 同步等待结果，超时/失败返回空 `text`，让 `authenticator` 现有的「换一张重试」逻辑自然接管）。**该实现放在 `campuslink/router/` 内，不进 core。**
- `Authenticator.__init__` 增加可选参数 `ocr_backend: OcrBackend | None = None`（默认 None → 内部构造 Local），此改动属 Agent 2，且必须保证既有调用方零改动。

### A2. HTTP 契约

`docs/design/02-API规范.md` 全部端点、错误码、字段名。图像尺寸限制：PNG/JPEG、≤150 KB、宽 ≤400 px、高 ≤160 px。

### A3. 模型一致性

ONNX 模型文件（`common_old.onnx`）的 SHA-256 进入 `/capabilities` 的 `vision.model_hash`；Desktop 与 Mobile 加载的模型 hash 必须与 Router 声明一致，不一致时节点应在结果中标记 `"model_mismatch": true`。

---

## Agent 1 · Router 端（控制面）

### 任务背景与目的

Router 是整个协同系统的**唯一 Portal 会话持有者**与任务分发中枢。当前它不存在——需要把现有 `core/`（portal / netdetect / authenticator）移植到 OpenWrt（mipsel）可运行形态，并在其上实现 `/api/v1` 服务端、任务管理器与设备配对。这是 Phase 1 + Phase 2 的服务端部分，也是另外两个 Agent 的联调依赖。

### 具体工作内容

1. **依赖 vendor 化**：将 `requests`、`urllib3`、`bs4`（纯 Python 部分）vendor 进 `campuslink/router/_vendor/`；`lxml` 走 OpenWrt feed 包，缺失时 bs4 退回 `html.parser`（写明开关）。禁止在设备上跑 pip。
2. **`campuslink/router/` 包**：
   - `apiserver.py`：`/api/v1` 全部端点（标准库 `http.server` + `ThreadingHTTPServer` 或 asyncio，**不引第三方框架**），含长轮询、Bearer 校验、限速（`/auth/*` 每设备 10 req/min）
   - `taskmanager.py`：任务生命周期状态机（pending→assigned→submitted→verified / rejected / expired / cancelled），TTL 90 s、assigned 后 30 s 未提交回 pending、nonce 幂等、全局 pending ≤ 8、图像仅内存
   - `pairing.py`：6 位 PIN（60 s 单次有效）、token 签发（只存 SHA-256）、设备列表、吊销、错误 5 次锁定 5 min
   - `remotecomplete.py`：实现 `OcrBackend` 协议的 `RemoteVisionBackend`（对接 TaskManager）
   - `autostart` 不适用——Router 用 init 脚本
3. **`router/` 部署目录**：OpenWrt init 脚本（procd）、安装说明、tmpfs 日志配置（日志/队列/图像全部不落 flash）
4. **`tools/router_sim.py`**：**PC 模拟器**——同一套 API server 在 PC 上跑，Portal 侧对接现有 `mock/server.py` 思路的模拟门户。这是 Agent 2/3 的联调依赖（见 §C 协作机制）
5. **`campuslink/cli.py`** 新增子命令：`campuslink router-serve [--sim] [--port 7700]`
6. **`tests/contract/`**：契约测试套件——schema 快照、状态机属性测试（随机操作序列后断言终态不可逆 / nonce 单次 / 单节点单任务）、**敏感字段自动扫描**（全部响应体不得含 cookie/password/token 字样，配对响应除外）

### 输入与依赖

- 输入：`docs/design/02/03/04`（契约与预算）、`campuslink/core/**`（只读复用）
- 依赖：无外部 Agent 阻塞——`OcrBackend` 协议签名已在 §A 冻结，可先行编码；Agent 2 的 `ocr_backend.py` 落地前，用本地 stub 实现同一协议
- 硬件（R2100 真机）不在依赖内：全部功能必须先在 `router_sim` 模式下验证

### 预期交付物

- `campuslink/router/**`（含 vendored 依赖）
- `router/`（init 脚本 + 部署文档）
- `tools/router_sim.py` + `campuslink/cli.py` 的 `router-serve` 子命令
- `tests/contract/**`（≥ 25 条：每端点 ≥ 2 条 + 状态机属性测试 + 敏感扫描）
- `docs/design/contract-change-log.md`（如无提案则写「无」）

### 验收标准

- [ ] `pytest tests/contract` 全绿（≥ 25 条），覆盖 02 文档全部端点
- [ ] `router_sim` 模式下：配对 → 创建认证任务 → 模拟门户认证成功 全链路走通
- [ ] 敏感字段扫描零命中；token 重放返回 401；PIN 第 6 次错误触发锁定
- [ ] 任务并发不变式：属性测试随机 500 步操作后无终态回退、无 nonce 复用
- [ ] 图像超限返回 413/415；过期返回 410；重复提交返回 409
- [ ] 现有 44 条测试仍全绿（`core/**` 未被修改）
- [ ] PC 模拟器空载 RSS ≤ 80 MB（为真机 128 MB 留余量的间接指标）

---

## Agent 2 · Windows 端（视觉面 + 兼容面）

### 任务背景与目的

Desktop 是唯一已存在并稳定运行的端。本任务给它加**可选的视觉节点角色**（把现有 OpenCV + ddddocr 能力暴露给 Router），并实现「Router 托管 / 独立」双模式与自动降级。红线：现有 GUI 行为与 44 条测试**零回归**。

### 具体工作内容

1. **`campuslink/core/ocr_backend.py`**（§A 签名）+ `LocalDdddocrBackend`：包装 `core/ocr.recognize()`；`Authenticator` 加可选 `ocr_backend` 参数，默认行为不变
2. **`campuslink/visionnode/` 包**：
   - `client.py`：API 客户端（mDNS 发现 `zeroconf` 可选——若引入依赖需先提案；未引入前手输 IP）、配对（PIN 输入对话框流程）、长轮询领任务、提交结果（带 nonce）
   - `node.py`：节点主循环——领任务 → `LocalDdddocrBackend.recognize()` → 提交；退出/网络错误时不留悬空任务
   - `watcher.py`：Router 可达性监测（`/health` 连续 3 次失败 = 失联）
3. **GUI 集成**（`campuslink/gui/`）：设置区新增「作为视觉节点连接 Router」开关（**默认关闭**）、Router 地址、配对入口；主窗口状态区显示当前模式（独立 / Router 托管 / 失联降级中）
4. **托管模式行为**：Router 托管时**禁用本地「立即连接」**（按钮置灰 + 提示"由 Router 托管"），避免双登录触发门户并发限制；本地认证决策权交给 Router
5. **降级逻辑**：托管中 Router 失联 → 自动恢复独立模式并通知用户；Router 恢复 → 重新托管（防抖：状态切换至少间隔 10 s）
6. **测试**：`tests/test_ocr_backend.py`（Local 实现与旧 `ocr.recognize` 输出逐字段一致）+ `tests/test_visionnode.py`（配对/轮询/提交/降级，对 `router_sim` 或本地 stub）

### 输入与依赖

- 输入：§A1 签名、02 文档视觉端点、`router_sim`（Agent 1 产出）
- **非阻塞保障**：Agent 1 的模拟器未就绪时，用 `tests/` 下自建的 stub server 开发；合并联调以模拟器为准
- 硬约束：**禁止在 `requirements.txt` 增加任何依赖**（`zeroconf` 走提案）

### 预期交付物

- `campuslink/core/ocr_backend.py` + `Authenticator` 的可选参数改动
- `campuslink/visionnode/**`
- GUI 设置区与状态显示改动
- `tests/test_ocr_backend.py`、`tests/test_visionnode.py`（合计 ≥ 15 条）
- 用户手册增补章节（`docs/用户手册.md` 追加「Router 模式」一节）

### 验收标准

- [ ] 现有 44 条测试**零修改**全绿；`test_ocr_backend` 证明 Local 实现输出与旧路径逐字段一致
- [ ] 视觉节点开关默认关闭；关闭时 GUI 行为与当前版本完全一致
- [ ] 对 `router_sim`：配对 → 领任务 → 识别 → 提交 → Router 认证成功，端到端走通
- [ ] 降级：模拟器 kill 后 6 s 内回独立模式；恢复后重新托管；10 s 防抖生效
- [ ] 托管模式下「立即连接」被阻止且有明确提示
- [ ] 节点退出时不遗留 `assigned` 状态任务（主动取消）

---

## Agent 3 · Mobile 端（视觉面 + 人工兜底）

### 任务背景与目的

Mobile 是第三个视觉节点，独特价值是**人工输入兜底**：Desktop OCR 失败时，任务带 `allow_manual=true` 转给手机，人眼看图输字比反复重试快得多。本任务交付一个可安装的 Android 客户端，联调对象是 `router_sim`。

### 具体工作内容

1. **工程骨架**：`mobile/` 下 Kotlin + Gradle 工程，minSdk 26，目标 arm64-v8a
2. **网络层**：OkHttp 实现 02 文档客户端——配对（PIN 表单）、长轮询（`/vision/tasks/next?wait=25`，OkHttp 超时配置 ≥ 30 s）、结果提交（nonce）、`/auth/status` 只读展示
3. **人工输入路径（P0 优先级）**：收到 `allow_manual=true` 任务 → 全屏展示验证码图 + 4 位输入框 → 提交（`source: "manual"`，confidence 1.0）
4. **OCR 路径（P1）**：opencv-android + onnxruntime-mobile 加载与 Desktop 同源的 `common_old.onnx`（随 APK 打包），实现 `raw`/`gray` 两策略 + 投票；`model_hash` 校验，不一致时结果标记 `model_mismatch`
5. **安全与合规**：device_token 存 Android Keystore（EncryptedSharedPreferences）；**禁止**将验证码图片写入媒体存储/外部存储；仅 LAN 明文 HTTP（`networkSecurityConfig` 精确放行，不全局开 cleartext）
6. **生命周期**：不做常驻保活；前台 Service 仅在"参与任务分发"开关打开时运行；被杀后 Router 侧任务 30 s 自动重派（无需 App 补偿）

### 输入与依赖

- 输入：§A2/A3 契约、02 文档、`router_sim`（Agent 1 产出）
- **非阻塞保障**：无 Router 时用自建 MockWebServer（OkHttp 自带）按 02 文档模拟端点开发；模型文件未就绪时 OCR 路径可用随机占位实现先行打通流程
- 与 Agent 2 无代码依赖（不同仓库目录、不同语言），仅共享契约

### 预期交付物

- `mobile/` 可构建出 APK 的完整 Gradle 工程
- 人工输入路径端到端可用（对 `router_sim`）
- OCR 路径可用（P1，允许在验收时以 feature flag 关闭）
- `mobile/README.md`：构建说明、签名说明、与 Router 联调步骤
- 单元测试：状态机映射、nonce 处理、超时重试（≥ 10 条，JVM 本地测试即可）

### 验收标准

- [ ] `./gradlew assembleDebug` 产出 APK；`testDebugUnitTest` 全绿
- [ ] 对 `router_sim`：配对 → 领任务 → **人工输入** → 提交 → Router 认证成功，端到端 < 15 s
- [ ] OCR 路径：同一模型下识别结果与 Desktop ±5% 以内（用 `tests/fixtures/captcha_sample.png` 等固定样本比对）
- [ ] App 被杀后，Router 侧任务 30 s 内转为 pending 重派（无泄漏）
- [ ] Keystore 外无法读取 token（卸载重装需重新配对）
- [ ] 验证码图片不出现在相册/下载目录（自动化检查）

---

## 三 Agent 协作机制

### C1. 接口与交付顺序

```
契约冻结（已完成，输入）
   │
   ├─ Agent 2：core/ocr_backend.py 最先合入（改动最小，是 A1 的编译依赖）
   ├─ Agent 1：router_sim 尽早出第一个可运行版本（建议第 1 周内）→ 通知 2/3 切换联调
   └─ Agent 3：MockWebServer 自给自足，不被任何人阻塞
合并顺序：2 的 ocr_backend → 1 的 router 包 → 联调 → 3 的 APK
```

### C2. 变更仲裁

- 契约（02/03 文档、§A 签名）修改只能经 `contract-change-log.md` 提案 → 人工裁决 → 由提案发起 Agent 以独立 commit 实施 → **广播给另外两个 Agent 确认**。
- 两个 Agent 需要同一处改动时（例如都想给 02 文档加字段），以先提案者为准，后到者 append 自己的用例到该提案下。

### C3. 联调节奏

| 时点 | 事件 |
|---|---|
| T0 | 三 Agent 并行启动（1 做服务端与模拟器，2 做接缝与节点，3 做骨架与人工路径） |
| T1（约 1/3 进度） | Agent 1 交付 `router_sim` v0（可配对、可出模拟任务）→ 2/3 切换真联调 |
| T2（约 2/3 进度） | 三方端到端联调：sim 门户 + Desktop 节点 + Mobile 人工路径 |
| T3 | 各自验收清单自检 → 人工门禁（真机刷机、真实门户）由用户执行 |

### C4. 明确不做（全局）

- Vision Lite（Phase 4）——不在本次范围
- 真机刷机与真实门户登录验证——人工门禁
- 修改 Windows 稳定版的既有行为与既有测试
- 任何对公网服务的依赖
