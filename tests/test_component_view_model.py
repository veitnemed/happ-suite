import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.components.base import ComponentInstallation, ComponentStatus
from src.components.view_model import component_presentation
from src.dashboard import Dashboard


def installation(status, *, installed=False, version=None, can_install=False, can_repair=False):
    return ComponentInstallation(
        id="test", display_name="Test Component", installed=installed,
        version=version, can_install=can_install, can_repair=can_repair, status=status,
    )


class ComponentPresentationTests(unittest.TestCase):
    def test_installed_component_shows_version_and_open_action(self):
        presentation = component_presentation(
            installation(ComponentStatus.INSTALLED, installed=True, version="1.2.3"),
            open_label="Открыть",
        )
        self.assertEqual(presentation.status_text, "Установлен · 1.2.3")
        self.assertEqual(presentation.action_text, "Открыть")
        self.assertTrue(presentation.action_enabled)

    def test_missing_component_shows_install_action(self):
        presentation = component_presentation(
            installation(ComponentStatus.NOT_INSTALLED, can_install=True),
            install_label="Установить Mihomo",
        )
        self.assertEqual(presentation.status_text, "Не установлен")
        self.assertEqual(presentation.action_text, "Установить Mihomo")
        self.assertTrue(presentation.action_enabled)

    def test_installing_state_disables_primary_action(self):
        presentation = component_presentation(installation(ComponentStatus.INSTALLING))
        self.assertEqual(presentation.status_text, "Установка")
        self.assertEqual(presentation.action_text, "Установка…")
        self.assertFalse(presentation.action_enabled)

    def test_broken_component_uses_repair_only_when_supported(self):
        repairable = component_presentation(
            installation(ComponentStatus.BROKEN, can_install=True, can_repair=True),
        )
        self.assertEqual(repairable.action_text, "Восстановить")
        self.assertTrue(repairable.action_enabled)

    def test_unconfirmed_component_state_does_not_offer_unavailable_install(self):
        presentation = component_presentation(installation(ComponentStatus.UNKNOWN))
        self.assertIn("не подтверждено", presentation.status_text)
        self.assertFalse(presentation.action_enabled)

    def test_unknown_ag_component_can_keep_its_official_installer_action(self):
        presentation = component_presentation(
            installation(ComponentStatus.UNKNOWN, can_install=True),
            install_label="Открыть официальный установщик",
        )
        self.assertEqual(presentation.action_text, "Открыть официальный установщик")
        self.assertTrue(presentation.action_enabled)

    def test_duplicate_component_install_is_suppressed_and_button_disabled(self):
        dashboard = SimpleNamespace(_component_installing=set(), _component_install_pending=set())
        button, label = Mock(), Mock()
        self.assertTrue(Dashboard._begin_component_install(dashboard, "vscode", button, label))
        self.assertFalse(Dashboard._begin_component_install(dashboard, "vscode", button, label))
        self.assertIn("vscode", dashboard._component_installing)
        self.assertTrue(any(call.kwargs.get("state") == "disabled" for call in button.configure.call_args_list))

    def test_pending_official_installer_remains_protected_from_duplicate_clicks(self):
        dashboard = SimpleNamespace(
            _component_installing=set(), _component_install_pending={"ag_unlocker"},
        )
        button, label = Mock(), Mock()
        self.assertFalse(Dashboard._begin_component_install(dashboard, "ag_unlocker", button, label))
        button.configure.assert_not_called()

    def test_finish_clears_install_guard_and_schedules_detection_refresh(self):
        refresh = Mock()
        root = Mock()
        dashboard = SimpleNamespace(_component_installing={"vscode"}, root=root)
        Dashboard._finish_component_install(dashboard, "vscode", refresh)
        self.assertNotIn("vscode", dashboard._component_installing)
        root.after.assert_called_once_with(0, refresh)


if __name__ == "__main__":
    unittest.main()
