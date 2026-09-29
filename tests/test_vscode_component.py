import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.components import ComponentOperationError, ComponentStatus, VSCodeComponent
from src.components.vscode import VSCODE_DOWNLOAD_URL, VSCODE_INSTALLER_ARGS


class VSCodeComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.local = self.root / "Local"
        self.program_files = self.root / "Program Files"
        self.env = {
            "LOCALAPPDATA": str(self.local),
            "ProgramFiles": str(self.program_files),
            "ProgramFiles(x86)": str(self.root / "Program Files (x86)"),
            "PATH": "",
        }
        self.run = Mock(return_value=SimpleNamespace(returncode=0, stdout="1.101.0\nabc\nx64", stderr=""))

    def tearDown(self):
        self.temp.cleanup()

    def component(self, **options):
        return VSCodeComponent(environ=self.env, registry_paths=[], run=self.run, **options)

    def test_not_installed(self):
        result = self.component().detect()
        self.assertEqual(result.status, ComponentStatus.NOT_INSTALLED)
        self.assertFalse(result.installed)
        self.assertTrue(result.can_install)

    def test_detects_user_installer_version_without_reading_path_environment(self):
        exe = self.local / "Programs" / "Microsoft VS Code" / "Code.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"fixture")
        result = self.component().detect()
        self.assertEqual(result.version, "1.101.0")
        self.run.assert_called_once()
        self.assertEqual(self.run.call_args.args[0][0], str(exe))
        self.assertEqual(self.env["PATH"], "")

    def test_detects_system_install(self):
        exe = self.program_files / "Microsoft VS Code" / "Code.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"fixture")
        result = self.component().detect()
        self.assertTrue(result.installed)
        self.assertEqual(result.version, "1.101.0")

    def test_download_failure_does_not_claim_installed(self):
        component = self.component(downloader=Mock(side_effect=OSError("offline")))
        with self.assertRaises(ComponentOperationError):
            component.install()
        self.assertEqual(component.detect().status, ComponentStatus.NOT_INSTALLED)

    def test_rejects_invalid_installer_signature(self):
        installer = self.root / "VSCodeUserSetup.exe"
        installer.write_bytes(b"fixture")
        component = self.component(downloader=Mock(return_value=installer),
                                   signature_verifier=Mock(return_value=False))
        with self.assertRaisesRegex(ComponentOperationError, "Подпись"):
            component.install()

    def test_installer_launch_failure(self):
        installer = self.root / "VSCodeUserSetup.exe"
        installer.write_bytes(b"fixture")
        runner = Mock(side_effect=subprocess.TimeoutExpired("installer", 900))
        component = self.component(downloader=Mock(return_value=installer),
                                   signature_verifier=Mock(return_value=True),
                                   installer_runner=runner)
        with self.assertRaises(ComponentOperationError):
            component.install()

    def test_install_is_verified_by_absolute_path_after_runner_returns(self):
        installer = self.root / "VSCodeUserSetup.exe"
        installer.write_bytes(b"fixture")
        exe = self.local / "Programs" / "Microsoft VS Code" / "Code.exe"

        def launch(args, **kwargs):
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"installed")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        component = self.component(
            downloader=Mock(return_value=installer),
            signature_verifier=Mock(return_value=True),
            installer_runner=Mock(side_effect=launch),
        )
        result = component.install()
        self.assertEqual(result.status, ComponentStatus.INSTALLED)
        self.assertEqual(result.version, "1.101.0")
        args = component._installer_runner.call_args.args[0]
        self.assertEqual(args[1:], list(VSCODE_INSTALLER_ARGS))

    def test_microsoft_download_is_official_latest_stable_user_endpoint(self):
        self.assertEqual(
            VSCODE_DOWNLOAD_URL,
            "https://update.code.visualstudio.com/latest/win32-x64-user/stable",
        )
        self.assertTrue(all(arg.startswith("/") for arg in VSCODE_INSTALLER_ARGS))

    def test_open_uses_absolute_discovered_path_not_path_lookup(self):
        exe = self.local / "Programs" / "Microsoft VS Code" / "Code.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"fixture")
        opener = Mock()
        component = self.component(opener=opener)
        self.assertTrue(component.open())
        opener.assert_called_once_with([str(exe)], cwd=str(exe.parent), close_fds=True)


if __name__ == "__main__":
    unittest.main()
