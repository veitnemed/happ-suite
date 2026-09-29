"""Pinned Mihomo release installer with archive and executable verification."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import zipfile

from .mihomo_config import RuntimePaths, _atomic_write
from .official_installers import Installer, download
from .vpn_backend import BinaryIntegrityError


MIHOMO_VERSION = "1.19.31"
MIHOMO_ARCHIVE_SHA256 = "93d14e9a13b49b2f2d256202d02cc8d14a7c4695edf084cae0f941986bc9c218"
MIHOMO = Installer(
    f"Mihomo {MIHOMO_VERSION} (Windows x64 compatible)",
    f"mihomo-windows-amd64-compatible-v{MIHOMO_VERSION}.zip",
    f"https://github.com/MetaCubeX/mihomo/releases/download/v{MIHOMO_VERSION}/mihomo-windows-amd64-compatible-v{MIHOMO_VERSION}.zip",
    MIHOMO_ARCHIVE_SHA256,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_mihomo(paths: RuntimePaths | None = None, *, installer: Installer = MIHOMO) -> tuple[Path, str]:
    paths = paths or RuntimePaths()
    paths.ensure()
    archive_path = download(installer)
    if sha256_file(archive_path) != installer.sha256:
        raise BinaryIntegrityError("Mihomo release archive failed SHA-256 verification")

    executable_member = None
    license_member = None
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = PurePosixPath(info.filename)
                executable_name = name.name.casefold()
                if executable_name.startswith("mihomo") and executable_name.endswith(".exe"):
                    if executable_member is not None:
                        raise BinaryIntegrityError("Mihomo archive contains multiple executables")
                    executable_member = info
                elif name.name.casefold() in {"license", "license.txt"}:
                    license_member = info
            if executable_member is None:
                raise BinaryIntegrityError("Mihomo archive has no supported Windows executable")

            temporary = paths.binary.with_suffix(".exe.part")
            try:
                with archive.open(executable_member) as source, temporary.open("wb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, paths.binary)
            finally:
                if temporary.exists():
                    temporary.unlink()
            executable_sha = sha256_file(paths.binary)
            if license_member is not None:
                _atomic_write(paths.binary_root / "LICENSE", archive.read(license_member))
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, BinaryIntegrityError):
            raise
        raise BinaryIntegrityError("Mihomo archive could not be safely extracted") from exc

    manifest = {
        "version": MIHOMO_VERSION,
        "asset": installer.filename,
        "archive_sha256": installer.sha256,
        "executable_sha256": executable_sha,
        "platform": "windows-amd64-compatible",
    }
    _atomic_write(
        paths.binary_root / "integrity.json",
        (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8"),
    )
    return paths.binary, executable_sha


def installed_integrity(paths: RuntimePaths | None = None) -> tuple[str, str] | None:
    paths = paths or RuntimePaths()
    try:
        manifest = json.loads((paths.binary_root / "integrity.json").read_text(encoding="utf-8"))
        expected = manifest["executable_sha256"]
        version = manifest["version"]
        if version != MIHOMO_VERSION or sha256_file(paths.binary) != expected:
            return None
        return version, expected
    except (OSError, KeyError, TypeError, ValueError):
        return None
