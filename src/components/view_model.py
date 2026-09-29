"""Pure component-state presentation mapping shared by the desktop UI/tests."""

from __future__ import annotations

from dataclasses import dataclass

from .base import ComponentInstallation, ComponentStatus


@dataclass(frozen=True)
class ComponentPresentation:
    status_text: str
    action_text: str
    action_enabled: bool
    tone: str


def component_presentation(
    installation: ComponentInstallation,
    *,
    install_label: str = "Установить",
    open_label: str = "Открыть",
    unknown_action_enabled: bool | None = None,
) -> ComponentPresentation:
    status_labels = {
        ComponentStatus.INSTALLED: "Установлен",
        ComponentStatus.NOT_INSTALLED: "Не установлен",
        ComponentStatus.BROKEN: "Повреждён",
        ComponentStatus.VERIFICATION_FAILED: "Проверка не пройдена",
        ComponentStatus.INSTALLING: "Установка",
        ComponentStatus.UPDATE_AVAILABLE: "Доступно обновление",
        ComponentStatus.UNKNOWN: "Состояние не подтверждено",
    }
    tones = {
        ComponentStatus.INSTALLED: "success",
        ComponentStatus.NOT_INSTALLED: "muted",
        ComponentStatus.BROKEN: "error",
        ComponentStatus.VERIFICATION_FAILED: "error",
        ComponentStatus.INSTALLING: "busy",
        ComponentStatus.UPDATE_AVAILABLE: "warning",
        ComponentStatus.UNKNOWN: "warning",
    }
    label = status_labels.get(installation.status, status_labels[ComponentStatus.UNKNOWN])
    if installation.version:
        label += f" · {installation.version}"
    if installation.status is ComponentStatus.INSTALLING:
        return ComponentPresentation(label, "Установка…", False, tones[installation.status])
    if installation.status is ComponentStatus.INSTALLED:
        return ComponentPresentation(label, open_label, True, tones[installation.status])
    if installation.status is ComponentStatus.BROKEN and installation.can_repair:
        return ComponentPresentation(label, "Восстановить", True, tones[installation.status])
    enabled = installation.can_install
    if installation.status is ComponentStatus.UNKNOWN and unknown_action_enabled is not None:
        enabled = unknown_action_enabled
    return ComponentPresentation(label, install_label, enabled, tones.get(installation.status, "warning"))
