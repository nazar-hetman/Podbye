; Inno Setup script for Podbye — https://jrsoftware.org/isinfo.php
;
; Build the app first, then compile this:
;   .venv\Scripts\python.exe -m PyInstaller --noconfirm podbye.spec
;   iscc installer\Podbye.iss
; Output: installer\Output\PodbyeSetup-<version>.exe
;
; This installs the whole dist-beta5\Podbye FOLDER, not a repacked single file.
; That is deliberate and is a licensing requirement, not a packaging habit:
; Podbye links Qt under the LGPL v3, whose section 4(d) requires that whoever
; receives the program can replace the Qt libraries with their own build. The
; DLLs must therefore land on disk as ordinary, replaceable files. Do not
; "improve" this by compressing them into the installer executable itself.

#define AppName        "Podbye"
#define AppVersion     "1.0.0-beta.5"
#define AppPublisher   "Nazar Hetman"
#define AppExeName     "Podbye.exe"
#define SourceDir      "..\dist-beta5\Podbye"

[Setup]
AppId={{8F3C1A62-4D77-4B21-9E4E-7A1D0C5B9E10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user by default: {autopf} resolves to %LOCALAPPDATA%\Programs\Podbye
; and no elevation is asked for. Podbye never needs elevation to do its
; own work — the scanner records permission-denied paths and moves on — so
; the only thing admin would buy is the folder name.
;
; That trade is worse than it looks for this build. The executable is
; unsigned, so a first run already costs a SmartScreen warning; a UAC
; prompt on top of it makes two alarming dialogs before the program
; opens, to install a cleanup tool. Per-user is also what the tools this
; sits beside do — VS Code, Discord, Ollama, LM Studio all land in
; %LOCALAPPDATA%\Programs.
;
; Anyone who wants C:\Program Files\Podbye still gets it from the
; install-mode page, or with /ALLUSERS.
PrivilegesRequired=lowest
; 'commandline' as well as 'dialog' so /ALLUSERS and /CURRENTUSER work:
; an unattended deployment can pick a mode without a human at the
; wizard, and the install can be verified in both modes. Neither switch
; skips elevation — /ALLUSERS still prompts for it.
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir=Output
OutputBaseFilename=PodbyeSetup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Shown before install: the user agrees to Podbye's own terms. The bundled
; third-party licenses are installed alongside and linked from the app.
LicenseFile=..\LICENSE
InfoBeforeFile=Readme-before-install.txt
UninstallDisplayIcon={app}\{#AppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
; One entry per language the application itself ships (app/locales/, and
; i18n.LANGUAGES). An installer that speaks fewer languages than the
; program it installs greets someone in English and then switches on them.
Name: "english";   MessagesFile: "compiler:Default.isl"
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "german";    MessagesFile: "compiler:Languages\German.isl"
Name: "spanish";   MessagesFile: "compiler:Languages\Spanish.isl"
Name: "french";    MessagesFile: "compiler:Languages\French.isl"
Name: "polish";    MessagesFile: "compiler:Languages\Polish.isl"

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
; Scan sessions and settings live in %APPDATA%\Podbye. They are NOT removed
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
    DataDir := ExpandConstant('{userappdata}\Podbye');
    // Never during a silent uninstall. There is nobody to ask, and the
    // value this box returns when it cannot be shown was Yes — so
    // `unins000.exe /VERYSILENT /SUPPRESSMSGBOXES`, the form an unattended
    // deployment or an upgrade script uses, destroyed every scan record and
    // setting the user had, without asking and without saying so. Not being
    // able to ask now means not deleting, which is what the note above
    // always intended.
    //
    // MB_DEFBUTTON2 makes No the default for the interactive case as well,
    // so leaning on Enter keeps the data rather than removing it.
    if DirExists(DataDir) and (not UninstallSilent) then
      if MsgBox('Also delete Podbye''s saved scan results and settings?' + #13#10 + #13#10 +
                DataDir + #13#10 + #13#10 +
                'Choose No to keep them for a future reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
