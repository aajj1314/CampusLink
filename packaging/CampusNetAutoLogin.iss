; ===============================================================
;  CampusLink 静默版 安装程序脚本（Inno Setup 6）
;
;  编译：packaging\build_installer.bat
;  产物：packaging\output\CampusLink_Silent_Setup_v2.0.0.exe
;
;  设计：
;   · 安装到 Program Files，需要管理员权限
;   · 安装向导内填写学工号、上网账号、密码
;   · 配置写入 %APPDATA%\CampusLinkSilent\config.json
;   · 卸载时结束进程、删除自启项、清空配置与日志
; ===============================================================

#define MyAppName "CampusNetAutoLogin"
#define MyAppNameCN "CampusLink 静默版"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "CampusLink"
#define MyAppExeName "CampusNetAutoLogin.exe"
#define MyAppId "{{C06D3109-E249-434C-A568-B1EE9CFDFCBB}}"

#define ProjectRoot SourcePath + ".."
#define DistDir ProjectRoot + "\dist\CampusNetAutoLogin"

[Setup]
AppId={#MyAppId}
AppName={#MyAppNameCN}
AppVersion={#MyAppVersion}
AppVerName={#MyAppNameCN} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription={#MyAppNameCN} 安装程序
VersionInfoCompany={#MyAppPublisher}

DefaultDirName={autopf}\CampusLinkSilent
DefaultGroupName={#MyAppNameCN}
DisableProgramGroupPage=yes
AllowNoIcons=yes

PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline

OutputDir=output
OutputBaseFilename=CampusLink_Silent_Setup_v{#MyAppVersion}
SetupIconFile={#ProjectRoot}\packaging\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppNameCN} {#MyAppVersion}

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
DisableDirPage=auto
DisableReadyPage=no
ShowLanguageDialog=no
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建“查看运行日志”桌面快捷方式"; GroupDescription: "附加任务:"
Name: "startup";     Description: "开机自动启动（登录 Windows 后自动认证）"; GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppNameCN}";          Filename: "{app}\{#MyAppExeName}"
Name: "{group}\查看运行日志";              Filename: "{sys}\notepad.exe"; Parameters: """{userappdata}\CampusLinkSilent\logs\app.log"""
Name: "{group}\卸载 {#MyAppNameCN}";      Filename: "{uninstallexe}"
Name: "{autodesktop}\校园网认证日志";      Filename: "{sys}\notepad.exe"; Parameters: """{userappdata}\CampusLinkSilent\logs\app.log"""; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "CampusLinkSilent"; \
    ValueData: """{app}\{#MyAppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppNameCN}"; \
    Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im {#MyAppExeName}"; \
    Flags: runhidden; RunOnceId: "KillRunning"

[UninstallDelete]
; 卸载即清空，保证下次重装是全新状态
Type: filesandordirs; Name: "{userappdata}\CampusLinkSilent"
Type: files; Name: "{autodesktop}\校园网认证日志.lnk"
Type: files; Name: "{userdesktop}\校园网认证日志.lnk"
Type: files; Name: "{group}\查看运行日志.lnk"
Type: files; Name: "{userprograms}\CampusLink 静默版\查看运行日志.lnk"

[Code]
var
  AccountPage: TInputQueryWizardPage;

procedure InitializeWizard;
begin
  AccountPage := CreateInputQueryPage(wpSelectTasks,
    '校园网账号',
    '填写校园网认证信息',
    '请输入学(工)号、上网账号和密码。安装完成后程序会用这些信息自动登录校园网。' + #13#10 +
    '密码会以明文写入当前用户的 AppData 目录，请勿在公用电脑上保存。');
  AccountPage.Add('学(工)号：', False);
  AccountPage.Add('上网账号：', False);
  AccountPage.Add('密码：', True);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if WizardSilent then
    exit;
  if CurPageID = AccountPage.ID then
  begin
    if Trim(AccountPage.Values[0]) = '' then
    begin
      MsgBox('请填写学(工)号。', mbError, MB_OK);
      Result := False;
      exit;
    end;
    if Trim(AccountPage.Values[1]) = '' then
    begin
      MsgBox('请填写上网账号。', mbError, MB_OK);
      Result := False;
      exit;
    end;
    if Trim(AccountPage.Values[2]) = '' then
    begin
      MsgBox('请填写密码。', mbError, MB_OK);
      Result := False;
      exit;
    end;
  end;
end;

function JsonEscape(const Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir: String;
  Json: String;
begin
  if CurStep = ssPostInstall then
  begin
    if WizardSilent then
      exit;

    { 静默安装时没有填写页面，保留已有配置，不覆盖 }
    if (Trim(AccountPage.Values[0]) = '') and
       (Trim(AccountPage.Values[1]) = '') and
       (Trim(AccountPage.Values[2]) = '') then
      exit;

    DataDir := ExpandConstant('{userappdata}\CampusLinkSilent');
    ForceDirectories(DataDir);
    Json := '{"account":{"student_id":"' + JsonEscape(AccountPage.Values[0]) +
            '","username":"' + JsonEscape(AccountPage.Values[1]) +
            '","password":"' + JsonEscape(AccountPage.Values[2]) + '"}}';
    SaveStringToFile(DataDir + '\config.json', Json, False);
  end;
end;
