"""AG Unlocker relay detection and installer adapter.

The Google Antigravity desktop client has a separate adapter. This component
detects the documented AG Unlocker scheduled relay without starting/stopping it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from ..ag_unlocker import (
    TASK_NAME, _expected_relay_path, _task_is_safe_to_start,
)
from ..official_installers import AG_UNLOCKER
from .base import ComponentInstallation, ComponentStatus
from .official import OfficialInstallerComponent
from .windows_file_version import read_product_version


def detect_ag_unlocker_installation() -> ComponentInstallation:
    """Confirm install from its documented service binary and safe task."""
    executable = Path(_expected_relay_path())
    component_id = "ag_unlocker"
    display_name = "AG Unlocker / Relay"
    if not executable.is_file():
        return ComponentInstallation(
            component_id, display_name, False, None, True, False,
            ComponentStatus.NOT_INSTALLED,
        )
    if os.name != "nt":
        return ComponentInstallation(
            component_id, display_name, False, None, False, False,
            ComponentStatus.UNKNOWN,
        )
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return ComponentInstallation(
            component_id, display_name, False, None, False, False,
            ComponentStatus.UNKNOWN,
        )

    try:
        pythoncom.CoInitialize()
    except Exception:
        return ComponentInstallation(
            component_id, display_name, False, None, False, False,
            ComponentStatus.UNKNOWN,
        )
    service = task = None
    try:
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        task = service.GetFolder("\\").GetTask(TASK_NAME)
        if not _task_is_safe_to_start(task):
            return ComponentInstallation(
                component_id, display_name, False, None, False, False,
                ComponentStatus.BROKEN,
            )
        return ComponentInstallation(
            component_id, display_name, True, read_product_version(executable),
            False, False, ComponentStatus.INSTALLED,
        )
    except Exception:
        # File presence alone does not prove that the relay is installed.
        return ComponentInstallation(
            component_id, display_name, False, None, False, False,
            ComponentStatus.UNKNOWN,
        )
    finally:
        task = None
        service = None
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


class AgUnlockerComponent(OfficialInstallerComponent):
    def __init__(self, *, detection: Callable[[], ComponentInstallation] | None = None, **options):
        super().__init__(
            "ag_unlocker", "AG Unlocker / Relay", AG_UNLOCKER, **options,
        )
        self._detection = detection or detect_ag_unlocker_installation

    def detect(self) -> ComponentInstallation:
        result = self._detection()
        if result.id != self.id or result.display_name != self.display_name:
            raise ValueError("AG Unlocker detector returned a mismatched component")
        return result
