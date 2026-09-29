"""Reject user runtime data from distributable onedir build trees."""

from __future__ import annotations

import json
from pathlib import Path


PRIVATE_FILENAMES = {
    "local.json",
    "hotkeys.json",
    "gate.json",
    "state.json",
    "current.json",
    "credentials.dpapi",
    "subscription.yaml",
    "config.yaml",
    "installation-id",
}
PRIVATE_CONFIG_KEYS = {
    "subscription_url", "mihomo_subscription_url", "controller_secret",
    "ag_unlocker_key", "api_key", "password", "private_key", "secret", "token",
}


def _check_default_config(config_path: Path) -> None:
    value = json.loads(config_path.read_text(encoding="utf-8"))

    def visit(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key).casefold() in PRIVATE_CONFIG_KEYS:
                    raise ValueError("Packaged default config contains a private setting")
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)


def inspect_package_tree(root: Path, *, executable: str = "RelayStudio.exe") -> None:
    """Validate required application files and exclude private runtime artifacts."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError("Application build directory does not exist")
    if not (root / executable).is_file():
        raise FileNotFoundError(f"Package is missing {executable}")
    config = root / "config" / "default.json"
    if not config.is_file():
        raise FileNotFoundError("Package is missing config/default.json")
    _check_default_config(config)

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        lowered = [part.casefold() for part in relative.parts]
        if any(part in {"logs", "userdata", "user-data", "state"} for part in lowered):
            raise ValueError("Package contains user runtime data")
        if path.name.casefold() in PRIVATE_FILENAMES or path.suffix.casefold() in {".dpapi", ".log"}:
            raise ValueError("Package contains a private runtime file")


__all__ = ["inspect_package_tree"]
