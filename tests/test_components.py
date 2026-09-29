import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.components import (
    AgUnlockerComponent, ComponentInstallation, ComponentOperationError, ComponentStatus, MihomoComponent,
)
from src.mihomo_config import RuntimePaths
from src.mihomo_installer import MIHOMO_VERSION


class MihomoComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = RuntimePaths(Path(self.temp.name))
        self.paths.ensure()
        self.installer = Mock()
        self.run = Mock(return_value=SimpleNamespace(
            stdout=f"Mihomo Meta {MIHOMO_VERSION}", stderr="", returncode=0,
        ))
        self.manifest = (MIHOMO_VERSION, "a" * 64)
        self.integrity = Mock(return_value=self.manifest)
        self.component = MihomoComponent(
            self.paths, integrity=self.integrity, installer=self.installer, run=self.run,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_binary_is_not_installed(self):
        result = self.component.detect()
        self.assertEqual(result.status, ComponentStatus.NOT_INSTALLED)
        self.assertFalse(result.installed)
        self.assertTrue(result.can_install)
        self.integrity.assert_not_called()

    def test_file_without_verified_manifest_is_broken_not_installed(self):
        self.paths.binary.write_bytes(b"an executable-looking file")
        self.integrity.return_value = None
        result = self.component.detect()
        self.assertEqual(result.status, ComponentStatus.BROKEN)
        self.assertFalse(result.installed)
        self.assertTrue(result.can_repair)
        self.run.assert_not_called()

    def test_integrity_valid_but_binary_version_check_fails(self):
        self.paths.binary.write_bytes(b"verified file")
        self.run.side_effect = subprocess.TimeoutExpired("mihomo.exe", 8)
        result = self.component.detect()
        self.assertEqual(result.status, ComponentStatus.VERIFICATION_FAILED)
        self.assertFalse(result.installed)
        self.assertTrue(result.can_repair)

    def test_installed_component_is_verified_and_install_is_idempotent(self):
        self.paths.binary.write_bytes(b"verified file")
        result = self.component.install()
        self.assertEqual(result.status, ComponentStatus.INSTALLED)
        self.assertEqual(result.version, MIHOMO_VERSION)
        self.installer.assert_not_called()

    def test_missing_component_installs_and_verifies(self):
        def install(paths):
            paths.binary.parent.mkdir(parents=True, exist_ok=True)
            paths.binary.write_bytes(b"verified file")

        self.installer.side_effect = install
        result = self.component.install()
        self.assertTrue(result.installed)
        self.assertEqual(result.version, MIHOMO_VERSION)
        self.installer.assert_called_once_with(self.paths)

    def test_install_failure_is_reported_without_claiming_installed(self):
        self.installer.side_effect = OSError("offline")
        with self.assertRaises(ComponentOperationError):
            self.component.install()
        self.assertEqual(self.component.detect().status, ComponentStatus.NOT_INSTALLED)

    def test_post_install_verification_failure_is_not_success(self):
        self.installer.side_effect = lambda paths: paths.binary.write_bytes(b"corrupt")
        self.integrity.side_effect = [self.manifest]
        self.run.return_value = SimpleNamespace(stdout="wrong version", stderr="", returncode=0)
        with self.assertRaises(ComponentOperationError):
            self.component.install()


class OfficialComponentTests(unittest.TestCase):
    def test_ag_unlocker_installed_detection_uses_confirmed_service_task(self):
        detected = ComponentInstallation(
            "ag_unlocker", "AG Unlocker / Relay", True, "2.17.0.3",
            False, False, ComponentStatus.INSTALLED,
        )
        component = AgUnlockerComponent(detection=lambda: detected)
        result = component.detect()
        self.assertEqual(result.status, ComponentStatus.INSTALLED)
        self.assertTrue(result.installed)
        self.assertEqual(result.version, "2.17.0.3")

    def test_ag_unlocker_absent_detection_never_uses_installer_cache_as_proof(self):
        detected = ComponentInstallation(
            "ag_unlocker", "AG Unlocker / Relay", False, None,
            True, False, ComponentStatus.NOT_INSTALLED,
        )
        component = AgUnlockerComponent(detection=lambda: detected)
        result = component.detect()
        self.assertEqual(result.status, ComponentStatus.NOT_INSTALLED)
        self.assertFalse(result.installed)

    def test_ag_unlocker_unknown_detection_stays_unknown_when_task_query_fails(self):
        detected = ComponentInstallation(
            "ag_unlocker", "AG Unlocker / Relay", False, None,
            False, False, ComponentStatus.UNKNOWN,
        )
        component = AgUnlockerComponent(detection=lambda: detected)
        self.assertEqual(component.detect().status, ComponentStatus.UNKNOWN)

    def test_ag_unlocker_bad_task_is_reported_as_broken(self):
        detected = ComponentInstallation(
            "ag_unlocker", "AG Unlocker / Relay", False, None,
            False, False, ComponentStatus.BROKEN,
        )
        component = AgUnlockerComponent(detection=lambda: detected)
        self.assertEqual(component.detect().status, ComponentStatus.BROKEN)

    def test_official_installer_launch_failure_is_reported(self):
        downloader = Mock(return_value=Path("verified-installer.exe"))
        launcher = Mock(side_effect=OSError("cannot launch"))
        component = AgUnlockerComponent(
            downloader=downloader, launcher=launcher,
            detection=lambda: ComponentInstallation(
                "ag_unlocker", "AG Unlocker / Relay", False, None,
                True, False, ComponentStatus.NOT_INSTALLED,
            ),
        )
        with self.assertRaises(ComponentOperationError):
            component.install()
        downloader.assert_called_once()
        launcher.assert_called_once_with(Path("verified-installer.exe"))

    def test_official_installer_download_failure_is_reported(self):
        downloader = Mock(side_effect=OSError("offline"))
        component = AgUnlockerComponent(
            downloader=downloader,
            detection=lambda: ComponentInstallation(
                "ag_unlocker", "AG Unlocker / Relay", False, None,
                True, False, ComponentStatus.NOT_INSTALLED,
            ),
        )
        with self.assertRaises(ComponentOperationError):
            component.install()


if __name__ == "__main__":
    unittest.main()
