"""Private Mihomo runtime paths, DPAPI secret storage, and config rendering."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
from urllib.parse import urlsplit

from .vpn_backend import SubscriptionError


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise OSError("Windows DPAPI is required to protect Mihomo credentials")
    crypt = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    kernel = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    crypt.CryptProtectData.argtypes = (
        ctypes.POINTER(DataBlob), ctypes.wintypes.LPCWSTR, ctypes.POINTER(DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.POINTER(DataBlob),
    )
    crypt.CryptProtectData.restype = ctypes.wintypes.BOOL
    crypt.CryptUnprotectData.argtypes = (
        ctypes.POINTER(DataBlob), ctypes.POINTER(ctypes.wintypes.LPWSTR),
        ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.wintypes.DWORD, ctypes.POINTER(DataBlob),
    )
    crypt.CryptUnprotectData.restype = ctypes.wintypes.BOOL
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel.LocalFree.restype = ctypes.c_void_p
    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = DataBlob()
    if decrypt:
        ok = crypt.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(target)
        )
    else:
        ok = crypt.CryptProtectData(
            ctypes.byref(source), "Happ Suite VPN credentials", None, None, None,
            0x1, ctypes.byref(target),
        )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel.LocalFree(target.pbData)


class RuntimePaths:
    def __init__(self, root: Path | None = None):
        if root is None:
            local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            root = local / "HappSuite" / "vpn"
        self.root = Path(root)
        self.mihomo_root = self.root / "mihomo"
        self.binary_root = self.mihomo_root / "1.19.31"
        self.binary = self.binary_root / "mihomo.exe"
        self.config_root = self.mihomo_root / "config"
        self.providers_root = self.mihomo_root / "providers"
        self.state_root = self.mihomo_root / "state"
        self.config = self.config_root / "config.yaml"
        self.subscription_config = self.config_root / "subscription.yaml"
        self.secrets = self.state_root / "credentials.dpapi"
        self.instance = self.state_root / "current.json"
        self.launch_lock = self.state_root / "launch.lock"
        self.state = self.state_root / "state.json"
        self.installation_id = self.state_root / "installation-id"

    def ensure(self):
        for path in (self.binary_root, self.config_root, self.providers_root, self.state_root):
            path.mkdir(parents=True, exist_ok=True)


class SecretStore:
    """Store subscription URL and controller secret as one DPAPI-protected blob."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, str]:
        try:
            payload = _dpapi(self.path.read_bytes(), decrypt=True)
            value = json.loads(payload.decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, UnicodeError):
            return {}

    def save(self, values: dict[str, str]):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        protected = _dpapi(encoded, decrypt=False)
        _atomic_write(self.path, protected)


def _atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def validate_subscription_url(value: str) -> str:
    url = value.strip()
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.fragment:
            raise ValueError
        if len(url) > 8192:
            raise ValueError
    except ValueError as exc:
        raise SubscriptionError("Нужна HTTPS-ссылка Mihomo/Clash-compatible subscription") from exc
    return url


def safe_url_label(url: str) -> str:
    parsed = urlsplit(url)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"{parsed.hostname or 'provider'} / primary / sha256(url)={digest}"


def new_controller_secret() -> str:
    return secrets.token_urlsafe(32)


def render_config(subscription_profile: dict, api_secret: str, *, strict_route: bool = True,
                  tun_stack: str = "mixed", controller_port: int = 19090) -> str:
    """Build an isolated full-tunnel config from a fetched, parsed local profile."""
    if tun_stack not in {"mixed", "system"}:
        raise ValueError("unsupported Mihomo TUN stack")
    import yaml

    proxies = subscription_profile.get("proxies") if isinstance(subscription_profile, dict) else None
    if not isinstance(proxies, list) or not proxies:
        raise ValueError("Mihomo subscription profile has no proxies")
    names = [proxy.get("name") for proxy in proxies if isinstance(proxy, dict)]
    if len(names) != len(proxies) or any(not isinstance(name, str) or not name for name in names):
        raise ValueError("Mihomo subscription profile has invalid proxies")
    managed = dict(subscription_profile)
    for key in (
        "external-controller", "secret", "external-controller-cors", "external-controller-unix",
        "allow-lan", "bind-address", "authentication", "listeners", "port", "socks-port",
        "redir-port", "tproxy-port", "mixed-port", "proxy-providers", "rule-providers",
    ):
        managed.pop(key, None)
    managed.update({
        "mode": "rule",
        "log-level": "warning",
        "allow-lan": False,
        "bind-address": "127.0.0.1",
        "external-controller": f"127.0.0.1:{int(controller_port)}",
        "secret": api_secret,
        "proxies": proxies,
        "proxy-groups": [{"name": "VPN", "type": "select", "proxies": names}],
        "rules": ["MATCH,VPN"],
        "tun": {
            "enable": True,
            "stack": tun_stack,
            "device": "Mihomo",
            "auto-route": True,
            "auto-detect-interface": True,
            "strict-route": bool(strict_route),
        },
    })
    return yaml.safe_dump(managed, allow_unicode=True, sort_keys=False)
