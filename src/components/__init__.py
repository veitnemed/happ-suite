"""Verified installation adapters used by Relay Studio's component settings."""

from .base import ComponentInstallation, ComponentOperationError, ComponentStatus
from .ag_unlocker import AgUnlockerComponent
from .antigravity import GoogleAntigravityComponent
from .mihomo import MihomoComponent
from .vscode import VSCodeComponent
from .view_model import ComponentPresentation, component_presentation

__all__ = [
    "ComponentInstallation",
    "ComponentOperationError",
    "ComponentStatus",
    "AgUnlockerComponent",
    "GoogleAntigravityComponent",
    "MihomoComponent",
    "VSCodeComponent",
    "ComponentPresentation",
    "component_presentation",
]
