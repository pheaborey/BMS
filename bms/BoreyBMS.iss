#define AppName "BoreyBMS"
#define AppVersion "1.0.0"
#define AppPublisher "BoreyBMS"
#define AppExeName "MeridianBMS.exe"

[Setup]
AppId={{87D6B2A7-3B5B-4E24-B97E-DAED435C707C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\BoreyBMS
UsePreviousAppDir=no
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir=dist
OutputBaseFilename=BoreyBMS-Setup
SetupIconFile=static\borey-bms.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter={#AppExeName}
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CopyDirectoryMissingFiles(SourceDir, DestDir: String);
var
  FindRec: TFindRec;
  SourcePath, DestPath: String;
begin
  if not DirExists(SourceDir) then
    Exit;
  if not ForceDirectories(DestDir) then
    RaiseException('Could not create the BoreyBMS data directory: ' + DestDir);
  if FindFirst(AddBackslash(SourceDir) + '*', FindRec) then
  try
    repeat
      if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
      begin
        SourcePath := AddBackslash(SourceDir) + FindRec.Name;
        DestPath := AddBackslash(DestDir) + FindRec.Name;
        if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          CopyDirectoryMissingFiles(SourcePath, DestPath)
        else if not FileExists(DestPath) and not CopyFile(SourcePath, DestPath, False) then
          RaiseException('Could not migrate existing BoreyBMS data: ' + SourcePath);
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  OldDataDir, NewDatabase: String;
begin
  if CurStep = ssPostInstall then
  begin
    OldDataDir := ExpandConstant('{localappdata}\Programs\MeridianBMS\data');
    NewDatabase := ExpandConstant('{app}\data\borey_bms.db');
    if not FileExists(NewDatabase) then
      CopyDirectoryMissingFiles(OldDataDir, ExpandConstant('{app}\data'));
  end;
end;
