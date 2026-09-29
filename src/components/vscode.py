"""Microsoft VS Code user installer and local install detector."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import urllib.request

from .base import ComponentInstallation, ComponentOperationError, ComponentStatus


VSCODE_DOWNLOAD_URL = "https://update.code.visualstudio.com/latest/win32-x64-user/stable"
VSCODE_INSTALLER_ARGS = (
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/MERGETASKS=!runcode",
)
_VERSION_PATTERN = re.compile(r"(?m)^\s*(\d+\.\d+\.\d+)\s*$")


def _version_from_output(output: str) -> str | None:
    match = _VERSION_PATTERN.search(output)
    return match.group(1) if match else None


def download_vscode(destination: Path | None = None, *, timeout: int = 60) -> Path:
    """Download only from Microsoft's documented stable x64 user endpoint."""
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    target_dir = Path(destination) if destination is not None else local_app_data / "HappSuite" / "installers"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "VSCodeUserSetup-x64-latest.exe"
    temporary = target.with_suffix(".exe.part")
    request = urllib.request.Request(VSCODE_DOWNLOAD_URL, headers={"User-Agent": "RelayStudio/2.3.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def verify_vscode_authenticode(path: Path, *, run=subprocess.run) -> bool:
    """Require a valid Windows Authenticode signature from Microsoft Corporation."""
    path_text = str(Path(path).resolve()).replace("'", "''")
    command = (
        "$signature = Get-AuthenticodeSignature -LiteralPath '" + path_text + "'; "
        "if ($signature.Status -ne 'Valid' -or "
        "$signature.SignerCertificate.Subject -notmatch 'CN=Microsoft Corporation') { exit 1 }"
    )
    try:
        result = run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=15, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


class VSCodeComponent:
    id = "vscode"
    display_name = "Visual Studio Code"

    def __init__(self, *, environ=None, registry_paths=None, run=subprocess.run,
                 downloader=download_vscode, signature_verifier=verify_vscode_authenticode,
                 installer_runner=subprocess.run, opener=subprocess.Popen):
        self.environ = os.environ if environ is None else environ
        self._registry_paths_override = registry_paths
        self._run = run
        self._downloader = downloader
        self._signature_verifier = signature_verifier
        self._installer_runner = installer_runner
        self._opener = opener

    def _registry_paths(self) -> list[Path]:
        if self._registry_paths_override is not None:
            return [Path(item) for item in self._registry_paths_override]
        try:
            import winreg
        except ImportError:
            return []

        paths = []
        keys = (
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\Code.exe"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths\Code.exe"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\Code.exe"),
        )
        for hive, subkey in keys:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, None)
                paths.append(Path(value.strip('"')))
            except OSError:
                continue
        return paths

    def executable_candidates(self) -> list[Path]:
        local = Path(self.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        candidates = [
            local / "Programs" / "Microsoft VS Code" / "Code.exe",
            Path(self.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft VS Code" / "Code.exe",
            Path(self.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft VS Code" / "Code.exe",
            *self._registry_paths(),
        ]
        unique = []
        seen = set()
        for path in candidates:
            key = os.path.normcase(str(path))
            if key not in seen:
                unique.append(path)
                seen.add(key)
        return unique

    def _find_installation(self) -> tuple[Path, str] | None:
        for executable in self.executable_candidates():
            if not executable.is_file():
                continue
            try:
                result = self._run(
                    [str(executable), "--version"], capture_output=True, text=True,
                    timeout=8, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            version = _version_from_output(result.stdout or "")
            if result.returncode == 0 and version:
                return executable, version
        return None

    def detect(self) -> ComponentInstallation:
        found = self._find_installation()
        if found:
            status = ComponentStatus.INSTALLED
            version = found[1]
        else:
            status = ComponentStatus.NOT_INSTALLED
            version = None
        return ComponentInstallation(
            id=self.id, display_name=self.display_name,
            installed=found is not None, version=version,
            can_install=found is None, can_repair=False, status=status,
        )

    def version(self) -> str | None:
        found = self._find_installation()
        return found[1] if found else None

    def verify(self) -> bool:
        return self._find_installation() is not None

    def open(self) -> bool:
        found = self._find_installation()
        if found is None:
            return False
        self._opener([str(found[0])], cwd=str(found[0].parent), close_fds=True)
        return True

    def install(self) -> ComponentInstallation:
        current = self.detect()
        if current.installed:
            return current
        try:
            installer = Path(self._downloader())
            if not installer.is_file():
                raise FileNotFoundError("VS Code installer download did not create a file")
            if not self._signature_verifier(installer):
                raise ComponentOperationError("Подпись установщика VS Code недействительна")
            result = self._installer_runner(
                [str(installer), *VSCODE_INSTALLER_ARGS], capture_output=True, text=True,
                timeout=900, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                raise ComponentOperationError("Установщик VS Code завершился с ошибкой")
        except ComponentOperationError:
            raise
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ComponentOperationError("Не удалось загрузить или запустить установщик VS Code") from exc
        found = self._find_installation()
        if found is None:
            raise ComponentOperationError("VS Code не найден после завершения установки")
        return self.detect()
