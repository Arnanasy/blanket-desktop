; Blanket Windows 标准安装包脚本（Inno Setup 6）
#ifndef MyAppVersion
  #define MyAppVersion "1.0.2"
#endif

[Setup]
AppId={{8D6B4F3E-6B91-4B8E-A3E4-9C4D5F1A2B11}
AppName=Blanket
AppVersion={#MyAppVersion}
AppPublisher=Eleven
AppPublisherURL=https://github.com/Arnanasy/blanket-desktop
AppSupportURL=https://github.com/Arnanasy/blanket-desktop/issues
DefaultDirName={localappdata}\Programs\Blanket
DefaultGroupName=Blanket
DisableProgramGroupPage=yes
LicenseFile=LICENSE
OutputDir=dist
OutputBaseFilename=Blanket-Setup
SetupIconFile=Blanket.ico
UninstallDisplayIcon={app}\Blanket.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
CloseApplications=force

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut (创建桌面快捷方式)"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "dist\Blanket.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Blanket"; Filename: "{app}\Blanket.exe"
Name: "{group}\Uninstall Blanket"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Blanket"; Filename: "{app}\Blanket.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Blanket.exe"; Description: "Launch Blanket (立即启动 Blanket)"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\Blanket_error.log*"
Type: files; Name: "{app}\config.json"

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C reg delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v Blanket /f >nul 2>&1 & reg delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v JingZhuoMian /f >nul 2>&1 & reg delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"" /v Blanket /f >nul 2>&1 & reg delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"" /v JingZhuoMian /f >nul 2>&1"; Flags: runhidden
