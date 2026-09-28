"""AG task lifecycle checks use COM mocks and never stop the real relay."""

import unittest
from unittest.mock import Mock, patch

from src.ag_unlocker import stop_dns_relay


class AGRelayTests(unittest.TestCase):
    def test_stop_targets_only_validated_installed_task(self):
        task = Mock()
        task.State = 4
        task.Stop.side_effect = lambda _flags: setattr(task, "State", 3)
        service = Mock()
        service.GetFolder.return_value.GetTask.return_value = task

        with patch("win32com.client.Dispatch", return_value=service), \
             patch("src.ag_unlocker._task_is_safe_to_start", return_value=True), \
             patch("src.ag_unlocker._relay_listener_open", return_value=False):
            result = stop_dns_relay(timeout_s=0)

        self.assertTrue(result.ready)
        task.Stop.assert_called_once_with(0)
        service.GetFolder.assert_called_once_with("\\")

    def test_unexpected_task_is_never_stopped(self):
        task = Mock()
        task.State = 4
        service = Mock()
        service.GetFolder.return_value.GetTask.return_value = task

        with patch("win32com.client.Dispatch", return_value=service), \
             patch("src.ag_unlocker._task_is_safe_to_start", return_value=False):
            result = stop_dns_relay(timeout_s=0)

        self.assertFalse(result.ready)
        task.Stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
