"""Download unmodified upstream installers only when the user asks for setup."""

from dataclasses import dataclass
import ctypes
import hashlib
import os
from pathlib import Path
import urllib.request


@dataclass(frozen=True)
class Installer:
    name: str
    filename: str
    url: str
    sha256: str


# Pinned official release assets. The hashes come from each GitHub release's
# asset digest. Updating versions requires updating both URL and digest.
AG_UNLOCKER = Installer(
    "AG Unlocker 2.17.0.3",
    "AG_2.17.0.3.exe",
    "https://github.com/confeden/Antigravity/releases/download/v2.17.0.3/AG_2.17.0.3.exe",
    "889040dd03d09f60645964578fb2752c609c7ae8299585e4dea9b487bb2f98cf",
)


def _download_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "HappSuite" / "installers"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(installer: Installer, progress=None, destination: Path | None = None) -> Path:
    target_dir = Path(destination) if destination is not None else _download_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / installer.filename
    if target.is_file() and _sha256(target) == installer.sha256:
        return target
    temporary = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(installer.url, headers={"User-Agent": "RelayStudio/2.3.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response, temporary.open("wb") as output:
            total = int(response.headers.get("Content-Length", "0"))
            received = 0
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                received += len(chunk)
                if progress:
                    progress(received, total)
        if _sha256(temporary) != installer.sha256:
            raise ValueError(f"{installer.name}: download hash mismatch")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def run_installer(path: Path) -> None:
    """Open the official GUI installer and let Windows show its own UAC prompt."""
    if os.name != "nt":
        raise OSError("Windows installer required")
    result = ctypes.windll.shell32.ShellExecuteW(None, "open", str(path), None, str(path.parent), 1)
    if result <= 32:
        raise OSError(f"Windows could not start installer (code {result})")
