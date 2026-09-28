# Happ Suite

Happ Suite is a Windows system-tray controller for Happ VPN and the installed
Antigravity Unlocker background service.

## Controls

- Press **F8** or choose **F8 — включить / выключить** from the tray menu to
  toggle the components.
- Gray means off, yellow means starting, green means the Happ HTTPS proxy and
  Unlocker listener respond, and red means an error.
- The tray menu also contains **Выход**.
- Starting Happ Suite does not connect the VPN automatically.

The suite does not open a console, browser, or Unlocker setup window while
handling F8. It checks the Unlocker service already installed by AG Unlocker;
that service is normally started by Windows at sign-in. The license key is kept
in `%LOCALAPPDATA%\HappSuite\local.json`, outside the repository and packaged
builds.

## Happ behavior

The installed Happ 4.3.0 exposes a Windows service, but its public release notes
do not document a command-line connect/disconnect interface. When no Happ process
is open, the suite launches `Happ.exe` with the Windows initial-show state set
to hidden, then checks HTTPS through Happ's configured local proxy. Whether the
client connects on launch follows the settings already configured in Happ.

The suite does not kill an unrelated `Happ.exe` process or stop the shared
`HappService`. If a VPN session was already active before the suite started, it
leaves that session untouched when disabling.

## Build

Install the dependencies from `pyproject.toml` and PyInstaller, then run:

```powershell
py -3 scripts/build.py
```

The generated executable is written to `dist\HappSuite.exe` and is built
without a console window.
