"""The normal F8 path must not display a tray balloon."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.tray import TrayApp


class TrayToggleTests(unittest.TestCase):
    def test_connected_route_requests_disconnect_without_notification(self):
        app = TrayApp.__new__(TrayApp)
        app._action_lock = threading.Lock()
        app._icon = Mock()
        app._update_icon = Mock()
        app._action_stop_all = Mock(return_value=True)
        app._action_start_all = Mock(return_value=True)
        controller = Mock()
        controller.read_status.return_value = SimpleNamespace(
            route=SimpleNamespace(through_happ=True)
        )
        app.orchestrator = SimpleNamespace(happ=SimpleNamespace(controller=controller))

        with patch("src.tray.threading.Thread") as thread:
            app._schedule_toggle()
            thread.call_args.kwargs["target"]()

        app._action_stop_all.assert_called_once()
        app._action_start_all.assert_not_called()
        app._icon.notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
