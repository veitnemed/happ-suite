"""Mock-only rollback checks; no HAPP command or network change."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch


PATH = Path(__file__).resolve().parents[1] / "experiments" / "gemini-direct" / "route_trial.py"
SPEC = importlib.util.spec_from_file_location("gemini_route_trial", PATH)
trial = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trial)


class GeminiRouteRollbackTests(unittest.TestCase):
    def setUp(self):
        self.run = {"profile_name": "HappSuite Gemini Direct", "happ_exe": "Happ.exe", "happ_pid": 1}
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.run_dir = Path(self.directory.name)

    def test_restores_only_our_active_profile(self):
        controller = Mock()
        controller.read_status.return_value = SimpleNamespace(
            route=SimpleNamespace(through_happ=True), connected=True,
        )
        with patch.object(trial, "_read", return_value={"activeRoutingName": self.run["profile_name"]}), \
             patch.object(trial, "_send_routing") as send, \
             patch.object(trial, "_controller", return_value=controller), \
             patch.object(trial, "_http_status", return_value=204):
            self.assertTrue(trial._restore(self.run_dir, self.run, "watchdog"))
        send.assert_called_once_with(self.run, "off")

    def test_does_not_replace_another_active_profile(self):
        with patch.object(trial, "_read", return_value={"activeRoutingName": "User Profile"}), \
             patch.object(trial, "_send_routing") as send, \
             patch.object(trial.time, "sleep"):
            self.assertFalse(trial._restore(self.run_dir, self.run, "watchdog"))
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
