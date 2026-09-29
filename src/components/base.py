"""Small common contract for installed desktop components."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ComponentStatus(str, Enum):
    INSTALLED = "installed"
    NOT_INSTALLED = "not_installed"
    BROKEN = "broken"
    VERIFICATION_FAILED = "verification_failed"
    INSTALLING = "installing"
    UPDATE_AVAILABLE = "update_available"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ComponentInstallation:
    id: str
    display_name: str
    installed: bool
    version: str | None
    can_install: bool
    can_repair: bool
    status: ComponentStatus


class ComponentOperationError(RuntimeError):
    """A component install or verification could not complete safely."""


class ComponentAdapter(Protocol):
    id: str
    display_name: str

    def detect(self) -> ComponentInstallation: ...

    def install(self) -> ComponentInstallation: ...

    def verify(self) -> bool: ...

    def version(self) -> str | None: ...
