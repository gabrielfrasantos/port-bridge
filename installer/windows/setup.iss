; Inno Setup script for port-bridge GUI installer (Windows)
; Build: iscc setup.iss /DAppVersion=0.1.0
; Output: installer-output\port-bridge-0.1.0-windows-setup.exe

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{B3F2A1C4-7E8D-4F5A-9B2C-1D3E5F7A9B0C}
AppName=port-bridge
AppVersion={#AppVersion}
AppPublisher=Gabriel Santos
AppPublisherURL=https://github.com/gabrielfrasantos/port-bridge
AppSupportURL=https://github.com/gabrielfrasantos/port-bridge/issues
AppUpdatesURL=https://github.com/gabrielfrasantos/port-bridge/releases
DefaultDirName={autopf}\port-bridge
DefaultGroupName=port-bridge
AllowNoIcons=yes
; Place output next to this script so the workflow can find it easily
OutputDir=..\..\installer-output
OutputBaseFilename=port-bridge-{#AppVersion}-windows-setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Run as user — no admin required for autopf on modern Windows
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\port-bridge-gui.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";  Description: "{cm:CreateDesktopIcon}"; \
      GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon";  Description: "Launch port-bridge when Windows starts"; \
      GroupDescription: "Startup options:"; Flags: unchecked

[Files]
Source: "..\..\dist\port-bridge-gui.exe"; DestDir: "{app}"; \
        Flags: ignoreversion

[Icons]
Name: "{group}\port-bridge";                    Filename: "{app}\port-bridge-gui.exe"
Name: "{group}\{cm:UninstallProgram,port-bridge}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\port-bridge";              Filename: "{app}\port-bridge-gui.exe"; \
      Tasks: desktopicon
Name: "{userstartup}\port-bridge";              Filename: "{app}\port-bridge-gui.exe"; \
      Tasks: startupicon

[Run]
Filename: "{app}\port-bridge-gui.exe"; \
          Description: "{cm:LaunchProgram,port-bridge}"; \
          Flags: nowait postinstall skipifsilent
