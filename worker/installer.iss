 #define MyAppName "Cerebro Worker"
 #define MyAppExe "CerebroWorker.exe"

[Setup]
AppName={#MyAppName}
AppVersion=1.0
DefaultDirName={localappdata}\Programs\CerebroWorker
DefaultGroupName={#MyAppName}
OutputDir=dist
OutputBaseFilename=CerebroWorkerInstaller
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExe}
WizardStyle=modern

[Files]
Source: "..\dist\CerebroWorker.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\config.json"; DestDir: "{localappdata}\CerebroWorker"; Flags: ignoreversion createallsubdirs onlyifdoesntexist
Source: "..\dist\.env.example"; DestDir: "{localappdata}\CerebroWorker"; Flags: ignoreversion
Source: "..\dist\BUILD.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Cerebro Worker"; Filename: "{app}\{#MyAppExe}"

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Launch Cerebro Worker"; Flags: nowait postinstall skipifsilent
