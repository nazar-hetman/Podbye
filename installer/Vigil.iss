; Inno Setup script for Vigil — https://jrsoftware.org/isinfo.php
;
; Build the app first, then compile this:
;   .venv\Scripts\python.exe -m PyInstaller --noconfirm vigil.spec
;   iscc installer\Vigil.iss
; Output: installer\Output\VigilSetup-<version>.exe
;
; This installs the whole dist\Vigil FOLDER, not a repacked single file.
; That is deliberate and is a licensing requirement, not a packaging habit:
; Vigil links Qt under the LGPL v3, whose section 4(d) requires that whoever
; receives the program can replace the Qt libraries with their own build. The
; DLLs must therefore land on disk as ordinary, replaceable files. Do not
; "improve" this by compressing them into the installer executable itself.

#define AppName        "Vigil"
#define AppVersion     "1.0.0-beta.3"
#define AppPublisher   "Nazar Getman"
#define AppExeName     "Vigil.exe"
#define SourceDir      "..\dist\Vigil"

[Setup]
AppId={{8F3C1A62-4D77-4B21-9E4E-7A1D0C5B9E10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install by default so no admin prompt is needed. Vigil never needs
; elevation to do its own work — only the app's own uninstaller does, and
; Windows handles that.
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=VigilSetup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Shown before install: the user agrees to Vigil's own terms. The bundled
; third-party licenses are installed alongside and linked from the app.
LicenseFile=..\LICENSE
InfoBeforeFile=Readme-before-install.txt
UninstallDisplayIcon={app}\{#AppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english";   MessagesFile: "compiler:Default.isl"
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "french";    MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; recursesubdirs picks up _internal\ — including the replaceable Qt DLLs and
; the bundled license texts.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";                 Filename: "{app}\{#AppExeName}"
Name: "{group}\License";                    Filename: "{app}\_internal\LICENSE"
Name: "{group}\Third-party notices";        Filename: "{app}\_internal\THIRD-PARTY-NOTICES.md"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";           Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Scan sessions and settings live in %APPDATA%\Vigil. They are NOT removed
; automatically — a user reinstalling should not silently lose their history.
; The uninstaller offers it instead (see [Code] below).
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\Vigil');
    if DirExists(DataDir) then
      if MsgBox('Also delete Vigil''s saved scan results and settings?' + #13#10 + #13#10 +
                DataDir + #13#10 + #13#10 +
                'Choose No to keep them for a future reinstall.',
                mbConfirmation, MB_YESNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
