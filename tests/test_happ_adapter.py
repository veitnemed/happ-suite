"""Mock-only checks for the F8 HAPP adapter; never change the live VPN."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.core import ComponentState, HappVPNComponent
from src.happ_controller import HappStatus
from src.happ_ipc import _init_message, _server_name
from src.happ_status import TunnelRoute


class HappAdapterTests(unittest.TestCase):
    def setUp(self):
        config = SimpleNamespace(
            happ_exe=r"C:\Program Files\FlyFrogLLC\Happ\Happ.exe",
            tunnel_wait_timeout=1,
            proxy_port=10809,
        )
        self.component = HappVPNComponent(config)
        self.component.controller = Mock()

    def _status(self, connected, gui_pid=123):
        route = TunnelRoute(42, "happ-xray", connected)
        return HappStatus(route, connected, gui_pid)

    def test_existing_connection_is_adopted_without_another_command(self):
        self.component.controller.read_status.return_value = self._status(True)
        self.assertTrue(self.component.start())
        self.assertEqual(self.component.state, ComponentState.RUNNING)
        self.component.controller.connect_current_profile.assert_not_called()

    def test_connect_uses_existing_gui_controller(self):
        self.component.controller.read_status.return_value = self._status(False)
        self.component.controller.connect_current_profile.return_value = True
        self.assertTrue(self.component.start())
        self.component.controller.connect_current_profile.assert_called_once()
        self.assertEqual(self.component.state, ComponentState.RUNNING)

    def test_absent_gui_does_not_spawn_second_window(self):
        self.component.controller.read_status.return_value = self._status(False, None)
        with patch("src.core.subprocess.Popen") as launch:
            self.assertFalse(self.component.start())
        launch.assert_not_called()
        self.assertEqual(self.component.state, ComponentState.ERROR)

    def test_disconnection_of_preexisting_tunnel_uses_gui_command(self):
        self.component.controller.disconnect.return_value = True
        self.assertTrue(self.component.stop_owned())
        self.component.controller.disconnect.assert_called_once()
        self.assertEqual(self.component.state, ComponentState.STOPPED)

    def test_unverified_server_id_is_rejected(self):
        self.assertFalse(self.component.start({"id": 5}))
        self.component.controller.connect_current_profile.assert_not_called()
        self.assertEqual(self.component.state, ComponentState.ERROR)

    def test_failed_disconnect_is_red(self):
        self.component.controller.disconnect.return_value = False
        self.assertFalse(self.component.disconnect())
        self.assertEqual(self.component.state, ComponentState.ERROR)

    def test_qt_handshake_matches_observed_happ(self):
        self.assertEqual(_server_name(), "S9n8oC_xoOlWdalE1T86BpVGpsouYi6lNuCOkq0mJjM=")
        self.assertEqual(_init_message()[-2:], bytes.fromhex("e49e"))


if __name__ == "__main__":
    unittest.main()
