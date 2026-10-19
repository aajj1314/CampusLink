# CampusLink 静默版

CampusLink 的独立静默分支：无界面、无托盘、开机自启，自动完成校园网网页认证，断线自动重连。

- 远程分支：<https://github.com/aajj1314/CampusLink/tree/silent>
- 与 `main`（GUI 版）、`cli-headless` 相互独立，不共享提交历史
- 当前版本：2.0.0

## 功能

- 后台静默运行，登录 Windows 后自动启动
- 自动识别算术验证码：CRNN + CTC，ONNX CPU 推理
- 通过门户 `/logoff` 判断在线状态，未联网时自动认证
- 验证码置信度不足自动换图；连续失败自动打开浏览器兜底
- 账号类错误冷却 10 分钟，避免频繁失败触发风控
- 单实例运行，配置文件与日志独立
- 安装向导内填写学工号、上网账号、密码
- 卸载时结束进程、删除自启项、清空配置与日志，重装即全新

## 目录结构

```
CampusLink静默版本/
├─ campusnet/
│  ├─ main.py            程序入口、命令行参数
│  ├─ config.py          配置加载、开发/打包路径
│  ├─ portal.py          门户 HTTP 客户端、错误归类
│  ├─ captcha.py         ONNX 预处理、CTC 解码、算式求值
│  ├─ monitor.py         在线检测、自动重连、退避
│  ├─ autostart.py       开机自启注册表
│  ├─ single_instance.py 单实例互斥量
│  └─ logger.py          日志轮转
├─ models/
│  ├─ crnn_math.onnx     验证码识别模型（必需）
│  ├─ train_full_log.txt 训练日志
│  └─ crnn_math_model_使用说明.html
├─ packaging/
│  ├─ CampusNetAutoLogin.spec   PyInstaller 配置
│  ├─ CampusNetAutoLogin.iss    Inno Setup 安装脚本
│  ├─ entry.py                  PyInstaller 入口
│  ├─ app.ico                   CampusLink 图标
│  ├─ build_exe.bat             构建 exe
│  └─ build_installer.bat       构建安装包
├─ config.example.json
├─ requirements.txt
├─ run.py                       带控制台入口
└─ run.pyw                      无窗口入口
```

## 快速开始（源码运行）

要求 Python 3.10 或更高版本。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.org/simple
copy config.example.json config.json
```

编辑 `config.json`：

```json
{
  "account": {
    "student_id": "学(工)号",
    "username": "运营商上网账号",
    "password": "密码"
  }
}
```

启动：

```powershell
.venv\Scripts\python.exe run.py      # 带控制台，便于调试
.venv\Scripts\pythonw.exe run.pyw    # 无窗口后台运行
```

常用调试命令：

```powershell
.venv\Scripts\python.exe run.py --test-captcha   # 只抓一张验证码并识别
.venv\Scripts\python.exe run.py --once           # 只检测/认证一次
```

## 配置说明

配置文件为 JSON，未填写的字段会自动使用默认值。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `account.student_id` | 空 | 学(工)号，必填 |
| `account.username` | 空 | 运营商上网账号，必填 |
| `account.password` | 空 | 上网密码，必填 |
| `portal.base_url` | `http://10.6.6.6` | 认证门户地址 |
| `portal.isp` | `自动选择` | 运营商选择 |
| `monitor.check_interval_seconds` | `20` | 在线状态检测间隔 |
| `monitor.max_captcha_retries` | `6` | 单次重连最多验证码尝试次数 |
| `monitor.open_browser_on_failure` | `true` | 连续失败后是否打开浏览器 |
| `monitor.account_cooldown_seconds` | `600` | 账号类错误冷却时间 |
| `captcha.confidence_threshold` | `0.9` | 低于该置信度直接换图 |
| `logging.level` | `INFO` | 日志级别 |

注意：学工号、上网账号、密码必须与在运营商处实名登记的信息一致，否则门户会返回“用户未实名注册运营商上网账号”。

## 数据与日志位置

源码运行：

- 配置：项目目录 `config.json`
- 日志：项目目录 `logs\app.log`

安装版：

- 安装目录：`%ProgramFiles%\CampusLinkSilent`
- 配置：`%APPDATA%\CampusLinkSilent\config.json`
- 日志：`%APPDATA%\CampusLinkSilent\logs\app.log`
- 开机自启：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
  中的 `CampusLinkSilent` 项

桌面上的“校园网认证日志”快捷方式会直接打开安装版日志。程序本身是静默服务，没有窗口或托盘，双击启动入口不会有可见反应。

## 构建 exe 与安装包

依赖：Python 3.10+、PyInstaller、Inno Setup 6。

```powershell
.venv\Scripts\python.exe -m pip install pyinstaller -i https://pypi.org/simple
packaging\build_exe.bat
packaging\build_installer.bat
```

产物：

- `dist\CampusNetAutoLogin\CampusNetAutoLogin.exe`
- `packaging\output\CampusLink_Silent_Setup_v2.0.0.exe`

`build_exe.bat` 使用项目自己的 `.venv`；`build_installer.bat` 会自动查找 Inno Setup 6 的 `ISCC.exe`。

## 验证码模型

- 输入：200×60 的 PNG，程序内部转为灰度并缩放到 200×32
- 字符集：`0-9` 加 `加 减 乘 除`
- 输出：算式原文，例如 `7加9`，程序再求值为 `16`
- 测试集答案正确率约 92.2%；置信度低于 0.9 时程序会主动换图
- 模型仅针对当前校园网门户训练，换门户后效果不保证

## 卸载

在“设置 → 应用”中卸载，或运行安装目录下的 `unins000.exe`。卸载会：

- 结束正在运行的 `CampusNetAutoLogin.exe`
- 删除 `CampusLinkSilent` 开机自启项
- 删除 `%APPDATA%\CampusLinkSilent`（配置与日志）
- 删除开始菜单、桌面日志快捷方式

## 使用边界

- 仅限个人单账号自用，不做多账号池
- 密码以明文保存在本地 JSON 中，请勿把配置文件发送给他人
- 不要同时运行本静默版和 CampusLink GUI/Python 版去认证同一个账号
- 门户改版后可能需要更新 `campusnet/portal.py` 或安装脚本
