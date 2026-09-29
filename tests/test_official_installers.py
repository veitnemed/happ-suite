import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.official_installers import AG_UNLOCKER, Installer, download


class _Response:
    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.headers = {"Content-Length": str(sum(map(len, chunks)))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _size=-1):
        return next(self._chunks, b"")


class OfficialInstallerTests(unittest.TestCase):
    def test_ag_unlocker_source_is_pinned_to_https_release_and_sha256(self):
        self.assertTrue(AG_UNLOCKER.url.startswith("https://github.com/"))
        self.assertIn("2.17.0.3", AG_UNLOCKER.filename)
        self.assertRegex(AG_UNLOCKER.sha256, r"^[0-9a-f]{64}$")

    def test_download_verifies_pinned_release_hash_before_rename(self):
        payload = b"test installer"
        installer = Installer(
            "AG test", "AG_test.exe", "https://github.com/example/release.exe",
            hashlib.sha256(payload).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.official_installers.urllib.request.urlopen",
            return_value=_Response([payload]),
        ):
            path = download(installer, destination=Path(directory))
            self.assertEqual(path.read_bytes(), payload)
            self.assertFalse(path.with_suffix(".exe.part").exists())

    def test_bad_download_hash_is_rejected_and_partial_file_removed(self):
        installer = Installer(
            "AG test", "AG_test.exe", "https://github.com/example/release.exe", "0" * 64,
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.official_installers.urllib.request.urlopen",
            return_value=_Response([b"tampered data"]),
        ):
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                download(installer, destination=Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_http_failure_is_not_replaced_by_an_unverified_file(self):
        installer = Installer(
            "AG test", "AG_test.exe", "https://github.com/example/release.exe", "0" * 64,
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.official_installers.urllib.request.urlopen", side_effect=OSError("offline"),
        ):
            with self.assertRaises(OSError):
                download(installer, destination=Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
