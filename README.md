# CampusLink 校园网认证客户端

把原来那个命令行版的校园网自动登录工具，做成**双击就能用、装在系统里的 Windows 程序**。

- 图形界面（PySide6），面向完全没有编程基础的用户
- 密码存进 **Windows 凭据管理器**，不落明文、不进日志
- 常驻**系统托盘**，断线自动重新认证
- 打包成 **CampusLink_Setup_v1.0.exe**，双击安装，不需要装 Python

> **本项目是在原有可运行版本基础上做的增量开发。**
> 原项目（`../captcha-login/`）完整保留、未做任何修改，并已备份到
> `../_backup/captcha-login-<时间戳>/`。认证逻辑是从那边**迁移**过来的，不是重写。

---

## 1. 目录结构

```
CampusLink/
├── campuslink/                源码包
│   ├── core/                  认证核心（平台无关）
│   │   ├── models.py          数据模型：凭据 / 结果 / 门户档案
│   │   ├── ocr.py             验证码识别（多策略 + 投票）
│   │   ├── portal.py          门户协议层：解析登录页、提交、判定
│   │   ├── authenticator.py   认证编排：重试、验证码去重、取消
│   │   ├── netdetect.py       网络状态检测 + 门户发现
│   │   └── exceptions.py      异常体系（面向用户的文案）
│   ├── gui/                   图形界面
│   │   ├── app.py             应用入口、单实例检测
│   │   ├── main_window.py     主窗口
│   │   ├── worker.py          后台线程：认证 / 监测 / 预热
│   │   ├── tray.py            系统托盘
│   │   ├── style.py           主题、QSS、程序化图标
│   │   └── assets/            app.ico / app.png
│   ├── profiles/              各校门户档案（换学校只改这里）
│   ├── config.py              用户配置（JSON，不含密码）
│   ├── secrets.py             密码存储（系统凭据管理器）
│   ├── autostart.py           开机启动
│   ├── logging_setup.py       日志 + 敏感信息脱敏
│   ├── paths.py               路径解析
│   └── cli.py                 命令行入口（无 GUI 也能用）
├── packaging/                 打包相关
│   ├── CampusLink.spec        PyInstaller 规格
│   ├── CampusLink.iss         Inno Setup 安装脚本
│   ├── entry_gui.py           打包入口
│   ├── version_info.txt       exe 版本资源
│   ├── build_exe.bat          打包成 exe
│   └── build_installer.bat    做成安装程序
├── tests/                     pytest 测试（44 条）
├── tools/                     辅助脚本（图标、冒烟测试）
└── docs/                      用户手册、依赖说明、验收报告
```

---

## 2. 架构：为什么这么分层

```
        ┌──────────────────────────────┐
        │  gui/     PySide6            │   只有这一层允许 import Qt
        │  主窗口 / 托盘 / 后台线程      │
        └──────────────┬───────────────┘
                       │ 只通过数据对象和回调交互
        ┌──────────────▼───────────────┐
        │  config / secrets / profiles │   配置与凭据
        └──────────────┬───────────────┘
                       │
        ┌──────────────▼───────────────┐
        │  core/    平台无关            │   不 import Qt、不 import winreg
        │  ocr / portal / authenticator │
        │  netdetect / models           │
        └──────────────────────────────┘
```

**core 与 GUI 的解耦不是口头约定，是有测试守着的：**

```python
# tests 里断言：只导入 core 时，sys.modules 里不应出现任何 Qt 模块
import campuslink.core.authenticator
assert not [m for m in sys.modules if "PySide6" in m]
```

这也是为**将来移植到 OpenWrt 预留的条件** —— 那边只需要 `core/`，
把 `ocr.py` 换成适合嵌入式环境的实现（或直接去掉验证码环节）即可，
`portal.py` / `authenticator.py` / `netdetect.py` 都能原样复用。

---

## 3. 快速开始

### 3.1 开发环境

```bat
cd CampusLink
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

依赖分三组（详见 `docs/依赖说明.md`）：认证核心、GUI、凭据存储。

### 3.2 跑起来

```bat
:: 图形界面
.venv\Scripts\python -m campuslink.gui

:: 命令行（不需要 Qt，用来排查问题）
.venv\Scripts\python -m campuslink.cli info
.venv\Scripts\python -m campuslink.cli netcheck
.venv\Scripts\python -m campuslink.cli dry-run
.venv\Scripts\python -m campuslink.cli login
```

### 3.3 测试

```bat
.venv\Scripts\python -m pytest            :: 44 条
.venv\Scripts\python tools\smoke_gui.py   :: GUI 无头冒烟
```

### 3.4 打包

```bat
packaging\build_exe.bat            :: -> dist\CampusLink\CampusLink.exe
packaging\build_installer.bat      :: -> packaging\output\CampusLink_Setup_v1.0.exe
```

**Inno Setup 6 需要单独安装**（`build_installer.bat` 会检测并给出下载地址，
不会自己装东西）：<https://jrsoftware.org/isdl.php>

---

## 4. 关键设计决策（以及为什么）

这一节记录的是踩过坑之后定下来的做法，改之前请先看理由。

### 4.1 认证核心：每次都要重新解析登录页

该门户是 ASP.NET WebForms，`__VIEWSTATE` / `__EVENTVALIDATION` 每次加载都变；
Panabit 系门户还有 `t` / `sccpTimestamp` 这类一次性令牌。
**把隐藏域写死在配置里提交必然失败**，所以 `portal.py` 每次都重新 GET 登录页、
把表单字段全部原样带回。

配套的两个细节：
- **提交按钮必须带上**（`<input type="submit" name=...>`）。ASP.NET 靠它判断
  点了哪个按钮，漏掉整条回发不触发。所以 `_parse_fields` 不排除 `submit`。
- 另有 `mode=api` 的退路（门户是纯 JS 拼包、没有 `<form>` 时用），见旧版 README。

### 4.2 验证码：不刷新时的去重（本项目最关键的一段）

实测该门户的 `/CheckCode`：

| 操作 | 结果 |
|---|---|
| 同一 Session 连续请求 | **返回同一张图** |
| 重新 GET `/login` 之后再取 | 会换 |
| 两次尝试落在同一时间粒度内 | 仍可能撞到同一张 |

不做处理的话，「识别错 → 重试 → 拿到同一张错图 → 又识别错」是**死循环**。
`authenticator.py` 的做法：给验证码图片算 md5，**发现重复就换一个新会话 + 等 1.3 秒重取**，
最多 4 轮。`tests/test_mock_e2e.py::test_captcha_dedup_forces_new_session` 专门验证这条路径。

### 4.3 成功判定：只能直接匹配文案

该门户**成功和失败共用同一个提示容器** `<p class="validation-summary-errors">`，
而且**成功时登录表单照旧渲染、不跳转**：

| 响应 | 容器内容 | 表单还在？ |
|---|---|---|
| 成功 | `联网成功!` | 在 |
| 验证码错 | `验证码错误!` | 在 |
| 未实名 | `用户未实名注册运营商上网账号!` | 在 |

所以任何「容器为空」「表单消失」这类**推断式**条件都会误判（旧版真的因为这个
把 7 次成功全报成了失败）。现在配置里就是一句
`{"type": "contains", "value": "联网成功"}`。

### 4.4 网络状态：不能看 TCP 通不通

强制门户会**劫持所有 :80 连接并由网关自己代答**，TCP 握手百分百成功 ——
那是网关在回你，不是互联网。旧版因此把「未认证」误报成「已联网」。

可靠判据只能是 **HTTP 响应内容**：`generate_204` 真联网时返回 204 且 body 为空。

### 4.5 门户发现：不能只靠劫持重定向

该门户**原地返回登录页、不做 302**，所以「看请求被重定向到哪里」这条路抓不到东西。
`PortalProfile` 因此多了 `portal_candidates`：**已知地址优先探，自动发现兜底**。

### 4.6 密码：结构上就没有存放位置

`AppConfig` 里**没有任何密码字段** —— 不是靠"记得别写进去"的约定。
密码走 `keyring` → Windows 凭据管理器（条目名 `CampusLink:<门户>:<账号>`，
带门户名是为了换学校时互不覆盖）。

**没有可用凭据后端时，宁可不保存，也不退化成明文文件**，程序会明确告诉用户
「这台电脑无法安全保存密码，每次启动需要重新输入」。

### 4.7 日志脱敏：两层防护

任务书要求「禁止将密码、Token、Cookie 等输出到日志」。光靠写代码时小心不够，
`logging_setup.py` 做了两层：

1. **运行时登记**：拿到密码/Cookie 的那一刻调用 `register_secret(value)`，
   之后任何日志里出现这个字符串都会被替换成 `***`。即使某处代码不小心把
   整个 payload 打出来，密码也不会泄漏。
2. **正则兜底**：`password=` / `token=` / `cookie=` 这类键值对模式替换。

`tests/test_config_and_secrets.py::test_log_file_never_contains_password`
是端到端验证：写下含密码的日志，再读文件确认密码不在里面。

### 4.8 开机启动：注册表 Run 键

**不用**「启动文件夹 + 快捷方式」，**也不用**计划任务：

- 比快捷方式简单：不需要生成 `.lnk`（那要么依赖 pywin32，要么调 VBScript，
  而 VBScript 在新版 Windows 上正在被移除）
- 比 `schtasks` 可靠：旧版用计划任务，结果在装了终端管控软件的电脑上直接被拦
- 没有黑框闪现：GUI 程序（`console=False`）从 Run 键拉起不会出现控制台窗口
- 用户态即可，不需要管理员权限
- 位置固定好排查：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`

安装程序和程序内开关写的是**同一个键**，所以两边状态天然一致。

### 4.9 线程模型

认证要发多个 HTTP 请求、还可能重试 8 次每次隔 1.5 秒，最长几十秒；
网络检测最长等 6 秒；ddddocr 首次加载模型要 1～2 秒。**这三件事都不能放主线程**。

| 线程 | 职责 | 生命周期 |
|---|---|---|
| `AuthWorker` | 一次认证，可取消 | 每次点「立即连接」新建 |
| `MonitorWorker` | 周期检测网络、触发自动重连 | 常驻 |
| `WarmupWorker` | 预热识别模型 | 启动时跑一次 |

线程之间**只通过 Qt 信号通信，不共享可变状态**，避免竞态。

### 4.10 打包：onedir，不用 onefile

onefile 每次运行都要把几百 MB 解压到 `%TEMP%`，启动要十几秒。
本项目是常驻托盘的程序，onedir 启动不到 2 秒，明显更合适。

另外**必须 `collect_all` ddddocr / onnxruntime / cv2** ——
它们的模型和原生 DLL 是 data 文件，PyInstaller 的静态分析看不见，
不收集的话编译能过、运行时才报 `No such file or directory: common.onnx`。

> **一个容易搞反的地方**：ddddocr 的 `beta=False`（默认）实际加载的是
> **`common_old.onnx`**，不是 `common.onnx`。源码里是
> `if old: ... elif beta: common.onnx else: common_old.onnx`。
> 名字极具迷惑性，别看着「old」就以为可以删。

---

## 5. 已知限制

| 限制 | 说明 |
|---|---|
| 只支持 Windows | `autostart.py` 用注册表；core 本身是跨平台的 |
| 只内置了一所学校的门户档案 | 换学校需要新增 `profiles/*.json`，代码不用改 |
| 未做代码签名 | 首次运行会被 SmartScreen 拦一下（「更多信息 → 仍要运行」），个别杀软可能误报 |
| 无自动更新 | 需要重新下载安装包覆盖安装 |
| 验证码识别率依赖模型 | 实测该门户 `raw`+`gray` 单次约 90%，配合重试足够；识别率明显下降时需要重新调策略（`tools/` 里有采样与评测脚本的旧版实现可参考） |

---

## 6. 与旧版的关系

| | 旧版 `captcha-login/` | 本项目 `CampusLink/` |
|---|---|---|
| 形态 | 命令行 + 批处理 | GUI + 安装程序 |
| 密码 | 明文存在 `config.json` | Windows 凭据管理器 |
| 配置位置 | exe 旁边 | `%APPDATA%\CampusLink` |
| 门户差异 | 散在配置文件里 | 收敛成 `profiles/*.json` |
| 测试 | 纯脚本断言 | pytest，44 条 |
| 开机启动 | 任务计划程序 | 注册表 Run 键 |

旧版**保持原样可用**，两者互不影响（不共享虚拟环境、不共享数据目录）。
