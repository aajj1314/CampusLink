# CampusLink 校园网认证客户端

把命令行版的校园网自动登录工具，做成**双击就能用、装在系统里的 Windows 程序**。

- 图形界面（PySide6），面向完全没有编程基础的用户
- 密码存进 **Windows 凭据管理器**，不落明文、不进日志
- 常驻**系统托盘**，断线自动重新认证
- **免验证码注销**：一键断开，腾出会话名额给手机
- **多账号**：一台电脑存多套账号，切换时自动先注销
- **防多设备检测**：TTL 归一 + HTTP UA 归一（Windows 侧）
- 打包成 `CampusLink_Setup_v2.1.3.exe`（**98.9 MB**），双击安装，不需要装 Python

> **本项目是在原有可运行版本基础上做的增量开发。**
> 原项目（`../captcha-login/`）完整保留、未做任何修改，并已备份到
> `../_backup/captcha-login-<时间戳>/`。认证逻辑是从那边**迁移**过来的，不是重写。

---

## 1. 目录结构

```
CampusLink/
├── campuslink/                源码包
│   ├── core/                  认证核心（平台无关，不 import Qt）
│   │   ├── models.py          数据模型：凭据 / 结果 / 门户档案
│   │   ├── portal.py          门户协议层：登录页 + 注销页解析、提交、判定
│   │   ├── authenticator.py   编排：login() 重试 / logout() 注销 / 判定
│   │   ├── ocr.py             字符验证码：多策略预处理（PIL+numpy）+ 投票
│   │   ├── ocr_backend.py     OCR 后端：text / math / logout 三种模式
│   │   ├── crnn_math.py       算式验证码专用 CRNN 模型推理
│   │   ├── captcha_math.py    算式求解 + 答案校验
│   │   ├── netdetect.py       网络状态检测 + 门户发现
│   │   ├── hotspot.py         WinDivert：TTL 修复（热点共享）
│   │   ├── ua_mask.py         WinDivert：HTTP User-Agent 归一
│   │   ├── proc.py            子进程封装（windowed 下不弹黑框）
│   │   └── exceptions.py      异常体系（面向用户的文案）
│   ├── gui/                   图形界面
│   │   ├── app.py             应用入口、单实例检测、--selftest 自检
│   │   ├── main_window.py     主窗口（含多账号、注销、防检测开关）
│   │   ├── worker.py          后台线程：认证 / 注销 / 监测 / 预热
│   │   ├── captcha_dialog.py  人工输入验证码的模态框
│   │   ├── tray.py            系统托盘
│   │   ├── style.py           主题、QSS、程序化图标
│   │   └── assets/            app.ico / app.png
│   ├── router/                Router 控制面（/api/v1，供路由器托管）
│   ├── visionnode/            视觉节点（由 Router 托管认证）
│   ├── profiles/              各校门户档案（换学校只改这里）
│   ├── config.py              用户配置（JSON，不含密码；含多账号）
│   ├── secrets.py             密码存储（系统凭据管理器）
│   ├── autostart.py           开机启动
│   ├── logging_setup.py       日志 + 敏感信息脱敏
│   ├── paths.py               路径解析
│   └── cli.py                 命令行入口（无 GUI 也能用）
├── ml/                        算式验证码模型与训练工程
│   ├── models/crnn_math.onnx  当前在用的模型（20 MiB，实测 92.2%）
│   ├── dataset/               训练/测试样本
│   └── scripts/               训练、评测脚本
├── packaging/                 打包相关
│   ├── CampusLink.spec        PyInstaller 规格
│   ├── CampusLink.iss         Inno Setup 安装脚本
│   ├── entry_gui.py           打包入口
│   ├── version_info.txt       exe 版本资源
│   ├── build_exe.bat          打包成 exe
│   └── build_installer.bat    做成安装程序
├── tests/                     pytest 测试（514 条）
│   └── contract/              Router API 契约测试
├── tools/                     辅助脚本（图标、冒烟测试）
└── docs/                      用户手册、依赖说明、设计文档、交接文档
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
        │  portal / authenticator       │
        │  ocr / netdetect / models     │
        │  hotspot / ua_mask（用 pydivert，但要管理员）│
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

依赖分四组（详见 `docs/依赖说明.md`）：认证核心、GUI、凭据存储、验证码识别。

> ⚠️ **不要在 Linux 上建 `.venv` 再同步到 Windows**。目录结构完全不同
> （Linux 是 `bin/lib`，Windows 是 `Scripts/`），在 Windows 里一个 `.exe` 都没有，
> 纯占 4.6 GB 空间且完全不能用。

### 3.2 跑起来

```bat
:: 图形界面
.venv\Scripts\python -m campuslink.gui

:: 命令行（不需要 Qt，用来排查问题）
.venv\Scripts\python -m campuslink.cli info       :: 运行环境与凭据状态
.venv\Scripts\python -m campuslink.cli netcheck   :: 网络体检 + 门户发现
.venv\Scripts\python -m campuslink.cli dry-run    :: 解析页面+识别验证码，不提交
.venv\Scripts\python -m campuslink.cli login      :: 认证一次
.venv\Scripts\python -m campuslink.cli loop       :: 常驻自动重连
.venv\Scripts\python -m campuslink.cli logout     :: 免验证码注销
.venv\Scripts\python -m campuslink.cli router-serve :: Router 控制面
```

### 3.3 测试

```bat
.venv\Scripts\python -m pytest                    :: 514 条
.venv\Scripts\python -m pytest tests\test_logout.py -v   :: 单个文件
.venv\Scripts\python tools\smoke_gui.py           :: GUI 无头冒烟
```

### 3.4 打包

```bat
packaging\build_exe.bat            :: -> dist\CampusLink\CampusLink.exe
packaging\build_installer.bat      :: -> packaging\output\CampusLink_Setup_v*.exe
```

**Inno Setup 6 需要单独安装**（`build_installer.bat` 会检测并给出下载地址，
不会自己装东西）：<https://jrsoftware.org/isdl.php>

#### 打包后必须做的自检

```bat
dist\CampusLink\CampusLink.exe --selftest
```

结果写到 exe 同目录的 `selftest.txt`，退出码 0 = 全通过。

它检查 7 组依赖：基础库 / 算式模型 / ddddocr+推理 / cv2 / 门户档案 /
注销协议链 / WinDivert。**ddddocr 那项会真跑一次推理**（用代码生成的
80×24 空白 PNG），不是只 import —— 模型文件缺失只有实际前向才暴露。

**为什么必须有这一步**：打包后的 exe 没有标准输出、也没法 import 里面的模块，
「编译能过、运行时才报错」的东西（onnx 模型、cv2 原生库、WinDivert 驱动）
只有让 exe 自己跑一遍才知道真相。

#### ⚠️ `packaging/CampusLink.iss` 的 `DistDir` 必须核对

```ini
#define DistDir ProjectRoot + "\dist16\CampusLink"
```

**这个值每次打包前都要确认指向当前产物目录。** 指向旧目录时
Inno Setup **照样编译成功**，只是安装包里装的是旧版本，完全看不出来。
历史上踩过一次。

#### 体积裁剪（2026-11-19）

产物从 **449.2 MB 降到 329.3 MB**，安装包从 **178.5 MB 降到 98.9 MB**。

裁剪逻辑在 `CampusLink.spec` 里，有**两层**：

| 层 | 位置 | 拦得住什么 |
|---|---|---|
| 一层 | `collect_all` 之后、`Analysis` 之前 | `collect_all` 返回的 entries |
| 二层 | `Analysis` 之后、`COLLECT` 之前 | hook 在 Analysis 阶段补进来的文件 |

**为什么必须两层**：`PySide6` 的 hook 会把 `opengl32sw.dll` 塞进
`a.binaries`，`cv2` 的 hook 也会补回 `opencv_videoio_ffmpeg500_64.dll`
—— 这两个都不在 `collect_all` 的返回里，只做一层会漏
（实测：日志说裁了 3 个文件 100.2 MB，产物里那两个 DLL 还在，实际只省 70.8 MB）。

裁掉的文件：

| 文件 | 大小 | 依据 |
|---|---|---|
| `ddddocr/common.onnx` | 51.58 MB | 代码传 `beta=False` → 走 `common_old.onnx`。A/B 实测 10 张真实验证码：common_old 对 8 次，common 只对 1 次 |
| `ddddocr/common_det.onnx` | 19.20 MB | 只有 `det=True` 才加载，我们从不传 |
| `cv2/opencv_videoio_ffmpeg500_64.dll` | 29.45 MB | 全仓库 `cv2.VideoCapture`/`VideoWriter` = 0 处 |
| `PySide6/opengl32sw.dll` | 19.68 MB | QWidget 不需要 OpenGL（删后 GUI 截图验证正常） |

**加新裁剪项时必须做的三件事**：
1. 在 `_PRUNE_BY_NAME` 里写清「多大、为什么能删、怎么验证的」
2. 重建后跑 `--selftest`
3. **用真实样本验证目标功能**（自检只跑空白图，测不出识别率变化）

⚠️ 这类裁剪错了**不会编译报错**，只会运行时静默失效。

#### 没裁的部分（收益/风险不划算）

| 目标 | 大小 | 为什么不裁 |
|---|---|---|
| **`cv2.pyd`** | **82.3 MB** | **`ddddocr` 在 import 阶段硬依赖它**（详见下方） |
| `numpy` 的 OpenBLAS | 19.55 MB | cv2/onnxruntime 都依赖，动了直接崩 |
| `ddddocr` 库 + `common_old.onnx` | 13.2 MB | 想省必须自己实现 CTC 解码 |

##### 关于 cv2：我们不用了，但删不掉

2026-11-19 把 `core/ocr.py` 的四个预处理全部改成 PIL + numpy
（`cvtColor` / `adaptiveThreshold` / `medianBlur` / `morphologyEx`
分别用 PIL / 积分图 / `np.median` / erode-dilate 实现），
`ocr.py` 已经**不再 import cv2**。

但 `cv2` 还是删不掉，因为 **ddddocr 自己硬依赖它**：

```
ddddocr/__init__.py:3
  → compat/__init__.py:7
    → compat/v1.py:11
      → core/__init__.py:8
        → core/ocr_engine.py:13   ← safe_import_opencv() 抛 ImportError
```

这是**模块级**依赖（不是函数内），所以 `import ddddocr` 就失败，
注销功能整个不可用。`safe_import_opencv()` 名字叫 "safe"，
实际是「导入失败 → 打印一堆解决方案 → 抛 ImportError」，没有降级。

> ⚠️ **曾经误判过**：一度以为「ddddocr 只 import cv2 但注销路径不调用它，
> 所以能删」。测试时塞了个假 cv2 模块（import 能成功），确实跑通了；
> 但**真缺包时直接炸**。判断第三方库的硬依赖不能靠"看起来没用到"。

真要省这 82 MB，只有一条路：不用 ddddocr，自己用 onnxruntime
复现它的 CTC 解码。实测过一个版本：cap2/cap3 完全一致，
**cap1 会多认一个字符**（置信度 0.703）。识别率直接决定注销成功率，不值。

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

### 4.2 两种验证码，两条完全不同的链路

**这是本项目最容易搞混的地方。** 登录和注销用的是两个不同的验证码端点：

| | 登录 | 注销 |
|---|---|---|
| 端点 | `/ACaptcha.ashx` | `/CheckCode` |
| 尺寸 | 200×60 | 80×24 |
| 形态 | **算式**（`5乘0=?`），要填计算结果 | **4 位字母数字** |
| 识别 | CRNN 专用模型 `crnn_math.onnx` | ddddocr |
| 每次换图 | 同一 session 可能返回同一张 | **每次都是新图**（30 张 md5 全不同） |

对应 `ocr_backend.py` 的三种模式：`text` / `math` / `logout`。

**登录侧为什么用专用模型**：算式验证码走通用 OCR 只有约 13% 正确率，
且错答会消耗登录尝试次数（期望收益为负）。训练了专用 CRNN 后达到 92.2%，
模型弃权时上层直接换图，不用低质引擎编造答案。

**注销侧为什么用 ddddocr 且不投票**：注销页每次换图，重试成本极低；
而 ddddocr 的 `probability=True` 能直接给出**真实置信度**（实测 0.95~0.99），
比"策略一致率"准确得多。所以单次识别 + 置信度门限即可。

#### 图像预处理：PIL + numpy，不用 OpenCV（2026-11-19）

`core/ocr.py` 的四个预处理策略原本全走 cv2，现在改用 PIL + numpy：

| 原 cv2 调用 | 现实现 | 与 cv2 的像素差异 |
|---|---|---|
| `cvtColor(BGR2GRAY)` | `Image.convert("L")` | ≤1 个量化级（实测 16/20000 像素）|
| `adaptiveThreshold(MEAN_C,11,2)` | 积分图 box-mean + **四舍五入** | 0 |
| `medianBlur(3)` | `np.median` 3×3，`mode="edge"` | 0 |
| `morphologyEx(OPEN,2×2)` | erode→dilate，**anchor=(1,1)** | 0 |
| `cvtColor(BGR2HSV)` 取 S | 整数四舍五入 `(max-min)*255/max` | ≤1 个量化级 |
| `bitwise_not` | `255 - arr` | 0 |

**三个必须照抄的细节**（弄错就静默改变识别结果，实测数据）：

1. `medianBlur` 边界是 **replicate**，不是 reflect/constant（差 22~29 像素）
2. 偶数核 `getStructuringElement(MORPH_RECT,(2,2))` 的锚点是 **(1,1)** 不是 (0,0)
   （写错会整体偏移 1 像素，差 759~821 像素）
3. `adaptiveThreshold` 的均值要**四舍五入取整** —— cv2 内部用
   `boxFilter(ddepth=-1)`，输出深度跟输入一致（8U）
   （不取整时在大量 0 的区域判错 13~30 像素）

在**真实注销验证码**上，三种策略与 cv2 都是 **0 像素差异**。
随机图压力测试下灰度/饱和度各有 ≤1 的量化差（cv2 用的是整型定点，
浮点无法完全复现），但二值化后结果一致。

> 注意：这只是「我们的图像处理不再依赖 cv2」，**不代表打包能删 cv2**——
> ddddocr 在 import 阶段硬依赖它，见 3.4 节。

#### 为什么不用云端 OCR

评估过一个方案：把超算互联网的文档 OCR API 作为首选项、本地作兜底。
**实测否决了**：

| | 本地 ddddocr | 云端 API |
|---|---|---|
| 8 张真实验证码（提交验证）| **7 正确** | **0 正确** |
| 延迟 | **11.2 ms** | 575 ms |

云端是文档 OCR（`ocrType=GENERAL`），会做版面分析、漏字符、
把验证码判成「非文本行」。详细数据见
`docs/design/10-云端OCR方案评估.md`。

**结论：验证码是小图高频毫秒级任务，文档 OCR 是大图低频秒级任务，
方向不匹配。**

### 4.3 登录验证码：不刷新时的去重

实测登录侧 `/ACaptcha.ashx`：

| 操作 | 结果 |
|---|---|
| 同一 Session 连续请求 | **返回同一张图** |
| 重新 GET `/login` 之后再取 | 会换 |
| 两次尝试落在同一时间粒度内 | 仍可能撞到同一张 |

不做处理的话，「识别错 → 重试 → 拿到同一张错图 → 又识别错」是**死循环**。
`authenticator.py` 的做法：给验证码图片算 md5，**发现重复就换一个新会话 + 等 1.3 秒重取**，
最多 4 轮。`tests/test_mock_e2e.py::test_captcha_dedup_forces_new_session` 专门验证这条路径。

（注销侧不需要这套逻辑 —— 那边每次都换图。）

### 4.4 成功判定：只能直接匹配文案

该门户**成功和失败共用同一个提示容器** `<p class="validation-summary-errors">`，
而且**成功时表单照旧渲染、不跳转**：

| 响应 | 容器内容 | 表单还在？ |
|---|---|---|
| 登录成功 | `联网成功!` | 在 |
| 登录验证码错 | `验证码错误!` | 在 |
| 未实名 | `用户未实名注册运营商上网账号!` | 在 |
| 注销成功 | `注销成功!` | 在 |
| 注销验证码错 | `验证码错误!` | 在 |

所以任何「容器为空」「表单消失」这类**推断式**条件都会误判（旧版真的因为这个
把 7 次成功全报成了失败）。现在配置里就是一句
`{"type": "contains", "value": "联网成功"}`；注销侧同理匹配 `注销成功`。

### 4.5 注销：`/logoff` 不能带 `lrl=1`

实测（2026-11-18，真实在线账号）：

```
GET /logoff?lrl=1   →  当前在线状态 = 「未联网」   ← 假
GET /logoff         →  当前在线状态 = 「已联网」   ← 真
```

带 `lrl` 参数时服务端走另一个分支，状态字段不可信。早期实现拼了 `lrl=1`，
导致一条真实在线的会话被判成未联网，**注销功能完全测不出来**。

**另一个反直觉的事实**：注销成功后页面**仍显示「已联网」**（服务端状态不实时刷新）。
所以「已联网 → 未联网」这个变化判据在成功时**不成立**，
`_judge_logout` 已把它移除，只以文案为主判据。
`tests/test_logout.py::test_state_change_alone_is_not_success` 钉住了这一点。

### 4.6 注销后不能自动重连

用户点「注销」的目的是腾出会话名额（比如给手机用）。
早期版本 `_on_dropped` 只判断「掉线了 + 勾了自动重连」，
不区分**主动注销**和**意外掉线**，结果实测日志：

```
23:17:05  注销验证码 wmhm
23:17:07  检测到掉线，自动重新认证   ← 抵消了用户的注销
23:17:08  识别 7（算式结果）         ← 又登回去了
```

修法：`_suppress_auto_reconnect` 标志，**注销一开始就置位**
（不能等成功才置 —— 成功后 1 秒内就会触发检测，来不及）。
解除时机有三处：注销失败、手动点「立即连接」、认证成功。
三个解除点是为了避免「注销过一次就永远不自动重连」的死状态。

### 4.7 多账号密码：键是 (门户, 账号)，且同步时机很关键

凭据条目名 = `CampusLink:<门户>:<账号>`，所以多账号天然不互相覆盖。

**但有两个必须小心的地方：**

1. **表单同步的时机**。`_persist()` 由每次输入变化触发（400ms 去抖）。
   如果它同时把表单写进 `accounts[当前索引]`，用户改个学工号就会
   悄悄改掉旧账号，旧凭据条目成孤儿、密码丢失（实测复现过）。
   现在同步只在明确动作时发生：勾保存存密码、点新增、切换账号前。

2. **改学工号时要清理旧凭据键**。键变了旧条目要删，
   否则永远留着孤儿，用户填回旧学工号会被一个**过期密码**自动登录。

`tests/test_multi_account_password.py` 用「修复前行为有害」的反面测试钉住了这两点。

### 4.8 网络状态：不能看 TCP 通不通

强制门户会**劫持所有 :80 连接并由网关自己代答**，TCP 握手百分百成功 ——
那是网关在回你，不是互联网。旧版因此把「未认证」误报成「已联网」。

可靠判据只能是 **HTTP 响应内容**：`generate_204` 真联网时返回 204 且 body 为空。

### 4.9 门户发现：不能只靠劫持重定向

该门户**原地返回登录页、不做 302**，所以「看请求被重定向到哪里」这条路抓不到东西。
`PortalProfile` 因此多了 `portal_candidates`：**已知地址优先探，自动发现兜底**。

### 4.10 密码：结构上就没有存放位置

`AppConfig` 里**没有任何密码字段** —— 不是靠"记得别写进去"的约定。
密码走 `keyring` → Windows 凭据管理器（条目名 `CampusLink:<门户>:<账号>`，
带门户名是为了换学校时互不覆盖，带账号是为了多账号互不覆盖）。

**没有可用凭据后端时，宁可不保存，也不退化成明文文件**，程序会明确告诉用户
「这台电脑无法安全保存密码，每次启动需要重新输入」。

### 4.11 日志脱敏：两层防护

`logging_setup.py` 做了两层：

1. **运行时登记**：拿到密码/Cookie 的那一刻调用 `register_secret(value)`，
   之后任何日志里出现这个字符串都会被替换成 `***`。即使某处代码不小心把
   整个 payload 打出来，密码也不会泄漏。
2. **正则兜底**：`password=` / `token=` / `cookie=` 这类键值对模式替换。

`tests/test_config_and_secrets.py::test_log_file_never_contains_password`
是端到端验证：写下含密码的日志，再读文件确认密码不在里面。

### 4.12 开机启动：注册表 Run 键

**不用**「启动文件夹 + 快捷方式」，**也不用**计划任务：

- 比快捷方式简单：不需要生成 `.lnk`（那要么依赖 pywin32，要么调 VBScript，
  而 VBScript 在新版 Windows 上正在被移除）
- 比 `schtasks` 可靠：旧版用计划任务，结果在装了终端管控软件的电脑上直接被拦
- 没有黑框闪现：GUI 程序（`console=False`）从 Run 键拉起不会出现控制台窗口
- 用户态即可，不需要管理员权限
- 位置固定好排查：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`

安装程序和程序内开关写的是**同一个键**，所以两边状态天然一致。

### 4.13 防多设备检测：两层，各管一段

学校限制「一台设备在线」。除了认证本身，出口流量也要看起来像一台机器。

| 层 | 做法 | 覆盖范围 | 实现 |
|---|---|---|---|
| **TTL 归一** | 出站包 TTL 统一为 64 | IP 层，全部流量 | `core/hotspot.py` |
| **UA 归一** | 明文 HTTP 的 User-Agent 改成一个值 | 只有 80 端口 | `core/ua_mask.py` |

两者都用 **WinDivert** 拦包改写，需要管理员权限，可独立开关。

#### UA 归一的硬约束

**不能改变 TCP 包长度。** 改长度 = 后续所有包的序列号都要平移
= 要做完整 TCP 重组。所以采用「原地替换 + 空格填充」：

```
原 UA  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131"  (120 字节)
新 UA  "Mozilla/5.0                                       "  (120 字节)
```

HTTP 规范（RFC 7231 §5.5.3）要求 UA 值两端空白被忽略，
所以服务端解析出来就是干净的 `Mozilla/5.0`。

**由此带来的限制**（与路由器上的 ua2f 完全一致）：
- 新 UA 不能比原 UA 长 → 短 UA 会被截断
- 长度 < 3 时直接放弃（截断只剩一两个字反而更假）
- **HTTPS 的 UA 改不了**（在 TLS 加密层里，任何工具都做不到）

`curl` / `wget` / `python-requests` / `Go-http-client` / `okhttp` / `Java` /
`Microsoft-CryptoAPI` 这些**主动跳过** —— 改了反而制造「一台机器只有一种 UA」
的异常。

`tests/test_ua_mask.py::TestLengthInvariant` 穷举 3~500 字节验证长度不变式——
这条一破连接就挂。

#### 与路由器方案的关系

路由器方案（uA2F + nftables）效果好得多，但管不到两个场景：
Windows 开热点共享、临时插网线直连。这两种只能在本机改。

**两套方案不要同时开** —— 双层改写会产生 `TTL 64 → 63 → 64` 的抖动。

### 4.14 线程模型

认证要发多个 HTTP 请求、还可能重试 8 次每次隔 1.5 秒，最长几十秒；
注销同理；网络检测最长等 6 秒；识别模型首次加载要 1～2 秒。
**这些都不能放主线程**。

| 线程 | 职责 | 生命周期 |
|---|---|---|
| `AuthWorker` | 一次认证，可取消 | 每次点「立即连接」新建 |
| `LogoutWorker` | 一次注销，可取消 | 每次点「注销」新建 |
| `MonitorWorker` | 周期检测网络、触发自动重连 | 常驻 |
| `WarmupWorker` | 预热识别模型 | 启动时跑一次 |

线程之间**只通过 Qt 信号通信，不共享可变状态**，避免竞态。

人工输入验证码是个特例：worker 阻塞在 `threading.Event` 上，
主线程弹模态框，答完再交回来（`submit_answer()`）。
这是唯一安全的跨线程问答方式 —— Qt 的 UI 只能在主线程动。

### 4.15 打包：onedir，不用 onefile

onefile 每次运行都要把几百 MB 解压到 `%TEMP%`，启动要十几秒。
本项目是常驻托盘的程序，onedir 启动不到 2 秒，明显更合适。

另外**必须 `collect_all` onnxruntime / keyring / pydivert / ddddocr / cv2** ——
它们的模型和原生 DLL 是 data 文件，PyInstaller 的静态分析看不见，
不收集的话编译能过、运行时才报错。

> **2.0 曾把 ddddocr / cv2 从打包里排除**（当时只有算式门户，用不到）。
> 3.x 加注销功能后又必须加回来 —— 注销验证码走的就是 ddddocr。
> 这个 bug 的特征是**编译完全不报错，运行时才报「字符识别引擎不可用」**。
> 代价是产物从 241 MB 涨到 449 MB。

> **一个容易搞反的地方**：ddddocr 的 `beta=False`（默认）实际加载的是
> **`common_old.onnx`**，不是 `common.onnx`。源码里是
> `if old: ... elif beta: common.onnx else: common_old.onnx`。
> 名字极具迷惑性，别看着「old」就以为可以删。

---

## 5. 版本历史

| 版本 | 主要变化 |
|---|---|
| 1.0.x | 初始版本：GUI + 安装包 + 登录认证 |
| 2.0.x | 算式验证码改用专用 CRNN 模型（92.2%）；剥离 ddddocr；加 `--selftest` |
| **2.1.0** | **免验证码注销**（ddddocr + 置信度门限）；**多账号** + 切换自动注销；**Windows 侧 UA 归一** |
| 2.1.1 | 修：多账号入口被隐藏、新增时丢备注名 |
| 2.1.2 | 修：默认窗口太窄导致账号行按钮被切；清理 ml2 实验线 |
| **2.1.3** | **体积裁剪**：产物 449→329 MB，安装包 178.5→98.9 MB（去 4 个无用大文件） |

---

## 6. 已知限制

| 限制 | 说明 |
|---|---|
| 只支持 Windows | `autostart.py` 用注册表、`hotspot.py`/`ua_mask.py` 用 WinDivert；core 本身跨平台 |
| 只内置了一所学校的门户档案 | 换学校需新增 `profiles/*.json`，代码不用改 |
| 未做代码签名 | 首次运行会被 SmartScreen 拦一下（「更多信息 → 仍要运行」），个别杀软可能误报 |
| 无自动更新 | 需要重新下载安装包覆盖安装 |
| 安装包约 99 MB | 已从 178.5 MB 裁到 98.9 MB（见 3.4 节）。剩余大头是 `cv2.pyd`(82 MB) + `onnxruntime`(42 MB)，再裁会降识别率 |
| UA 归一不覆盖 HTTPS | 协议决定的，任何工具都做不到（除非做 TLS 中间人） |
| UA 归一不能变长 | 新 UA 比原 UA 长时会被截断（与 ua2f 一致） |
| 防检测需管理员权限 | WinDivert 要装内核驱动 |

---

## 7. 与旧版的关系

| | 旧版 `captcha-login/` | 本项目 `CampusLink/` |
|---|---|---|
| 形态 | 命令行 + 批处理 | GUI + 安装程序 |
| 密码 | 明文存在 `config.json` | Windows 凭据管理器 |
| 配置位置 | exe 旁边 | `%APPDATA%\CampusLink` |
| 账号 | 单账号 | 多账号，可切换 |
| 门户差异 | 散在配置文件里 | 收敛成 `profiles/*.json` |
| 测试 | 纯脚本断言 | pytest，514 条 |
| 开机启动 | 任务计划程序 | 注册表 Run 键 |

旧版**保持原样可用**，两者互不影响（不共享虚拟环境、不共享数据目录）。

---

## 8. 相关文档

| 文档 | 内容 |
|---|---|
| `docs/用户手册.md` | 面向最终用户的操作说明 |
| `docs/依赖说明.md` | 每个依赖为什么需要、能不能去掉 |
| `docs/验收报告.md` | 验收记录 |
| `docs/HANDOVER-交接文档-*.md` | **接手必读**：已知 bug、构建坑、发布流程 |
| `docs/design/00-总体架构.md` | 分层与模块边界 |
| `docs/design/01-模块职责.md` | 每个文件干什么 |
| `docs/design/02-API规范.md` | 内部接口契约（有 contract 测试守着） |
| `docs/design/03-核心数据结构.md` | 数据模型字段说明 |
| `docs/design/04-平台依赖.md` | 平台相关代码在哪 |
| `docs/design/07-门户认证协议实测.md` | 登录/注销协议逐字段实测记录 |
| `docs/design/08-验证码改版应对.md` | 算式验证码改版的应对过程 |
| `docs/design/09-免验证码注销与防检测整合.md` | **注销 + 防检测的完整技术方案** |
| `docs/design/06-风险评估与测试.md` | 测试策略与风险表 |
