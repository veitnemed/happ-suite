import json
import tempfile
import unittest
from pathlib import Path

from src.package_inspection import inspect_package_tree


class PackageInspectionTests(unittest.TestCase):
    def _package(self, root: Path):
        (root / "config").mkdir(parents=True)
        (root / "RelayStudio.exe").write_bytes(b"exe")
        (root / "config" / "default.json").write_text(
            json.dumps({"vpn_backend": "mihomo"}), encoding="utf-8",
        )

    def test_accepts_clean_program_files_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._package(root)
            inspect_package_tree(root)

    def test_rejects_private_runtime_files_and_state_directories(self):
        unsafe = (
            Path("state.json"), Path("credentials.dpapi"), Path("subscription.yaml"),
            Path("logs/app.log"), Path("vpn/state/current.json"), Path("installation-id"),
        )
        for relative in unsafe:
            with self.subTest(path=relative.as_posix()), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self._package(root)
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"private")
                with self.assertRaises(ValueError):
                    inspect_package_tree(root)

    def test_rejects_private_config_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._package(root)
            (root / "config" / "default.json").write_text(
                '{"subscription_url":"private"}', encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "private setting"):
                inspect_package_tree(root)


if __name__ == "__main__":
    unittest.main()
