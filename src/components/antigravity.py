"""Google Antigravity desktop installer adapter, separate from AG Unlocker."""

from __future__ import annotations

import gzip
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse
import urllib.request

from .base import ComponentInstallation, ComponentOperationError, ComponentStatus
from .windows_file_version import read_product_version


OFFICIAL_DOWNLOAD_PAGE = "https://antigravity.google/download"
_DOWNLOAD_RE = re.compile(
    r"https://storage\.googleapis\.com/antigravity-public/[^\"'<>\s]+/windows-x64/Antigravity-x64\.exe"
)
_VERSION_RE = re.compile(r"/antigravity-hub/(\d+\.\d+\.\d+)-")


def _version_matches(installed: str, downloaded: str) -> bool:
    return installed == downloaded or installed.startswith(downloaded + ".")


def official_download_url(*, timeout: int = 20, opener=urllib.request.urlopen) -> tuple[str, str]:
    """Resolve the x64 installer only from the official Google download page."""
    request = urllib.request.Request(OFFICIAL_DOWNLOAD_PAGE, headers={"User-Agent": "RelayStudio/2.3.0"})
    with opener(request, timeout=timeout) as response:
        if response.geturl().split("/", 3)[:3] != ["https:", "", "antigravity.google"]:
            raise ComponentOperationError("Google Antigravity download page redirected outside the official domain")
        body = response.read(2_000_000)
        headers = getattr(response, "headers", {})
        content_encoding = headers.get("Content-Encoding", "") if hasattr(headers, "get") else ""
        if content_encoding.casefold() == "gzip":
            body = gzip.decompress(body)
        page = body.decode("utf-8", "replace")
    match = _DOWNLOAD_RE.search(page)
    if not match:
        raise ComponentOperationError("Google Antigravity x64 installer was not found on the official page")
    version_match = _VERSION_RE.search(match.group(0))
    if not version_match:
        raise ComponentOperationError("Google Antigravity installer version could not be verified")
    return match.group(0), version_match.group(1)


def download_antigravity(destination: Path | None = None, *, timeout: int = 90) -> tuple[Path, str]:
    """Download the current official x64 installer; return its source version."""
    url, version = official_download_url(timeout=min(timeout, 30))
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    target_dir = Path(destination) if destination is not None else local_app_data / "HappSuite" / "installers"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"GoogleAntigravity-{version}-x64.exe"
    temporary = target.with_suffix(".exe.part")
    request = urllib.request.Request(url, headers={"User-Agent": "RelayStudio/2.3.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as output:
            final_host = urlparse(response.geturl()).hostname
            if final_host not in {"storage.googleapis.com"}:
                raise ComponentOperationError("Google Antigravity installer redirected outside Google storage")
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target, version


def verify_google_authenticode(path: Path, *, run=subprocess.run) -> bool:
    """Accept only a valid Authenticode signature issued to Google LLC."""
    literal = str(Path(path).resolve()).replace("'", "''")
    command = (
        "$s = Get-AuthenticodeSignature -LiteralPath '" + literal + "'; "
        "if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'CN=Google LLC') { exit 1 }"
    )
    try:
        result = run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


class GoogleAntigravityComponent:
    id = "google_antigravity"
    display_name = "Google Antigravity"

    def __init__(self, *, environ=None, registry_paths=None, signature_verifier=verify_google_authenticode,
                 downloader=download_antigravity, opener=subprocess.Popen, version_reader=None):
        self.environ = os.environ if environ is None else environ
        self._registry_paths_override = registry_paths
        self._signature_verifier = signature_verifier
        self._downloader = downloader
        self._opener = opener
        self._version_reader = version_reader or read_product_version

    def executable_candidates(self) -> list[Path]:
        local = Path(self.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        program_files = Path(self.environ.get("ProgramFiles", r"C:\Program Files"))
        candidates = [
            local / "Programs" / "Antigravity" / "Antigravity.exe",
            local / "Programs" / "Google Antigravity" / "Antigravity.exe",
            local / "Google" / "Antigravity" / "Application" / "Antigravity.exe",
            program_files / "Google" / "Antigravity" / "Application" / "Antigravity.exe",
        ]
        candidates.extend(Path(path) for path in (self._registry_paths() or []))
        unique, seen = [], set()
        for path in candidates:
            key = os.path.normcase(str(path))
            if key not in seen:
                unique.append(path)
                seen.add(key)
        return unique

    def _registry_paths(self) -> list[Path]:
        if self._registry_paths_override is not None:
            return [Path(path) for path in self._registry_paths_override]
        try:
            import winreg
        except ImportError:
            return []
        found = []
        roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
        locations = (
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        )
        for root in roots:
            for location in locations:
                try:
                    with winreg.OpenKey(root, location) as key:
                        count = winreg.QueryInfoKey(key)[0]
                        children = [winreg.EnumKey(key, i) for i in range(count)]
                except OSError:
                    continue
                for child in children:
                    try:
                        with winreg.OpenKey(root, location + "\\" + child) as app:
                            name, _ = winreg.QueryValueEx(app, "DisplayName")
                            if "google antigravity" not in str(name).casefold():
                                continue
                            icon, _ = winreg.QueryValueEx(app, "DisplayIcon")
                            found.append(Path(str(icon).strip('"').split(",", 1)[0]))
                    except OSError:
                        continue
        return found

    def _find(self) -> tuple[Path, str] | None:
        for executable in self.executable_candidates():
            if not executable.is_file():
                continue
            try:
                product_version = self._version_reader(executable)
                if product_version:
                    return executable, product_version
            except (AttributeError, OSError, ValueError):
                continue
        return None

    def detect(self) -> ComponentInstallation:
        found = self._find()
        return ComponentInstallation(
            id=self.id, display_name=self.display_name,
            installed=found is not None, version=found[1] if found else None,
            can_install=found is None, can_repair=False,
            status=ComponentStatus.INSTALLED if found else ComponentStatus.NOT_INSTALLED,
        )

    def version(self) -> str | None:
        found = self._find()
        return found[1] if found else None

    def verify(self) -> bool:
        return self._find() is not None

    def open(self) -> bool:
        found = self._find()
        if found is None:
            return False
        self._opener([str(found[0])], cwd=str(found[0].parent), close_fds=True)
        return True

    def install(self) -> ComponentInstallation:
        current = self.detect()
        if current.installed:
            return current
        try:
            installer, source_version = self._downloader()
            installer = Path(installer)
            if not installer.is_file():
                raise FileNotFoundError("Google Antigravity installer was not downloaded")
            if not self._signature_verifier(installer):
                raise ComponentOperationError("Google Antigravity installer signature is invalid")
            process = self._opener([str(installer)], close_fds=True)
            process.wait(timeout=900)
        except ComponentOperationError:
            raise
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ComponentOperationError("Google Antigravity installation did not complete") from exc
        found = self._find()
        if found is None or not _version_matches(found[1], source_version):
            raise ComponentOperationError("Google Antigravity was not detected after installation")
        return self.detect()
