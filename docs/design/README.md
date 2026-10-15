# CampusLink 跨平台架构设计

> 版本 1.0 · 2026-10-15 · 阶段：设计定稿（未实现）
> 任务书：CampusLink 跨平台架构设计（CampusLink Router / Desktop / Mobile / Vision Lite）

## 文档索引

| 文档 | 内容 |
|---|---|
| [00-总体架构.md](00-总体架构.md) | 现状盘点（含对任务书假设的核对修正）、三层架构、8 项核心决策、合规边界 |
| [01-模块职责.md](01-模块职责.md) | Router / Desktop / Mobile / Vision Lite 各自做什么、明确不做什么 |
| [02-API规范.md](02-API规范.md) | `/api/v1` 全端点、配对鉴权、任务生命周期、错误码、图像约束 |
| [03-核心数据结构.md](03-核心数据结构.md) | Task / VisionNode / DeviceIdentity / SessionRef、状态机、敏感信息边界 |
| [04-平台依赖.md](04-平台依赖.md) | 各平台依赖清单、Router 的 flash / RAM 预算实测推算 |
| [05-研发路线图.md](05-研发路线图.md) | Phase 0–4、每阶段验收标准、停止点 |
| [06-风险评估与测试.md](06-风险评估与测试.md) | 风险矩阵（10 项）、测试策略（含现有 44 条测试的复用方式） |

## 一页纸结论

1. **现有代码的解耦程度比任务书假设的好**：`core/` 无 Qt、无 Windows API，`cv2`/`numpy` 只被 `ocr.py` 引用，OCR 调用点全项目只有 1 处（`authenticator.py:177`）。跨设备拆分的**唯一代码改动**是给这一处加一个 `OcrBackend` 接口（本地实现 / 远程节点实现），协议层 `portal.py` 原样复用。
2. **Router 是唯一持有 Portal 会话的节点**。Cookie 只存在于 Router 进程内存，视觉节点只见图片、只回文本，永不接触会话与凭据。
3. **Vision Lite 的诚实结论：现有视觉栈（ddddocr + onnxruntime + opencv）在 MT7621A 上不可行**——onnxruntime 无 mipsel 官方构建，RAM 预算也不够。可行方向只有纯经典 CV，投入产出比低，列为最低优先级；视觉计算下沉到 Desktop / Mobile 才是正解。
4. **合规边界写死在设计里**：Router 模式仍用用户自己的账号、仍受门户并发终端数限制；不做多账号、不做 MAC 轮换、不做账号共享。并发超限时把错误如实报给用户。
5. **不在本阶段写任何实现代码**。Windows 稳定版不动，新增内容全部落在新模块与文档。
