; ===============================================================
;  CampusLinkCLI 安装程序脚本（Inno Setup 6）—— 无人值守精简版
;
;  编译：
;      ISCC.exe packaging\CampusLinkCLI.iss
;  产物：packaging\output\CampusLinkCLI_Setup_v2.0.0.exe
;
;  与 GUI 版的差异：
;   · 装完即创建两个快捷方式：
;       桌面   CampusLink 认证（无参数，手动点击看结果）
;       启动文件夹 CampusLink 静默认证（--silent，开机全自动，无窗口）
;   · 无程序组、无可勾选项——配置在首次运行时以控制台交互完成，
;     三字段明文存 json（用户拍板，单机场景）
;   · 卸载时一并清理 json 配置（含明文密码，必须清）
; ===============================================================

#define MyAppName "CampusLinkCLI"
#define MyAppNameCN "校园网认证（无人值守）"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "CampusLink"
#define MyAppExeName "CampusLinkCLI.exe"
#define MyAppId "{{9E4B8D32-5C7F-4F3B-A069-3D2G8B0F6C15}"

#define ProjectRoot SourcePath + ".."
#define DistDir ProjectRoot + "\dist-cli\CampusLinkCLI"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName} {#MyAppNameCN}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription={#MyAppName} 安装程序
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=output
OutputBaseFilename=CampusLinkCLI_Setup_v{#MyAppVersion}
SetupIconFile={#ProjectRoot}\campuslink\gui\assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppNameCN}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 静默启动包装器：vbs 以隐藏窗口运行 exe（黑框完全不可见）；
; exe 路径由 vbs 自身的 ScriptFullName 推定，无需安装时替换
Source: "silent_run.vbs.tmpl"; DestDir: "{app}"; DestName: "silent_run.vbs"

[Icons]
; 桌面：手动点击，控制台显示进度与结果
Name: "{autodesktop}\CampusLink 认证"; Filename: "{app}\{#MyAppExeName}"; \
  WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; 桌面 2：修改账号密码用（强制重新配置）
Name: "{autodesktop}\CampusLink 配置"; Filename: "{app}\{#MyAppExeName}"; \
  Parameters: "--setup"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; \
  Tasks: desktopicon
; 启动文件夹：指向 wscript + vbs，开机以隐藏窗口静默认证
Name: "{userstartup}\CampusLink 静默认证"; \
  Filename: "wscript.exe"; Parameters: """{app}\silent_run.vbs"""; \
  WorkingDir: "{app}"; Tasks: startupicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式（手动认证用）"; \
  GroupDescription: "快捷方式:"; Flags: checkedonce
Name: "startupicon"; Description: "开机自动认证（完全静默，无窗口）"; \
  GroupDescription: "快捷方式:"; Flags: checkedonce

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行首次配置"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; json 里存有明文密码，卸载必须清掉
Type: files; Name: "{userappdata}\CampusLink\data\cli_config.json"
Type: files; Name: "{app}\silent_run.vbs"
