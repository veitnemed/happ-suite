"""Tray toggle paths call the right component and report their result."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.tray import TrayApp


class TrayToggleTests(unittest.TestCase):
    def _make_app(self, through_happ: bool):
        """Build a minimal TrayApp stub for toggle tests."""
        app = TrayApp.__new__(TrayApp)
        app._action_lock = threading.Lock()
        app._icon = Mock()
        app._update_icon = Mock()
        app.health_monitor = Mock()

        controller = Mock()
        controller.read_status.return_value = SimpleNamespace(
            route=SimpleNamespace(through_happ=through_happ)
        )

        ag_mock = Mock()
        ag_mock.state = Mock()  # don't care about exact state here
        ag_mock.requested_enabled = False

        app.orchestrator = Mock()
        app.orchestrator.happ = SimpleNamespace(
            controller=controller,
            state=Mock(),
        )
        app.orchestrator.ag_unlocker = ag_mock
        app.orchestrator.desired_enabled = through_happ
        app.orchestrator.toggle_happ = Mock(return_value=True)
        app.orchestrator.toggle_ag_unlocker = Mock(return_value=True)
        return app

    def test_f8_connected_calls_toggle_happ(self):
        """F8 with active HAPP route toggles HAPP and reports the outcome."""
        app = self._make_app(through_happ=True)
        with patch("src.tray.threading.Thread") as thread:
            app._schedule_toggle_happ()
            thread.call_args.kwargs["target"]()

        app.orchestrator.toggle_happ.assert_called_once()
        app._icon.notify.assert_called_once()

    def test_f8_disconnected_calls_toggle_happ(self):
        """F8 with no HAPP route toggles HAPP and reports the outcome."""
        app = self._make_app(through_happ=False)
        with patch("src.tray.threading.Thread") as thread:
            app._schedule_toggle_happ()
            thread.call_args.kwargs["target"]()

        app.orchestrator.toggle_happ.assert_called_once()
        app._icon.notify.assert_called_once()

    def test_f9_calls_toggle_ag_unlocker(self):
        """F9 toggles AG and reports the outcome without touching Happ."""
        app = self._make_app(through_happ=True)
        with patch("src.tray.threading.Thread") as thread:
            app._schedule_toggle_ag()
            thread.call_args.kwargs["target"]()

        app.orchestrator.toggle_ag_unlocker.assert_called_once()
        app.orchestrator.toggle_happ.assert_not_called()
        app._icon.notify.assert_called_once()

    def test_f8_and_f9_are_independent(self):
        """F8 toggle must not affect AG Unlocker, F9 must not affect Happ VPN."""
        app = self._make_app(through_happ=False)
        with patch("src.tray.threading.Thread") as thread:
            app._schedule_toggle_happ()
            thread.call_args.kwargs["target"]()
        app.orchestrator.toggle_ag_unlocker.assert_not_called()

        # Reset and test F9 path
        app._action_lock = threading.Lock()
        app.orchestrator.toggle_happ.reset_mock()
        with patch("src.tray.threading.Thread") as thread:
            app._schedule_toggle_ag()
            thread.call_args.kwargs["target"]()
        app.orchestrator.toggle_happ.assert_not_called()


if __name__ == "__main__":
    unittest.main()
