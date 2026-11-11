; Inno Setup script for Ae Thicc Launcher (Windows).
;
; Installs the application into the standard Programs folder, like any other
; app: C:\Program Files\AeThiccLauncher (64-bit install mode).
; Runtime data/cache stays in the per-user AppData folder (the historical
; location); the first-run wizard also defaults the game library to C:\SteamRip.
; Both can be moved by the user, on the same or another drive.
;
; Build prerequisites:
;   winget install -e --id JRSoftware.InnoSetup
;   powershell -ExecutionPolicy Bypass -File build_win.ps1
;
; Uninstalling removes only the application folder; data and games persist.

#define AppName "Ae Thicc Launcher"
#define AppExe "Ae_Thicc_Launcher.exe"
; La versione arriva da core/version.py tramite "ISCC /DAppVersion=x.y.z".
; Il valore qui sotto è solo il fallback per una compilazione manuale.
#ifndef AppVersion
  #define AppVersion "1.9.2"
#endif
#define AppPublisher "Ae Thicc"
#define AppId "{{8F3C2B71-9D64-4E0A-B1C5-7A2D9E6F4C13}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
DefaultDirName={autopf}\AeThiccLauncher
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=dist
OutputBaseFilename=Ae_Thicc_Launcher_Setup_{#AppVersion}
SetupIconFile=.media\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Dirs]
; Support folders are implementation details: keep them out of Explorer.
Name: "{app}\.media"; Attribs: hidden
Name: "{app}\bin"; Attribs: hidden
Name: "{app}\data"; Attribs: hidden
Name: "{app}\_internal"; Attribs: hidden
Name: "{app}\lists"; Attribs: hidden

[Files]
Source: "dist\Ae_Thicc_Launcher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent runascurrentuser

[UninstallDelete]
; Only the application folder. User data and the game library are elsewhere.
Type: filesandordirs; Name: "{app}"

[Code]
function NextButtonClick(CurPageID: Integer): Boolean;
var
  ProbeDir, Probe, Previous: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    { The destination folder may not exist yet, so probing inside it would
      always fail. Walk up to the first existing ancestor (e.g. Program Files)
      and test writability there. }
    ProbeDir := WizardForm.DirEdit.Text;
    Previous := '';
    while (ProbeDir <> '') and (not DirExists(ProbeDir)) and (ProbeDir <> Previous) do
    begin
      Previous := ProbeDir;
      ProbeDir := ExtractFileDir(ProbeDir);
    end;
    if ProbeDir = '' then
      ProbeDir := ExtractFileDrive(WizardForm.DirEdit.Text);

    Probe := AddBackslash(ProbeDir) + '.aethicc_probe';
    if SaveStringToFile(Probe, '', False) then
      DeleteFile(Probe)
    else
    begin
      MsgBox('The selected folder is not writable. Choose another folder or run the installer as administrator.',
             mbError, MB_OK);
      Result := False;
    end;
  end;
end;
