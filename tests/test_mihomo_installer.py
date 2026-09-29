import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from src.mihomo_config import RuntimePaths
from src.mihomo_installer import MIHOMO_VERSION, install_mihomo, sha256_file
from src.official_installers import Installer
from src.vpn_backend import BinaryIntegrityError


class MihomoInstallerTests(unittest.TestCase):
    def _archive(self, root: Path, members: dict[str, bytes]) -> Path:
        archive_path = root / "mihomo-release.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for name, content in members.items():
                archive.writestr(name, content)
        return archive_path

    def test_extracts_official_compatible_target_name_as_managed_mihomo_exe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = b"official mihomo executable fixture"
            archive_path = self._archive(root, {
                "mihomo-windows-amd64-compatible.exe": binary,
                "LICENSE": b"license text",
            })
            installer = Installer(
                "Mihomo test", archive_path.name, "https://example.invalid/mihomo.zip",
                sha256_file(archive_path),
            )
            paths = RuntimePaths(root / "runtime")

            with patch("src.mihomo_installer.download", return_value=archive_path):
                installed_path, executable_sha = install_mihomo(paths, installer=installer)

            self.assertEqual(installed_path.name, "mihomo.exe")
            self.assertEqual(installed_path.read_bytes(), binary)
            self.assertEqual(executable_sha, sha256_file(installed_path))
            self.assertEqual(installed_path.with_name("LICENSE").read_text(), "license text")
            self.assertEqual(installed_path.with_name("integrity.json").exists(), True)

    def test_refuses_archive_with_multiple_supported_executables(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = self._archive(root, {
                "mihomo.exe": b"first",
                "mihomo-windows-amd64-compatible.exe": b"second",
            })
            installer = Installer(
                "Mihomo test", archive_path.name, "https://example.invalid/mihomo.zip",
                sha256_file(archive_path),
            )
            paths = RuntimePaths(root / "runtime")

            with patch("src.mihomo_installer.download", return_value=archive_path):
                with self.assertRaises(BinaryIntegrityError):
                    install_mihomo(paths, installer=installer)
            self.assertFalse(paths.binary.exists())

    def test_rejects_downloaded_archive_with_bad_sha256(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = self._archive(root, {"mihomo.exe": b"verified fixture"})
            installer = Installer("Mihomo test", archive_path.name,
                                  "https://example.invalid/mihomo.zip", "0" * 64)
            paths = RuntimePaths(root / "runtime")
            with patch("src.mihomo_installer.download", return_value=archive_path):
                with self.assertRaisesRegex(BinaryIntegrityError, "SHA-256"):
                    install_mihomo(paths, installer=installer)
            self.assertFalse(paths.binary.exists())

    def test_rejects_corrupt_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "corrupt.zip"
            archive_path.write_bytes(b"not a zip archive")
            installer = Installer("Mihomo test", archive_path.name,
                                  "https://example.invalid/mihomo.zip", sha256_file(archive_path))
            paths = RuntimePaths(root / "runtime")
            with patch("src.mihomo_installer.download", return_value=archive_path):
                with self.assertRaisesRegex(BinaryIntegrityError, "safely extracted"):
                    install_mihomo(paths, installer=installer)

    def test_rejects_archive_without_an_executable_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = self._archive(root, {"LICENSE": b"license"})
            installer = Installer("Mihomo test", archive_path.name,
                                  "https://example.invalid/mihomo.zip", sha256_file(archive_path))
            paths = RuntimePaths(root / "runtime")
            with patch("src.mihomo_installer.download", return_value=archive_path):
                with self.assertRaisesRegex(BinaryIntegrityError, "no supported"):
                    install_mihomo(paths, installer=installer)

    def test_network_timeout_and_download_error_do_not_create_binary(self):
        for error in (TimeoutError("timed out"), OSError("HTTP download failed")):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as temp:
                paths = RuntimePaths(Path(temp))
                with patch("src.mihomo_installer.download", side_effect=error):
                    with self.assertRaises(type(error)):
                        install_mihomo(paths)
                self.assertFalse(paths.binary.exists())


if __name__ == "__main__":
    unittest.main()
