; Builds EasyRecord-Setup.exe - a normal Windows installer to share.
; Build: run build.bat first, then open this file in Inno Setup 6 and press Compile.

#define AppName "EasyRecord"
#define AppVersion "1.0"

[Setup]
AppId={{6B0E8C4A-3F2D-4E7B-9C51-0A5F1E2D3C4B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=tbrains1998-web
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=EasyRecord-Setup
UninstallDisplayIcon={app}\EasyRecord.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\easyrecord.ico
LicenseFile=FFMPEG-LICENSE.txt

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on my desktop"

[Files]
Source: "..\dist\EasyRecord.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "FFMPEG-LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "GPL-3.0.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\EasyRecord.exe"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\EasyRecord.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\EasyRecord.exe"; Description: "Open EasyRecord now"; Flags: nowait postinstall skipifsilent
