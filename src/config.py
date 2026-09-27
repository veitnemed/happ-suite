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


def _find_zapret_dir() -> Optional[str]:
    """Auto-discover zapret installation directory."""
    candidates = [
        # In the same directory as the suite
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "components", "zapret"),
        # Common locations
        r"C:\zapret",
        r"D:\zapret",
    ]
    # Also search for zapret-discord-youtube-* folders near the script
    script_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "components", "ag_unlocker", "ag_dns.exe"),
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
        # Determine base directory (handles PyInstaller bundle)
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        default_path = os.path.join(base_dir, "config", "default.json")
        if not os.path.exists(default_path):
            exe_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else base_dir
            default_path = os.path.join(exe_dir, "config", "default.json")

        if os.path.exists(default_path):
            with open(default_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._data = {
                "happ_exe": r"C:\Program Files\FlyFrogLLC\Happ\Happ.exe",
                "proxy_port": 10809,
                "proxy_url": "http://127.0.0.1:10809",
                "registry_pref": r"HKCU\Software\Happ\OrganizationDefaults\Preferences",
                "servers": [
                    {"id": 264383027, "label": "[DE] Германия #2", "name": "Германия #2", "country": "DE", "category": "foreign", "host": "167.233.117.249", "port": 443},
                    {"id": 1322034547, "label": "[DE] Германия", "name": "Германия", "country": "DE", "category": "foreign", "host": "138.199.170.143", "port": 443},
                    {"id": 2200331173, "label": "[FI] Финляндия [GRPC]", "name": "Финляндия ⚡️ [GRPC]", "country": "FI", "category": "foreign", "host": "135.181.211.216", "port": 443},
                    {"id": 3478276439, "label": "[RU] Россия (Антизаглушка)", "name": "Россия | YouTube без рекламы", "country": "RU", "category": "domestic", "host": "5.129.194.228", "port": 443}
                ],
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
