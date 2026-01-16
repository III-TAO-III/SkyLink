; --- НАСТРОЙКИ ПРОЕКТА ---
#define MyAppName "SkyLink Agent"
#define MyAppVersion "0.81"
#define MyAppPublisher "SkyBioML"
#define MyAppURL "https://github.com/III-TAO-III/SkyLink"
#define MyAppExeName "SkyLinkV0.81.exe"

[Setup]
; Уникальный ID приложения (сгенерирован случайно, можно оставить этот)
AppId={{A4B3C2D1-E5F6-4789-A1B2-C3D4E5F67890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Куда сохранять готовый setup.exe (в корень проекта)
OutputDir=.
OutputBaseFilename=SkyLink_Setup_v0.81
; Иконка самого инсталлятора
SetupIconFile=icon.ico

; Папка установки по умолчанию (Program Files\SkyLink Agent)
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
; Требовать права админа для установки (чтобы писать в Program Files)
PrivilegesRequired=admin

; Сжатие
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
; Чекбокс "Создать значок на рабочем столе" (теперь включен по умолчанию)
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

; Чекбокс "Запускать вместе с Windows" (теперь включен по умолчанию)
Name: "startup"; Description: "Запускать SkyLink Agent автоматически при входе в систему"; GroupDescription: "Автозагрузка:"

[Files]
; Откуда берем файлы. Внимание: предполагается, что exe лежит в папке dist
Source: "dist\SkyLinkV0.81\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Если иконка нужна отдельно (обычно она уже внутри exe, но на всякий случай)
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Логика автозагрузки: пишем в реестр Windows (HKCU Run), если пользователь поставил галочку
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "SkyLinkAgent"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
; Предложить запустить программу после установки
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent