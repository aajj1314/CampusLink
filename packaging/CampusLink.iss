; ===============================================================
;  CampusLink 安装程序脚本（Inno Setup 6）
;
;  编译（一般走 packaging\build_installer.bat）：
;      ISCC.exe packaging\CampusLink.iss
;
;  产物：packaging\output\CampusLink_Setup_v1.0.exe
;
;  设计要点：
;   · 装到 Program Files，需要管理员权限（标准做法，避免权限混乱）
;   · 「开机自动启动」做成安装时的可勾选项，写的是 HKCU 的 Run 键 ——
;     和程序内那个开关用的是同一个键，所以两边状态天然一致
;   · 卸载时询问是否删除用户配置；密码在凭据管理器里，脚本删不掉，
;     所以在提示里明确告诉用户去哪儿手动清
; ===============================================================

#define MyAppName "CampusLink"
#define MyAppNameCN "校园网认证客户端"
#define MyAppVersion "2.1.5"
#define MyAppPublisher "CampusLink"
#define MyAppExeName "CampusLink.exe"
#define MyAppId "{{8F3A7C21-4B6E-4E2A-9D58-2C1F7A9E5B04}"

; 上一级目录（本文件在 packaging/ 下）
#define ProjectRoot SourcePath + ".."
#define DistDir ProjectRoot + "\dist19\CampusLink"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName} {#MyAppNameCN}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription={#MyAppName} 安装程序

; ---- 安装位置 ----
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes

; ---- 权限：装到 Program Files 需要管理员 ----
PrivilegesRequired=admin

; 2026-11-19 去掉了 PrivilegesRequiredOverridesAllowed=dialog。
;
; 它原本的作用：让用户能选「仅为当前用户安装」（装到 LOCALAPPDATA，
; 不需要管理员权限）。
;
; 去掉的原因 —— 它破坏了单实例检查：
;
;   dialog 模式会在 **InitializeSetup 之前**弹出「选择安装模式」对话框。
;   实测同时启动两个安装器时，两个进程都先弹这个框、都进向导，
;   [Code] 里的互斥检查根本没机会跑（顺序上晚了一步）。
;   结果就是用户截图里看到的：界面重叠、底部冒出两个「上一步」按钮。
;
; 而它带来的收益很小：本程序要写 Program Files、还要注册 WinDivert
; 内核驱动（防检测功能必需），没有管理员权限本来也用不了完整功能。
;
; 权衡后：单实例可靠 > 支持非管理员安装。

; ---- 输出 ----
OutputDir=output
OutputBaseFilename=CampusLink_Setup_v{#MyAppVersion}
SetupIconFile={#ProjectRoot}\campuslink\gui\assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppNameCN}

; ---- 系统要求：只支持 64 位 ----
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

; ---- 外观：现代向导 ----
WizardStyle=modern

Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes

; ---- 单实例限制 ----
; 不在 [Setup] 里用 SetupMutex —— 实测那个指令没生效
; （同时启动两个安装器，两个都能进向导、都能继续装）。
; 改成在 [Code] 的 InitializeSetup 里自己维护互斥，
; 行为可验证、可控制。详见文件末尾 [Code] 段的说明。

; ---- 安装路径选择页 ----
; 2026-11-19：从 auto 改成 no。
;
; auto 的行为是「检测到已安装就跳过路径页、沿用旧路径」。
; 改成 no 后**每次安装都让用户自己选**，行为可预期。
;
; 为什么不用 auto：
;   用户换盘安装（比如从 C: 换到 F:）时，auto 会把用户按回旧路径，
;   而界面上看不出发生了什么 —— 用户会以为安装程序没好。
;   代价只是升级时多一次「下一步」。
DisableDirPage=no
DisableReadyPage=no
ShowLanguageDialog=no

[Languages]
; Inno Setup 6 官方安装包不带简体中文（属于"非官方"语言，需单独下载）。
; 所以在 packaging/ 里内置了一份 ChineseSimplified.isl，这里直接引用本地文件。
; 路径相对本 .iss 所在目录（packaging/），所以就是同目录下的文件名。
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"
Name: "startup";     Description: "开机自动启动（登录 Windows 后自动登录校园网）"; GroupDescription: "附加任务:"; Flags: checkedonce

[Messages]
; ---- 标题里的 [name] 占位符 ----
;
; 中文语言包（ChineseSimplified.isl）里有两条消息**硬编码了字面的 "[name]"**
; （不是 %1 占位符），会原样显示在界面上：
;
;   SetupWindowTitle      → "[name] 安装程序"
;   UninstallAppTitle     → "卸载 [name]"
;   UninstallLabel        → "卸载 [name]"
;
; 2026-11-19 实测：安装器标题之前覆盖过，但**卸载器的漏了** ——
; 卸载时窗口标题就是「卸载 [name]」。
;
; 注意要用对消息名：
;   UninstallAppTitle     —— 卸载**窗口标题**（实际生效的是这条）
;   UninstallAppFullTitle —— 带版本/名称的完整标题（向导内某些位置用）
;   两条都要覆盖，只改一条不够。
SetupWindowTitle={#MyAppName} {#MyAppNameCN} 安装程序
; SetupAppTitle 是**简版标题**，用在：消息框、任务栏、初始化失败提示等。
; 实测漏了这条：InitializeSetup 返回 False 后弹出的窗口
; 标题显示成 "[name] 安装程序"（中文语言包里硬编码的字面 [name]）。
; 两条都要覆盖。
SetupAppTitle={#MyAppName} {#MyAppNameCN} 安装程序
UninstallAppTitle=卸载 {#MyAppName}
UninstallAppFullTitle=卸载 {#MyAppName} {#MyAppNameCN}
UninstallLabel=卸载 {#MyAppName}
UninstalledAll={#MyAppName} 已从你的计算机中移除。
UninstalledMost={#MyAppName} 已卸载完成。%n%n部分文件需要手动删除。

[Files]
; 整个 onedir 产物都装进去。_internal 目录里有 Qt 运行库和识别模型，一个都不能少。
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 用户文档
Source: "{#ProjectRoot}\docs\用户手册.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "{#ProjectRoot}\docs\依赖说明.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "{#ProjectRoot}\README.md";       DestDir: "{app}\docs"; DestName: "开发者说明.md"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}";              Filename: "{app}\{#MyAppExeName}"
Name: "{group}\用户手册";                   Filename: "{app}\docs\用户手册.md"
Name: "{group}\卸载 {#MyAppName}";          Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";        Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; 「开机自动启动」勾选项 -> 写 HKCU 的 Run 键。
; 键名和参数必须跟 campuslink/autostart.py 里的一致，否则
; 程序内那个开关会显示成「关闭」，两边状态对不上。
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "CampusLink"; \
    ValueData: """{app}\{#MyAppExeName}"" --autostart"; \
    Flags: uninsdeletevalue; Tasks: startup

[Run]
; 装完可以直接勾选启动
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallRun]
; 先让正在运行的程序退出，否则卸载会残留文件删不掉
Filename: "{cmd}"; Parameters: "/c taskkill /f /im {#MyAppExeName}"; \
    Flags: runhidden; RunOnceId: "KillRunning"

[UninstallDelete]
; 这两类是程序运行时会生成的，卸载时直接清掉
Type: filesandordirs; Name: "{app}\docs"
Type: filesandordirs; Name: "{app}\_internal"
Type: files;          Name: "{app}\*.dll"

[Code]
{ ============================================================
  单实例检查（2026-11-19 加）
  ============================================================

  问题：不加限制时，用户可以同时开多个安装器（比如双击两次）。
  后果：
    · 两个向导窗口重叠 —— 实测用户截图里底部露出两个「上一步」按钮
    · 两个实例同时解压同一批文件，写入冲突
    · 可能留下半装状态（文件不全但注册表已写）

  为什么不用 [Setup] 的 SetupMutex：
    实测设置了 `SetupMutex=CampusLinkSetupMutexV2` 但**没生效** ——
    同时启动两个安装器，两个都正常进入向导，都能继续。
    也确认过这个值没被编进安装包（Inno 会加密元数据，
    用字符串搜索验证不了，只能行为测试）。

  改用 [Code] 里自己维护一个互斥：
    CreateMutex 句柄存在 = 已有实例在跑
    句柄只在本进程生命周期内有效，进程退出系统自动释放，
    不会像锁文件那样留下"假死"的锁把用户永久挡在外面。

  注意：必须在 InitializeSetup 里做（最早的时机），
  放到 ssInstall 就晚了 —— 那时文件已经开始复制。
  ============================================================ }

const
  { 全局命名，跨会话（多用户切换）也互斥 }
  MUTEX_NAME = 'Global\CampusLink_Setup_SingleInstance_v2';

{ kernel32 的两个 API。
  注意 GetLastError 的调用必须紧跟在 CreateMutex 之后 ——
  Inno 的脚本引擎本身可能插进别的 win32 调用把错误码冲掉，
  所以这里把两步包进同一个函数里，中间不留空隙。 }
function CreateMutexW(lpMutexAttributes: LongWord;
                      bInitialOwner: Boolean;
                      lpName: String): THandle;
  external 'CreateMutexW@kernel32.dll stdcall';

function GetLastError(): LongWord;
  external 'GetLastError@kernel32.dll stdcall';

{ 返回 True 表示「已有实例在跑」 }
function AnotherInstanceRunning(): Boolean;
var
  Mutex: THandle;
  LastErr: LongWord;
begin
  Mutex := CreateMutexW(0, False, MUTEX_NAME);
  LastErr := GetLastError();

  { CreateMutex 失败（返回 0）：不要阻塞安装。
    宁可允许装，也别因为权限问题让用户装不了。 }
  if Mutex = 0 then
  begin
    Result := False;
    Exit;
  end;

  { ERROR_ALREADY_EXISTS = 183
    Windows 语义：互斥已存在时仍然返回有效句柄，
    但 GetLastError 被设为 183 —— 这正是我们要的信号。 }
  Result := (LastErr = 183);

  { 句柄故意不关：
    第一个实例持有它直到进程退出（系统自动释放），
    第二个实例拿到句柄后立刻退出，也不会泄漏。 }
end;

function InitializeSetup(): Boolean;
begin
  if AnotherInstanceRunning() then
  begin
    { 静默模式（/SILENT、/VERYSILENT）下不弹框 ——
      无人值守调用（脚本批量装、远程部署）时弹框会一直挂着。
      而且 /SUPPRESSMSGBOXES 对 [Code] 里的 MsgBox 不生效
      （实测：/VERYSILENT + /SUPPRESSMSGBOXES 时进程仍然卡在 MsgBox 上）。
      所以这里自己判断，静默时直接返回 False 退出。}
    if WizardSilent() then
    begin
      Result := False;
      Exit;
    end;

    MsgBox('CampusLink 安装程序已经在运行。' + #13#10 + #13#10 +
           '请先关闭已经打开的安装窗口，再重新运行。' + #13#10 + #13#10 +
           '同时运行两个安装程序会导致文件写入冲突、界面重叠，' + #13#10 +
           '甚至留下装了一半的状态。',
           mbError, MB_OK);
    Result := False;
  end
  else
    Result := True;
end;
{ 卸载时询问是否删除用户配置。
  注意：账号密码存在 Windows 凭据管理器里，安装脚本没有权限删
  （那是系统级凭据，由用户的 Windows 账户保护），所以在提示里说清楚。 }
function InitializeUninstall(): Boolean;
var
  Reply: Integer;
  DataDir: String;
begin
  Result := True;
  DataDir := ExpandConstant('{userappdata}\CampusLink');

  if DirExists(DataDir) then
  begin
    Reply := MsgBox('是否同时删除已保存的账号配置和运行日志？' + #13#10 + #13#10 +
                    '目录：' + DataDir + #13#10 + #13#10 +
                    '说明：登录密码保存在 Windows 凭据管理器中，' + #13#10 +
                    '      安装程序无权删除，需要到' + #13#10 +
                    '      「控制面板 → 用户帐户 → 凭据管理器 → Windows 凭据」里' + #13#10 +
                    '      手动删除名为 CampusLink 的条目。',
                    mbConfirmation, MB_YESNO);
    if Reply = IDYES then
      DelTree(DataDir, True, True, True);
  end;
end;

{ ============================================================
  安装前：卸载 WinDivert 内核驱动
  ============================================================

  为什么需要（2026-11-19 实测踩到）：

  pydivert 会把 WinDivert64.sys 注册成内核驱动服务并**常驻**。
  用户只要启用过「共享助手」或「UA 归一」，这个驱动就留在内核里，
  即使 CampusLink 进程已经退出。

  后果：升级安装时 Inno 要覆盖 _internal\pydivert\windivert_dll\WinDivert64.sys，
  文件被内核锁着 → 报错

      DeleteFile failed; code 5. 拒绝访问。
      File: ...\WinDivert64.sys

  用户看到的是「安装失败」，完全想不到跟防检测功能有关。

  修法：安装开始前主动停掉驱动服务。sc stop 成功后再删服务记录，
  这样新版本的 WinDivert64.sys 才能写进去。
  下次启用防检测功能时 pydivert 会自己重新注册。
  ============================================================ }

{ 查服务是否在跑。用 sc query 的输出判断，避免依赖 WMI。
  注意：必须先声明后使用，Inno 的 Pascal 脚本按顺序编译。 }
function IsServiceRunning(const ServiceName: String): Boolean;
var
  ResultCode: Integer;
  TmpFile: String;
  Output: AnsiString;
begin
  Result := False;
  TmpFile := ExpandConstant('{tmp}\_svcquery.txt');
  Exec(ExpandConstant('{cmd}'),
       '/c sc query ' + ServiceName + ' > "' + TmpFile + '" 2>&1',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if LoadStringFromFile(TmpFile, Output) then
  begin
    { 输出里出现 RUNNING 就是还在跑}
    Result := Pos('RUNNING', String(Output)) > 0;
    DeleteFile(TmpFile);
  end;
end;

procedure StopWinDivertDriver();
var
  ResultCode: Integer;
  Attempt: Integer;
begin
  { 先试停止（服务不存在时 sc 返回非 0，忽略即可）}
  Exec(ExpandConstant('{cmd}'), '/c sc stop WinDivert >nul 2>&1',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  { sc stop 是异步的，轮询等它真的停（最多 5 秒）}
  for Attempt := 1 to 5 do
  begin
    Sleep(1000);
    if not IsServiceRunning('WinDivert') then
      Break;
  end;

  { 再删服务记录，让新的 .sys 能覆盖}
  Exec(ExpandConstant('{cmd}'), ' /c sc delete WinDivert >nul 2>&1',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    { 在真正开始复制文件之前 }
    StopWinDivertDriver();
  end;
end;
