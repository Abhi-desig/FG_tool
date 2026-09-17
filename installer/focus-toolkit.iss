; Focus Toolkit — Windows installer
;
; Built by .github/workflows/installer.yml on a windows-latest runner. It
; consumes the folder that `scripts/package_windows.py --for-installer` stages,
; plus a `runtime\` folder holding a relocatable Python environment. Everything
; the app needs is inside; nothing is downloaded at install time or first run.
;
; Compile:  ISCC.exe /DStagingDir=... /DAppVersion=... installer\focus-toolkit.iss

#ifndef StagingDir
  #define StagingDir "..\dist\focus-toolkit-shop"
#endif
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

#define AppName "Focus Toolkit"
#define AppExe "FocusToolkit.bat"

[Setup]
AppId={{B2F1C7E4-9D3A-4F6B-8E15-7C0A2D4E8F31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Focus Toolkit
DefaultDirName={localappdata}\Programs\FocusToolkit
DefaultGroupName={#AppName}
OutputBaseFilename=FocusToolkit-Setup
OutputDir=..\dist

; Per-user. No admin password, no UAC prompt -- and, the reason it matters,
; %LOCALAPPDATA% is writable by the person who installed it. The app writes
; data.db, models\ and tmp\ next to itself and appends learned names into
; data\names\exceptions.tsv; under Program Files every one of those fails, the
; last one silently. See ADR-038.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; The payload is ~3 GB of .onnx and .safetensors, which are already compressed.
; lzma2/max would spend an extra half hour to save almost nothing, and solid
; compression on a payload this size makes the installer slow to start.
Compression=lzma2/fast
SolidCompression=no

DisableProgramGroupPage=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
; A rough floor: the staged folder plus room for the database and scratch files.
ExtraDiskSpaceRequired=209715200

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a shortcut on the Desktop"; GroupDescription: "Shortcuts:"

[Files]
; Everything except the two files the app itself writes to.
Source: "{#StagingDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; \
  Excludes: "data\names\exceptions.tsv,data\places\gazetteer.tsv"

; These two are shipped content that the app APPENDS TO at runtime: every name
; the operator teaches it is written back into them (features/translit.py).
; Overwriting on upgrade would silently delete their corrections, so a copy that
; is already there is left alone. The cost is that new shipped entries do not
; reach an existing install -- the lesser loss of the two. ADR-038.
Source: "{#StagingDir}\data\names\exceptions.tsv"; DestDir: "{app}\data\names"; Flags: onlyifdoesntexist
Source: "{#StagingDir}\data\places\gazetteer.tsv"; DestDir: "{app}\data\places"; Flags: onlyifdoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\Read me first"; Filename: "{app}\README-FIRST.txt"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\README-FIRST.txt"; Description: "Open the read-me"; \
  Flags: postinstall shellexec skipifsilent unchecked
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName} now"; WorkingDir: "{app}"; \
  Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Scratch only. `tmp` is recreated on every boot and holds nothing the operator
; typed (backend/main.py wipes it at startup), so leaving it behind would just
; be litter.
Type: filesandordirs; Name: "{app}\tmp"

; Deliberately NOT removed on uninstall:
;   data.db        the operator's clients, glossary and remembered corrections
;   data\names\exceptions.tsv, data\places\gazetteer.tsv   names they taught it
; Uninstalling to fix a problem must not destroy months of their work. Inno
; leaves the folder in place when files it did not install remain, which is the
; behaviour wanted here.
