#ifndef AppVersion
  #define AppVersion "2.3.0"
#endif
#ifndef SourceDir
  #error SourceDir must point to the clean PyInstaller onedir output
#endif

[Setup]
AppId={{E8915D01-238E-4FC2-A0CC-244925C6A231}
AppName=Relay Studio
AppVersion={#AppVersion}
AppVerName=Relay Studio {#AppVersion}
UninstallDisplayName=Relay Studio
DefaultDirName={localappdata}\Programs\Relay Studio
DefaultGroupName=Relay Studio
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\RelayStudio.exe
UsePreviousAppDir=yes
OutputDir=..\dist\release
OutputBaseFilename=RelayStudio-Setup-{#AppVersion}-win-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Relay Studio"; Filename: "{app}\RelayStudio.exe"
Name: "{autodesktop}\Relay Studio"; Filename: "{app}\RelayStudio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RelayStudio.exe"; Description: "Launch Relay Studio"; Flags: postinstall nowait skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then begin
    if Exec(ExpandConstant('{app}\RelayStudio.exe'), '--bootstrap-mihomo', '',
      SW_HIDE, ewWaitUntilTerminated, ResultCode) then begin
      if ResultCode <> 0 then
        MsgBox('Relay Studio is installed, but Mihomo could not be verified or downloaded. Open Settings in Relay Studio and retry the Mihomo installation when the network is available.', mbInformation, MB_OK);
    end else
      MsgBox('Relay Studio is installed, but Mihomo bootstrap could not start. Open Settings in Relay Studio to retry.', mbInformation, MB_OK);
  end;
end;
