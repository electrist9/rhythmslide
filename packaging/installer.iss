; RhythmSlide Inno Setup インストーラ定義
; ビルド済み onedir 出力（dist\rhythmslide）を配布する。
; 参考: https://jrsoftware.org/isinfo.php

#define MyAppName "RhythmSlide"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "RhythmSlide"
#define MyAppExeName "rhythmslide.exe"

[Setup]
AppId={{7C9E1B2A-3D44-4E5F-9A10-RHYTHMSLIDE01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=RhythmSlide-{#MyAppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作成"; GroupDescription: "追加のショートカット:"

[Files]
; PyInstaller onedir 出力フォルダ全体を同梱
Source: "..\dist\rhythmslide\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyAppName} を起動"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; ユーザーキャッシュは削除しない（%LOCALAPPDATA%\RhythmSlide は残す）
