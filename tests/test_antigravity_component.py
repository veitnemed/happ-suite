import tempfile
import unittest
import gzip
from pathlib import Path
from unittest.mock import Mock, patch

from src.components.antigravity import (
    GoogleAntigravityComponent, OFFICIAL_DOWNLOAD_PAGE, official_download_url,
)
from src.components.base import ComponentInstallation, ComponentOperationError, ComponentStatus
from src.components.ag_unlocker import AgUnlockerComponent


class _Response:
    def __init__(self, body, url=OFFICIAL_DOWNLOAD_PAGE, headers=None):
        self.body = body
        self.url = url
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def geturl(self):
        return self.url

    def read(self, _limit=-1):
        return self.body


class GoogleAntigravityTests(unittest.TestCase):
    def test_official_page_resolves_versioned_google_storage_artifact(self):
        url = "https://storage.googleapis.com/antigravity-public/antigravity-hub/2.18.1-123/windows-x64/Antigravity-x64.exe"
        opener = Mock(return_value=_Response(f'<a href="{url}">Windows x64</a>'.encode()))
        self.assertEqual(official_download_url(opener=opener), (url, "2.18.1"))

    def test_official_page_gzip_response_is_supported(self):
        url = "https://storage.googleapis.com/antigravity-public/antigravity-hub/2.18.1-123/windows-x64/Antigravity-x64.exe"
        page = f'<a href="{url}">Windows x64</a>'.encode()
        response = _Response(gzip.compress(page), headers={"Content-Encoding": "gzip"})
        self.assertEqual(official_download_url(opener=Mock(return_value=response)), (url, "2.18.1"))

    def test_download_page_redirect_outside_google_is_rejected(self):
        with self.assertRaisesRegex(ComponentOperationError, "redirected"):
            official_download_url(opener=Mock(return_value=_Response(b"", "https://bad.example/download")))

    def test_missing_official_artifact_is_rejected(self):
        with self.assertRaisesRegex(ComponentOperationError, "not found"):
            official_download_url(opener=Mock(return_value=_Response(b"no windows link")))

    def test_absent_detection_does_not_claim_installed(self):
        component = GoogleAntigravityComponent(registry_paths=[], version_reader=lambda _p: None)
        result = component.detect()
        self.assertFalse(result.installed)
        self.assertEqual(result.status, ComponentStatus.NOT_INSTALLED)
        self.assertEqual(result.id, "google_antigravity")

    def test_detects_installed_google_product_from_file_version(self):
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "Antigravity.exe"
            exe.touch()
            component = GoogleAntigravityComponent(
                environ={"LOCALAPPDATA": directory, "ProgramFiles": directory},
                registry_paths=[exe], version_reader=lambda _p: "2.18.1.0",
            )
            result = component.detect()
        self.assertTrue(result.installed)
        self.assertEqual(result.version, "2.18.1.0")

    def test_invalid_authenticode_signature_never_launches_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / "google.exe"
            installer.touch()
            component = GoogleAntigravityComponent(
                registry_paths=[], downloader=Mock(return_value=(installer, "2.18.1")),
                signature_verifier=Mock(return_value=False), opener=Mock(),
                version_reader=lambda _p: None,
            )
            with self.assertRaisesRegex(ComponentOperationError, "signature"):
                component.install()
            component._opener.assert_not_called()

    def test_installer_launch_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / "google.exe"
            installer.touch()
            component = GoogleAntigravityComponent(
                registry_paths=[], downloader=Mock(return_value=(installer, "2.18.1")),
                signature_verifier=Mock(return_value=True), opener=Mock(side_effect=OSError("launch")),
                version_reader=lambda _p: None,
            )
            with self.assertRaisesRegex(ComponentOperationError, "did not complete"):
                component.install()

    def test_successful_install_requires_post_install_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / "google.exe"
            installed = Path(directory) / "Antigravity.exe"
            installer.touch()
            process = Mock()
            component = GoogleAntigravityComponent(
                registry_paths=[], downloader=Mock(return_value=(installer, "2.18.1")),
                signature_verifier=Mock(return_value=True), opener=Mock(return_value=process),
                version_reader=lambda _p: None,
            )
            with patch.object(component, "_find", side_effect=[
                None, (installed, "2.18.1"), (installed, "2.18.1"),
            ]):
                result = component.install()
            process.wait.assert_called_once_with(timeout=900)
            self.assertTrue(result.installed)
            self.assertEqual(result.version, "2.18.1")

    def test_install_rejects_version_mismatch_after_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / "google.exe"
            installed = Path(directory) / "Antigravity.exe"
            installer.touch()
            component = GoogleAntigravityComponent(
                registry_paths=[], downloader=Mock(return_value=(installer, "2.18.1")),
                signature_verifier=Mock(return_value=True), opener=Mock(return_value=Mock()),
                version_reader=lambda _p: None,
            )
            with patch.object(component, "_find", side_effect=[
                None, (installed, "2.17.9"),
            ]):
                with self.assertRaisesRegex(ComponentOperationError, "not detected"):
                    component.install()

    def test_google_antigravity_and_ag_unlocker_are_distinct_adapters(self):
        google = GoogleAntigravityComponent(registry_paths=[], version_reader=lambda _p: None)
        relay = AgUnlockerComponent(detection=lambda: ComponentInstallation(
            "ag_unlocker", "AG Unlocker / Relay", False, None,
            False, False, ComponentStatus.UNKNOWN,
        ))
        self.assertNotEqual(google.id, relay.id)
        self.assertNotEqual(google.display_name, relay.display_name)
        self.assertEqual(google.detect().status, ComponentStatus.NOT_INSTALLED)
        self.assertEqual(relay.detect().status, ComponentStatus.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
