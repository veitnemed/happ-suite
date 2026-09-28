"""Per-user Windows sign-in launch for the packaged start.exe."""

from pathlib import Path
import sys
import winreg


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "HappSuite"


def _command() -> str:
    if not getattr(sys, "frozen", False) or Path(sys.executable).name.lower() != "start.exe":
        raise RuntimeError("Autostart is available in packaged start.exe")
    return f'"{Path(sys.executable).resolve()}" --background'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
        return value == _command()
    except (OSError, RuntimeError):
        return False


def set_enabled(enabled: bool) -> None:
    command = _command()
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                existing, _ = winreg.QueryValueEx(key, VALUE_NAME)
            except FileNotFoundError:
                return
            if existing == command:
                winreg.DeleteValue(key, VALUE_NAME)
