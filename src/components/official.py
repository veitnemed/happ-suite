"""Adapters for upstream GUI installers with pinned download verification."""

from __future__ import annotations

from typing import Callable

from .base import ComponentInstallation, ComponentOperationError, ComponentStatus
from ..official_installers import Installer, download, run_installer


class OfficialInstallerComponent:
    def __init__(self, component_id: str, display_name: str, installer: Installer,
                 *, detector: Callable[[], str | None] | None = None,
                 downloader: Callable = download, launcher: Callable = run_installer):
        self.id = component_id
        self.display_name = display_name
        self.installer = installer
        self._detector = detector
        self._downloader = downloader
        self._launcher = launcher

    def detect(self) -> ComponentInstallation:
        version = self._detector() if self._detector is not None else None
        if version:
            status = ComponentStatus.INSTALLED
        else:
            status = ComponentStatus.UNKNOWN if self._detector is None else ComponentStatus.NOT_INSTALLED
        return ComponentInstallation(
            id=self.id, display_name=self.display_name,
            installed=status is ComponentStatus.INSTALLED, version=version,
            can_install=status is not ComponentStatus.INSTALLED,
            can_repair=False, status=status,
        )

    def version(self) -> str | None:
        return self.detect().version

    def verify(self) -> bool:
        return self.detect().installed

    def install(self) -> ComponentInstallation:
        current = self.detect()
        if current.installed:
            return current
        try:
            path = self._downloader(self.installer)
            self._launcher(path)
        except Exception as exc:
            raise ComponentOperationError(f"Не удалось запустить установщик {self.display_name}") from exc
        return ComponentInstallation(
            id=self.id, display_name=self.display_name, installed=False,
            version=None, can_install=False, can_repair=False,
            status=ComponentStatus.INSTALLING,
        )
