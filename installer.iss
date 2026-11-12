; Q 텍스트 추출 설치 프로그램 (Inno Setup 6)
; 빌드: ISCC.exe installer.iss  ->  installer_output\QTextExtract-Setup.exe
#define MyAppName "Q 텍스트 추출"
#define MyAppExeName "QTextExtract.exe"
#define MyAppVersion "1.0.0"

[Setup]
AppId={{7B1E5C3A-4D2F-4F6E-9A58-3C0D6E8B21F4}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Q Tools
; 관리자 권한 없이 내 계정에만 설치 (회사 PC에서도 설치 가능)
PrivilegesRequired=lowest
DefaultDirName={autopf}\QTextExtract
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=QTextExtract-Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\QTextExtract.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
ChangesAssociations=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
#if FileExists(CompilerPath + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#else
Name: "english"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "추가 작업:"

[Files]
Source: "dist\QTextExtract\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; 바로가기 전용 아이콘 파일 (바탕화면이 옛 아이콘 기억을 쓰지 않도록 따로 둔다)
Source: "assets\icon.ico"; DestDir: "{app}"; DestName: "QText.ico"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\QText.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\QText.ico"; Tasks: desktopicon

[Registry]
; 프로그램 설정(언어, 정리 옵션 등)은 제거할 때 함께 지운다
Root: HKCU; Subkey: "Software\QTextExtract"; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "설치가 끝나면 바로 실행"; Flags: nowait postinstall skipifsilent
