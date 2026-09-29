"""Mihomo installation adapter; the VPN lifecycle remains in mihomo_backend."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

from .base import ComponentInstallation, ComponentOperationError, ComponentStatus
from ..mihomo_config import RuntimePaths
from ..mihomo_installer import MIHOMO_VERSION, installed_integrity, install_mihomo


class MihomoComponent:
    id = "mihomo"
    display_name = "Mihomo"

    def __init__(
        self,
        paths: RuntimePaths | None = None,
        *,
        integrity: Callable = installed_integrity,
        installer: Callable = install_mihomo,
        run: Callable = subprocess.run,
    ):
        self.paths = paths or RuntimePaths()
        self._integrity = integrity
        self._installer = installer
        self._run = run

    def detect(self) -> ComponentInstallation:
        if not self.paths.binary.exists():
            status = ComponentStatus.NOT_INSTALLED
            version = None
        else:
            manifest = self._integrity(self.paths)
            if manifest is None:
                status = ComponentStatus.BROKEN
                version = None
            elif not self._verify_binary_version(manifest[0]):
                status = ComponentStatus.VERIFICATION_FAILED
                version = manifest[0]
            else:
                version = manifest[0]
                status = ComponentStatus.INSTALLED
        installed = status is ComponentStatus.INSTALLED
        return ComponentInstallation(
            id=self.id,
            display_name=self.display_name,
            installed=installed,
            version=version,
            can_install=not installed,
            can_repair=status in {
                ComponentStatus.BROKEN, ComponentStatus.VERIFICATION_FAILED,
            },
            status=status,
        )

    def version(self) -> str | None:
        return self.detect().version

    def _verify_binary_version(self, version: str) -> bool:
        if version != MIHOMO_VERSION:
            return False
        try:
            result = self._run(
                [str(self.paths.binary), "-v"], capture_output=True, text=True,
                timeout=8, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0 and version in output

    def verify(self) -> bool:
        return self.detect().installed

    def install(self) -> ComponentInstallation:
        current = self.detect()
        if current.installed:
            return current
        try:
            self._installer(self.paths)
        except Exception as exc:
            raise ComponentOperationError("Не удалось установить или восстановить Mihomo") from exc
        refreshed = self.detect()
        if not refreshed.installed:
            raise ComponentOperationError("Установленный Mihomo не прошёл проверку целостности")
        return refreshed


def bootstrap_mihomo(component: MihomoComponent | None = None, *, stdout=None, stderr=None) -> int:
    """Install or verify Mihomo without constructing the VPN runtime or starting TUN."""
    component = component or MihomoComponent()
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        before = component.detect()
        if before.installed:
            print(f"Mihomo {before.version} is installed and verified.", file=stdout)
            return 0
        installed = component.install()
        if not installed.installed or not component.verify():
            raise ComponentOperationError("Mihomo не прошёл проверку после установки")
        print(f"Mihomo {installed.version} installed and verified.", file=stdout)
        return 0
    except Exception as exc:
        detail = str(exc) if isinstance(exc, ComponentOperationError) else type(exc).__name__
        print(f"Mihomo bootstrap failed: {detail}", file=stderr)
        return 1
