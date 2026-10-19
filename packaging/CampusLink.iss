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
#define MyAppVersion "2.0.0"
#define MyAppPublisher "CampusLink"
#define MyAppExeName "CampusLink.exe"
#define MyAppId "{{8F3A7C21-4B6E-4E2A-9D58-2C1F7A9E5B04}"

; 上一级目录（本文件在 packaging/ 下）
#define ProjectRoot SourcePath + ".."
#define DistDir ProjectRoot + "\dist\CampusLink"

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
PrivilegesRequiredOverridesAllowed=dialog

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
DisableDirPage=auto
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
