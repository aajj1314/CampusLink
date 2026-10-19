; 简体中文语言包（Inno Setup 6）
; Inno Setup 官方安装包不带简体中文，需要单独提供。
; 这里内置一份，覆盖安装向导用户会看到的全部标准文案。
; 任何没列出的键，Inno 会回退到内置英文默认值，所以缺一条不会崩。

[Languages]
Name: "chinese"; LanguageID: 2052; LanguageCodePage: 936; LanguageName: "中文(简体)"

[Messages]
; ---- 标题栏 ----
; 校园网自动认证 是 Inno 在运行时替换的占位符（替换成 AppName），
; 不能用 {#SetupSetting(...)} —— 那是预处理期函数，.isl 加载时还没解析 [Setup]
SetupAppTitle=校园网自动认证 安装程序
SetupWindowTitle=校园网自动认证 安装程序

; ---- 准备 ----
PreparingToInstall=正在准备安装...
PreparingToUninstall=正在准备卸载...

; ---- 欢迎页 ----
WelcomeLabel1=欢迎使用 校园网自动认证 安装向导
WelcomeLabel2=此向导将帮你在计算机上安装 [name/ver]。%n%n建议在继续之前关闭其它正在运行的程序。
WelcomeLabel3=此向导将帮你从计算机上卸载 校园网自动认证。%n%n建议在继续之前关闭其它正在运行的程序。

; ---- 目录选择 ----
SelectDirLabel3=安装程序将把 校园网自动认证 安装到以下文件夹。
SelectDirBrowseLabel=如需安装到其它位置，请点击「浏览」选择。%n%n点击「下一步」继续。
DiskSpaceMBLabel=至少需要 [mb] MB 的可用磁盘空间。
DiskSpaceGBLabel=至少需要 [gb] GB 的可用磁盘空间。
CannotInstallToNetworkDrive=安装程序无法安装到网络驱动器。
CannotInstallToVolume=安装程序无法安装到该卷。
InvalidPath=输入的路径中必须包含完整驱动器号，例如：%n%nC:\APP%n%n而不是:%n%n\APP
InvalidDrive=选定的路径无效或不可访问。请选择其它位置。

; ---- 开始菜单 ----
SelectStartMenuFolderLabel2=安装程序将在以下位置创建程序的快捷方式。
SelectStartMenuFolderBrowseLabel=如需放到其它位置，请点击「浏览」选择。%n%n点击「下一步」继续。
MustEnterGroupName=请输入文件夹名称。
GroupNameTooLong=文件夹名称太长。
InvalidGroupName=文件夹名称无效。
BadGroupName=文件夹名称不能包含下列字符:%n%n%1

; ---- 任务 ----
SelectTasksLabel2=选择安装程序要执行的附加任务，然后点击「下一步」。
SelectTasksDesc=选择附加任务

; ---- 准备安装 ----
ReadyLabel1=安装程序已准备好在你的计算机上安装 校园网自动认证。
ReadyLabel2a=点击「安装」开始安装。
ReadyLabel2b=点击「下一步」继续。
ReadyTasks=已选择执行的任务:
ReadyGroup=程序快捷方式将创建在:
ReadyDir=将安装到:
ReadyMemoDir=安装位置:
ReadyMemoGroup=快捷方式位置:
ReadyMemoTasks=附加任务:
OnlyAdmin=此安装需要管理员权限，但 Windows 当前账户不是管理员。%n%n请使用管理员账户登录后重试。
PrivilegesRequired=此安装需要管理员权限。

; ---- 按钮 ----
ButtonNext=< 上一步(&P) | 下一步(&N) >
ButtonBack=< 上一步(&P)
ButtonInstall=安装(&I)
ButtonOK=确定
ButtonCancel=取消
ButtonYes=是(&Y)
ButtonNo=否(&N)
ButtonFinish=完成(&F)
ButtonBrowse=浏览(&R)...
ButtonYesToAll=全是(&A)
ButtonNoToAll=全否(&O)
ButtonAbort=中止
ButtonIgnore=忽略(&I)
ButtonRetry=重试(&R)

; ---- 安装中 ----
StatusClosingApplications=正在关闭应用程序...
StatusCreateDir=正在创建目录...
StatusCreateShortcut=正在创建快捷方式...
StatusExtractFiles=正在解压文件...
StatusRegisterFiles=正在注册文件...
StatusSavingRestore=正在保存还原数据...
StatusRunProgram=正在完成安装...
StatusRollback=正在回滚更改...
StatusUndoFiles=正在还原文件...
StatusVolumeChange=正在变更卷...

; ---- 文件相关 ----
ErrorReadingSource=无法读取源文件 "%1"。%n%n%2
FileCorrupted=文件 "%1" 已损坏。
FileAbortRetryIgnore=点击「重试」再试一次；点击「忽略」跳过此文件（不推荐）；或点击「中止」取消安装。
ErrorChangingAttr=无法设置文件 "%1" 的属性。

; ---- 完成页 ----
FinishedHeadingLabel=校园网自动认证 安装完成
FinishedLabel=安装向导已成功在你的计算机上安装 校园网自动认证。
FinishedLabelNoIcons=安装向导已成功在你的计算机上安装 校园网自动认证。
ClickFinish=点击「完成」退出安装向导。
FinishedRestartLabel=要完成安装，安装向导需要重新启动你的计算机。现在重新启动吗？
FinishedRestartMessage=%n要完成 校园网自动认证 的安装，安装向导需要重新启动你的计算机。%n%n是否现在重新启动？
ShowReadmeCheck=是，我想查看自述文件
YesRadio=是，立即重新启动计算机(&Y)
NoRadio=否，我稍后自行重新启动(&N)
RunProgramExecError=安装程序无法启动可执行文件 "%1"。%n%n%2

; ---- 卸载 ----
UninstallAppTitle=卸载 校园网自动认证
ConfirmUninstall=你确定要完全移除 %1 及其所有组件吗？
UninstallAppFullTitle=卸载 %1
UninstallDataCorrupted="%1" 文件已损坏。无法卸载。
UninstallLabel=卸载 校园网自动认证
OnlyAdminUninstall=此卸载需要管理员权限，但 Windows 当前账户不是管理员。%n%n请使用管理员账户登录后重试。
CreateQuestion=是否创建 %1?
CannotContinue=安装无法继续。请点击「取消」退出。
RunErrorInWaitProgram=安装完成后运行程序时出错:%n%n%1

; ---- 进度 ----
MB=MB
GB=GB
KB=KB
B=B
KB=&1 KB
MB=&1 MB
GB=&1 GB
Elapsed=已用
Remaining=剩余
Initialize=正在初始化...
Stop=停止(&S)

; ---- 提示 ----
ExitSetupTitle=退出安装向导
ExitSetupMessage=安装尚未完成。如果现在退出，程序将不会被安装。%n%n你可以稍后再次运行安装向导完成安装。%n%n确定要退出吗？
ConfirmTitle=确认
AskCreateProgramGroup2=是否在开始菜单为 校园网自动认证 创建快捷方式？
ErrorCreatingGroup=无法创建程序组 %1。
ErrorCreatingIcon=无法创建快捷方式 %1。
ErrorExtractionShortName=无法设置 %1 的短文件名。
ErrorRegisterServer=无法注册 DLL/OCX: %1
CouldNotLoadInfAddress=无法加载地址信息。

; ---- 磁盘空间 ----
SelectDirDesc=选择安装位置
SelectStartMenuFolderDesc=选择开始菜单文件夹位置
ReadyMemoTasks2=附加任务:
WizardSelectDir=选择安装位置
WizardSelectProgramGroup=选择开始菜单文件夹
WizardSelectTasks=选择附加任务
WizardSelectProgramGroupDesc=选择开始菜单文件夹位置

; ---- 向导步骤标题 ----
WizardInstalling=正在安装
WizardLicense=许可协议
WizardPassword=密码
WizardPreparing=正在准备
WizardWelcome=欢迎
WizardFinished=完成
WizardType=安装类型
WizardSelectComponents=选择组件
WizardSelectProgramGroup=选择开始菜单文件夹
WizardSelectTasksDesc=选择附加任务
WizardSelectDirDesc=选择 校园网自动认证 的安装位置
WizardSelectTasksLabel2=选择你想让安装程序执行的附加任务，然后点击「下一步」:
WizardReady=准备安装
WizardPreparingDesc=准备安装
WizardReadyDesc=准备开始安装
WizardUninstalling=正在卸载
WizardWelcomeDesc=欢迎

[CustomMessages]
; 自定义消息：[Tasks] 和 [Run] 里的 Description 引用这些。
; 注意：键名不能写成 <语言名>.xxx —— Inno 会把它当成"给语言设属性"而拒绝。
; 默认任务名（如果 .iss 里 Task 没用自己的 CM 名字会用到）
tasksdesktopicon=创建桌面快捷方式(&D)
additionaltasks=附加任务:
AdditionalIcons=附加图标:
CreateDesktopIcon=创建桌面快捷方式(&D)
CreateQuickLaunchIcon=创建快速启动栏快捷方式(&Q)
OptionsStartMenuGroup=开始菜单文件夹:
OutputDir=输出目录:
SelectTasksLabel2=选择你希望安装程序执行的附加任务，然后点击「下一步」:
ReadyMemoTasks=附加任务:
; ---- 我们这个安装包自定义的（在 .iss 里引用）----
; (在 .iss 的 [Tasks] Description 里直接写了中文，没用到 CM 引用，
;  这里留默认占位。)
