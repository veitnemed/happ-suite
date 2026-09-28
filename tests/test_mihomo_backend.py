import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.core import ComponentOwnership, ComponentState, DesiredState
from src.mihomo_backend import MihomoVPNComponent
from src.mihomo_config import RuntimePaths, render_config, validate_subscription_url
from src.mihomo_subscription import SubscriptionResult
from src.vpn_backend import (
    BackendObservation, OwnershipConflictError, ProviderFormatError, SubscriptionError,
)


class _Config:
    mihomo_wait_timeout = 0.1
    vpn_backend = "mihomo"

    def get(self, key, default=None):
        return default


class MihomoOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = RuntimePaths(Path(self.temp.name))
        self.paths.ensure()
        self.component = MihomoVPNComponent(_Config(), paths=self.paths)

    def tearDown(self):
        self.temp.cleanup()

    def test_external_mihomo_is_detected_but_never_stopped(self):
        self.component.ownership = ComponentOwnership.EXTERNAL
        with patch.object(self.component, "_external_mihomo_processes", return_value=[81]), \
             patch.object(self.component, "_stop_locked") as stop:
            self.assertTrue(self.component.stop())
            stop.assert_not_called()
        self.assertEqual(self.component.desired_state, DesiredState.OFF)

    def test_discovery_of_running_external_mihomo_does_not_change_intent(self):
        with patch.object(self.component, "_external_mihomo_processes", return_value=[81]):
            result = self.component.detect()
        self.assertEqual((result.state, result.ownership, result.pid), ("running", "external", 81))
        self.assertEqual(self.component.desired_state, DesiredState.OFF)
        self.assertEqual(self.component.ownership, ComponentOwnership.EXTERNAL)

    def test_unknown_ownership_never_stops_process(self):
        self.component.ownership = ComponentOwnership.UNKNOWN
        with patch.object(self.component, "_stop_locked") as stop:
            self.assertTrue(self.component.stop())
            stop.assert_not_called()

    def test_suite_owned_stop_requires_saved_identity_and_clears_record(self):
        record = {
            "pid": 42, "create_time": 10.0, "executable": str(self.paths.binary),
            "executable_sha256": "exe", "config_sha256": "cfg",
        }
        self.paths.instance.write_text(json.dumps(record), encoding="utf-8")
        process = Mock()
        process.wait.return_value = 0
        with patch.object(self.component, "_identity_matches", return_value=True), \
             patch.object(self.component, "_send_ctrl_break", return_value=True), \
             patch.object(self.component, "_wait_for_route_cleanup", return_value=True), \
             patch("src.mihomo_backend.psutil.Process", return_value=process):
            self.component._instance = record
            self.component.ownership = ComponentOwnership.SUITE
            self.assertTrue(self.component.stop())
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
        self.assertFalse(self.paths.instance.exists())
        self.assertEqual(self.component.state, ComponentState.STOPPED)

    def test_ctrl_break_is_scoped_to_the_dedicated_console(self):
        kernel = Mock()
        kernel.AttachConsole.return_value = 1
        with patch("src.mihomo_backend.os.name", "nt"), \
             patch("src.mihomo_backend.ctypes.WinDLL", return_value=kernel):
            self.assertTrue(self.component._send_ctrl_break(42))
        kernel.AttachConsole.assert_called_once_with(42)
        kernel.GenerateConsoleCtrlEvent.assert_called_once_with(1, 0)
        kernel.FreeConsole.assert_called_once_with()

    def test_exited_suite_process_keeps_record_if_route_cleanup_fails(self):
        record = {"pid": 42, "create_time": 10.0}
        self.paths.instance.write_text(json.dumps(record), encoding="utf-8")
        with patch.object(self.component, "_identity_matches", return_value=True), \
             patch.object(self.component, "_find_process", return_value=None), \
             patch.object(self.component, "_wait_for_route_cleanup", return_value=False):
            self.component._instance = record
            self.component.ownership = ComponentOwnership.SUITE
            self.assertFalse(self.component.stop())
        self.assertTrue(self.paths.instance.exists())
        self.assertEqual(self.component.ownership, ComponentOwnership.UNKNOWN)

    def test_start_reuses_a_verified_suite_owned_process(self):
        record = {"pid": 42, "create_time": 10.0, "controller_port": 19091}

        def detect_owned():
            self.component._instance = record
            self.component.ownership = ComponentOwnership.SUITE
            return BackendObservation("running", "suite", 42)

        with patch.object(self.component, "_acquire_launch_lock", return_value=True), \
             patch.object(self.component, "detect", side_effect=detect_owned), \
             patch.object(self.component, "_api_for_record", return_value=Mock()), \
             patch.object(self.component, "verify", return_value=True):
            self.assertTrue(self.component.start())
        self.assertEqual(self.component.state, ComponentState.RUNNING)

    def test_mismatched_identity_is_not_terminated(self):
        record = {"pid": 42, "create_time": 1}
        process = Mock()
        with patch.object(self.component, "_identity_matches", return_value=False), \
             patch("src.mihomo_backend.psutil.Process", return_value=process):
            self.component._instance = record
            self.component.ownership = ComponentOwnership.SUITE
            self.assertFalse(self.component.stop())
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
        self.assertEqual(self.component.ownership, ComponentOwnership.UNKNOWN)

    def test_start_refuses_external_process_without_adopting_it(self):
        self.component.desired_state = DesiredState.OFF
        with patch.object(self.component, "_external_mihomo_processes", return_value=[7]):
            self.assertFalse(self.component.start())
        self.assertEqual(self.component.desired_state, DesiredState.ON)
        self.assertEqual(self.component.ownership, ComponentOwnership.EXTERNAL)

    def test_start_refuses_preexisting_happ_tun(self):
        with patch.object(self.component, "_external_mihomo_processes", return_value=[]), \
             patch("src.mihomo_backend.best_route_to", return_value=Mock(interface_alias="happ-xray")), \
             patch.object(self.component, "_popen") as launch:
            self.assertFalse(self.component.start())
        launch.assert_not_called()
        self.assertEqual(self.component.ownership, ComponentOwnership.EXTERNAL)

    def test_mihomo_observe_inherits_component_observe_signature(self):
        self.component.observe(ComponentState.RUNNING)
        self.assertEqual(self.component.observed_state, ComponentState.RUNNING)
        self.component.request_enabled(False)
        self.assertEqual(self.component.desired_state, DesiredState.OFF)

    def test_backend_observation_is_separate_from_component_state_observation(self):
        self.component.desired_state = DesiredState.OFF
        with patch.object(self.component, "detect", return_value=BackendObservation("stopped", "unknown")):
            self.assertEqual(self.component.backend_observation().state, "stopped")
        self.assertEqual(self.component.desired_state, DesiredState.OFF)

    def test_save_fetches_and_caches_subscription_before_reporting_success(self):
        node = {"name": "node", "type": "socks5", "server": "127.0.0.1", "port": 1080}
        result = SubscriptionResult(
            profile={"proxies": [node]}, nodes=(node,), hostname="provider.example",
            redacted_id="0123456789ab", content_type="application/yaml",
            used_mihomo_suffix=False, hwid_active=True,
        )
        with patch.object(self.component.subscription_client, "fetch", return_value=result), \
             patch("src.mihomo_backend.SecretStore") as store:
            saved = self.component.set_subscription_url("https://provider.example/sub")
        self.assertEqual(saved.nodes[0]["name"], "node")
        self.assertTrue(self.paths.subscription_config.is_file())
        store.return_value.save.assert_called_once()


class MihomoConfigTests(unittest.TestCase):
    def test_rejects_non_https_subscription(self):
        with self.assertRaises(SubscriptionError):
            validate_subscription_url("http://provider.example/sub")

    def test_config_uses_loopback_secret_and_no_direct_fallback(self):
        profile = {"proxies": [{"name": "node", "type": "socks5", "server": "127.0.0.1", "port": 1080}]}
        text = render_config(profile, "random-secret")
        self.assertIn("external-controller: 127.0.0.1:19090", text)
        self.assertIn("secret: random-secret", text)
        self.assertIn("- MATCH,VPN", text)
        self.assertNotIn("DIRECT", text)
        self.assertIn("device: Mihomo", text)


if __name__ == "__main__":
    unittest.main()
