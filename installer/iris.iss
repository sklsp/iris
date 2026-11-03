; Inno Setup 6 script for IrisSetup-<version>.exe. installer/build.ps1 makes the bundle and runs this; see
; docs/desktop-app.md for the decisions behind it.
;
;   iscc /DAppVersion=0.6.0 /DStageDir=C:\path\to\build\Iris [/DNeedsVCRuntime] installer\iris.iss
;
; Per user, no administrator rights, into %LOCALAPPDATA%\Programs\Iris. Iris's own data (~\.iris: settings, skills,
; notes, logs, voice packs) is never part of the install and stays on uninstall unless the user ticks the box.

#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z (installer\build.ps1 does)
#endif
#ifndef StageDir
  #define StageDir "..\build\Iris"
#endif

#define AppName "Iris"
#define AppExe "Iris.exe"
; the same names as APP_ID and APP_MUTEX in iris/app.py (tests/test_installer.py checks)
#define AppUserModelID "sklsp.Iris"
#define AppMutexName "Local\Iris-App"
#define AppArgs "-s -m iris app"
#define WebView2Key "Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

[Setup]
AppId={{6F1C2B9E-3A57-4C1D-9E8B-2D4A7C915E30}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=skl
AppPublisherURL=https://github.com/sklsp/iris
AppSupportURL=https://github.com/sklsp/iris/issues
AppUpdatesURL=https://github.com/sklsp/iris/releases
AppCopyright=MIT License
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Iris setup
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Iris
DisableProgramGroupPage=yes
; like LM Studio: no folder question (the big downloads go to ~\.iris anyway); /DIR=... still works
DisableDirPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputBaseFilename=IrisSetup-{#AppVersion}
SetupIconFile=..\iris\assets\iris.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
LicenseFile=..\LICENSE
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
; the .pyc files were compiled against the files' times; keep those exact whatever the time zone
TimeStampsInUTC=yes
AppMutex={#AppMutexName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Put an Iris shortcut on the desktop"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startatlogin"; Description: "Start Iris in the tray when I sign in"; GroupDescription: "Shortcuts:"; Flags: unchecked

[InstallDelete]
; an upgrade starts from a clean Python: no package of the previous version lingers next to the new one
Type: filesandordirs; Name: "{app}\Lib"
Type: filesandordirs; Name: "{app}\DLLs"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "{#AppArgs}"; WorkingDir: "{%USERPROFILE}"; IconFilename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"; Comment: "Iris, a local voice agent"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "{#AppArgs}"; WorkingDir: "{%USERPROFILE}"; IconFilename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"; Comment: "Iris, a local voice agent"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "{#AppArgs} --background"; WorkingDir: "{%USERPROFILE}"; IconFilename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"; Comment: "Iris in the tray"; Tasks: startatlogin

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "{#AppArgs}"; WorkingDir: "{%USERPROFILE}"; Description: "Start Iris"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; what Python wrote at run time (__pycache__, comtypes' generated modules) lives in these folders
Type: filesandordirs; Name: "{app}\Lib"
Type: filesandordirs; Name: "{app}\DLLs"
Type: filesandordirs; Name: "{app}\tcl"
Type: dirifempty; Name: "{app}"

[Code]
const
  ReparsePoint = $400;

var
  RemoveData: Boolean;

function WebView2Installed(): Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM32, 'SOFTWARE\{#WebView2Key}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM, 'SOFTWARE\{#WebView2Key}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, 'Software\{#WebView2Key}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

procedure OfferDownload(const Text, Url: String);
var
  ErrorCode: Integer;
begin
  if WizardSilent() then
    Log(Text)
  else if MsgBox(Text + #13#10#13#10 + 'Open the download page now? Setup continues either way.',
                 mbConfirmation, MB_YESNO) = IDYES then
    ShellExecAsOriginalUser('open', Url, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not WebView2Installed() then
    OfferDownload('Iris shows its window with Microsoft Edge WebView2, which is not on this PC yet. Windows 11 ' +
                  'has it; on Windows 10 it comes with Microsoft Edge. Install it, then start Iris.',
                  'https://developer.microsoft.com/microsoft-edge/webview2/');
#ifdef NeedsVCRuntime
  if not FileExists(ExpandConstant('{sys}\msvcp140.dll')) then
    OfferDownload('Iris''s speech recognition needs the Microsoft Visual C++ Redistributable (x64), which is not ' +
                  'on this PC yet. Install it (it asks for administrator rights), then start Iris.',
                  'https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist');
#endif
end;

{ --- uninstall: keep ~\.iris unless the user ticks the box ------------------------------------------------------- }

function DataDir(): String;
begin
  Result := ExpandConstant('{%USERPROFILE|}');
  if Result <> '' then
    Result := AddBackslash(Result) + '.iris';
end;

function FolderSize(const Dir: String): Int64;
var
  Rec: TFindRec;
begin
  Result := 0;
  if FindFirst(Dir + '\*', Rec) then
  try
    repeat
      if (Rec.Name <> '.') and (Rec.Name <> '..') then
      begin
        if (Rec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        begin
          if (Rec.Attributes and ReparsePoint) = 0 then
            Result := Result + FolderSize(Dir + '\' + Rec.Name);
        end
        else
        begin
          Result := Result + Rec.SizeLow;
          if Rec.SizeHigh > 0 then
            Result := Result + Int64(Rec.SizeHigh) * 65536 * 65536;
        end;
      end;
    until not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
end;

function SizeText(Bytes: Int64): String;
begin
  if Bytes >= 1073741824 then
    Result := Format('%.1f GB', [Bytes / 1073741824.0])
  else
    Result := Format('%.0f MB', [Bytes / 1048576.0]);
end;

function InitializeUninstall(): Boolean;
var
  Form: TSetupForm;
  Info: TNewStaticText;
  Box: TNewCheckBox;
  UninstallButton, CancelButton: TNewButton;
begin
  Result := True;
  RemoveData := False;
  if UninstallSilent() or (DataDir() = '') or not DirExists(DataDir()) then
    Exit;  { a silent uninstall always keeps the data }
  Form := CreateCustomForm(ScaleX(440), ScaleY(170), False, False);
  try
    Form.Caption := 'Uninstall Iris';
    Form.Position := poScreenCenter;

    Info := TNewStaticText.Create(Form);
    Info.Parent := Form;
    Info.AutoSize := False;
    Info.WordWrap := True;
    Info.Left := ScaleX(16);
    Info.Top := ScaleY(16);
    Info.Width := Form.ClientWidth - ScaleX(32);
    Info.Height := ScaleY(64);
    Info.Caption := 'Iris will be removed from this PC. Your settings, skills, notes, logs and voice packs are in ' +
                    DataDir() + ' (' + SizeText(FolderSize(DataDir())) + '); they stay unless you tick the box, ' +
                    'so reinstalling Iris brings everything back.';

    Box := TNewCheckBox.Create(Form);
    Box.Parent := Form;
    Box.Left := ScaleX(16);
    Box.Top := ScaleY(88);
    Box.Width := Form.ClientWidth - ScaleX(32);
    Box.Height := ScaleY(20);
    Box.Caption := 'Also remove my Iris data';
    Box.Checked := False;

    UninstallButton := TNewButton.Create(Form);
    UninstallButton.Parent := Form;
    UninstallButton.Caption := 'Uninstall';
    UninstallButton.Width := ScaleX(90);
    UninstallButton.Height := ScaleY(26);
    UninstallButton.Left := Form.ClientWidth - ScaleX(16 + 90 + 8 + 90);
    UninstallButton.Top := Form.ClientHeight - ScaleY(16 + 26);
    UninstallButton.ModalResult := mrOk;
    UninstallButton.Default := True;

    CancelButton := TNewButton.Create(Form);
    CancelButton.Parent := Form;
    CancelButton.Caption := 'Cancel';
    CancelButton.Width := ScaleX(90);
    CancelButton.Height := ScaleY(26);
    CancelButton.Left := Form.ClientWidth - ScaleX(16 + 90);
    CancelButton.Top := Form.ClientHeight - ScaleY(16 + 26);
    CancelButton.ModalResult := mrCancel;
    CancelButton.Cancel := True;

    if Form.ShowModal() <> mrOk then
      Result := False
    else
      RemoveData := Box.Checked;
  finally
    Form.Free();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and RemoveData then
    if not DelTree(DataDir(), True, True, True) then
      MsgBox('Some files in ' + DataDir() + ' could not be removed, probably because a program still uses them. ' +
             'Delete the folder yourself later.', mbInformation, MB_OK);
end;
