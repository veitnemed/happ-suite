"""
Configuration manager for Happ Suite.
Loads default.json, merges with user overrides, auto-discovers component paths.
"""
import json
import os
import sys
import glob
from pathlib import Path
from typing import Optional, Dict, Any, List


def _application_dir() -> Path:
    """Return the project root or the directory containing the packaged exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _find_zapret_dir() -> Optional[str]:
    """Auto-discover zapret installation directory."""
    candidates = [
        # In the same directory as the suite
        str(_application_dir() / "components" / "zapret"),
        # Common locations
        r"C:\zapret",
        r"D:\zapret",
    ]
    # Also search for zapret-discord-youtube-* folders near the script
    script_parent = str(_application_dir())
    for folder in glob.glob(os.path.join(script_parent, "zapret-discord-youtube*")):
        if os.path.isdir(folder):
            candidates.insert(0, folder)

    for path in candidates:
        if os.path.isdir(path) and os.path.exists(os.path.join(path, "bin", "winws.exe")):
            return path
    return None


def _find_ag_dns() -> Optional[str]:
    """Auto-discover ag_dns.exe path."""
    candidates = [
        r"C:\ProgramData\AGUnlocker\ag_dns.exe",
        str(_application_dir() / "components" / "ag_unlocker" / "ag_dns.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


class Config:
    """Configuration holder with auto-discovery."""

    def __init__(self, config_path: Optional[str] = None):
        self._data: Dict[str, Any] = {}
        self._config_path = config_path
        # The onedir build keeps default.json beside HappSuite.exe. Check that
        # location first, even when PyInstaller also defines _MEIPASS.
        default_path = _application_dir() / "config" / "default.json"
        if not default_path.is_file() and getattr(sys, "frozen", False):
            legacy_path = Path(getattr(sys, "_MEIPASS", _application_dir())) / "config" / "default.json"
            if legacy_path.is_file():
                default_path = legacy_path

        if default_path.is_file():
            with default_path.open("r", encoding="utf-8") as f:
                self._data = json.load(f)
        elif getattr(sys, "frozen", False):
            raise FileNotFoundError(f"Happ Suite configuration is missing: {default_path}")
        else:
            self._data = {
                "happ_exe": r"C:\Program Files\FlyFrogLLC\Happ\Happ.exe",
                "proxy_port": 10809,
                "proxy_url": "http://127.0.0.1:10809",
                "registry_pref": r"HKCU\Software\Happ\OrganizationDefaults\Preferences",
                "servers": [],
                "health_check_interval_sec": 30,
                "tunnel_wait_timeout_sec": 45,
                "auto_reconnect_after_sleep": True,
                "check_urls": {
                    "youtube": "https://www.youtube.com",
                    "chatgpt": "https://chatgpt.com",
                    "gemini": "https://gemini.google.com",
                    "runet": "https://ya.ru"
                }
            }

        # Credentials and machine-specific overrides live outside the checkout
        # so a packaged build cannot accidentally embed them.
        local_root = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        local_path = os.path.join(local_root, "HappSuite", "local.json")
        if os.path.exists(local_path):
            with open(local_path, "r", encoding="utf-8") as f:
                self._data.update(json.load(f))

        # Load user overrides
        if config_path and os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                user_data = json.load(f)
                self._data.update(user_data)

        # Auto-discover paths if not set
        if not self._data.get("zapret_dir"):
            self._data["zapret_dir"] = _find_zapret_dir()

        if not self._data.get("ag_dns_path"):
            self._data["ag_dns_path"] = _find_ag_dns()

    @property
    def happ_exe(self) -> str:
        return self._data.get("happ_exe", r"C:\Program Files\FlyFrogLLC\Happ\Happ.exe")

    @property
    def proxy_port(self) -> int:
        return self._data.get("proxy_port", 10809)

    @property
    def proxy_url(self) -> str:
        return self._data.get("proxy_url", f"http://127.0.0.1:{self.proxy_port}")

    @property
    def zapret_dir(self) -> Optional[str]:
        return self._data.get("zapret_dir")

    @property
    def zapret_strategy(self) -> str:
        return self._data.get("zapret_strategy", "general.bat")

    @property
    def ag_dns_path(self) -> Optional[str]:
        return self._data.get("ag_dns_path")

    @property
    def ag_unlocker_key(self) -> Optional[str]:
        return self._data.get("ag_unlocker_key")

    @property
    def ag_unlocker_port(self) -> int:
        return self._data.get("ag_unlocker_port", 53129)

    @property
    def registry_pref(self) -> str:
        return self._data.get("registry_pref", r"HKCU\Software\Happ\OrganizationDefaults\Preferences")

    @property
    def servers(self) -> List[Dict[str, Any]]:
        return self._data.get("servers", [])

    @property
    def foreign_servers(self) -> List[Dict[str, Any]]:
        return [s for s in self.servers if s.get("category") == "foreign"]

    @property
    def domestic_servers(self) -> List[Dict[str, Any]]:
        return [s for s in self.servers if s.get("category") == "domestic"]

    @property
    def health_check_interval(self) -> int:
        return self._data.get("health_check_interval_sec", 30)

    @property
    def tunnel_wait_timeout(self) -> int:
        return self._data.get("tunnel_wait_timeout_sec", 45)

    @property
    def check_urls(self) -> Dict[str, str]:
        return self._data.get("check_urls", {})

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def __repr__(self):
        return (
            f"Config(happ={self.happ_exe!r}, zapret={self.zapret_dir!r}, "
            f"ag_dns={self.ag_dns_path!r}, servers={len(self.servers)})"
        )
