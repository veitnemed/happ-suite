import io
import subprocess
import unittest
from unittest.mock import Mock, patch

from src.components.base import ComponentInstallation, ComponentOperationError, ComponentStatus
from src.components.mihomo import bootstrap_mihomo
from src.mihomo_installer import MIHOMO_VERSION


def installation(status: ComponentStatus) -> ComponentInstallation:
    installed = status is ComponentStatus.INSTALLED
    return ComponentInstallation(
        id="mihomo", display_name="Mihomo", installed=installed,
        version=MIHOMO_VERSION if installed else None, can_install=not installed,
        can_repair=status in {ComponentStatus.BROKEN, ComponentStatus.VERIFICATION_FAILED},
        status=status,
    )


class MihomoBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.component = Mock()

    def run_bootstrap(self):
        return bootstrap_mihomo(self.component, stdout=self.stdout, stderr=self.stderr)

    def test_existing_verified_mihomo_succeeds_without_download_or_reinstall(self):
        self.component.detect.return_value = installation(ComponentStatus.INSTALLED)
        self.assertEqual(self.run_bootstrap(), 0)
        self.component.install.assert_not_called()
        self.assertIn(MIHOMO_VERSION, self.stdout.getvalue())
        self.assertEqual(self.stderr.getvalue(), "")

    def test_missing_mihomo_installs_and_verifies_without_vpn_runtime(self):
        self.component.detect.return_value = installation(ComponentStatus.NOT_INSTALLED)
        self.component.install.return_value = installation(ComponentStatus.INSTALLED)
        self.component.verify.return_value = True
        self.assertEqual(self.run_bootstrap(), 0)
        self.component.install.assert_called_once_with()
        self.component.verify.assert_called_once_with()

    def test_integrity_mismatch_returns_nonzero(self):
        self.component.detect.return_value = installation(ComponentStatus.NOT_INSTALLED)
        self.component.install.return_value = installation(ComponentStatus.INSTALLED)
        self.component.verify.return_value = False
        self.assertEqual(self.run_bootstrap(), 1)
        self.assertIn("проверку", self.stderr.getvalue())

    def test_download_failure_returns_nonzero_without_leaking_exception_details(self):
        self.component.detect.return_value = installation(ComponentStatus.NOT_INSTALLED)
        self.component.install.side_effect = ComponentOperationError("offline")
        self.assertEqual(self.run_bootstrap(), 1)
        self.assertIn("offline", self.stderr.getvalue())

    def test_non_component_error_is_reported_by_type_only(self):
        self.component.detect.side_effect = subprocess.TimeoutExpired("download", 30)
        self.assertEqual(self.run_bootstrap(), 1)
        self.assertIn("TimeoutExpired", self.stderr.getvalue())
        self.assertNotIn("download", self.stderr.getvalue())

    def test_cli_bootstrap_exits_before_single_instance_or_dashboard(self):
        import src.start as launcher

        with patch.object(launcher, "enable_high_dpi_awareness"), \
             patch("src.components.mihomo.bootstrap_mihomo", return_value=0) as bootstrap, \
             patch.object(launcher, "_single_instance_handle") as mutex:
            with self.assertRaises(SystemExit) as raised:
                launcher.main(["--bootstrap-mihomo"])
        self.assertEqual(raised.exception.code, 0)
        bootstrap.assert_called_once_with()
        mutex.assert_not_called()

    def test_source_launcher_bootstrap_does_not_stop_existing_suite(self):
        import start as source_launcher

        with patch("sys.argv", ["start.py", "--bootstrap-mihomo"]), \
             patch("src.components.mihomo.bootstrap_mihomo", return_value=0) as bootstrap, \
             patch.object(source_launcher, "_stop_old_suite") as stop_old:
            with self.assertRaises(SystemExit) as raised:
                source_launcher.main()
        self.assertEqual(raised.exception.code, 0)
        bootstrap.assert_called_once_with()
        stop_old.assert_not_called()


if __name__ == "__main__":
    unittest.main()
