import hashlib
import json
import zipfile

import pytest

from scripts.package_release import build_release


def make_package(root):
    (root / "config").mkdir(parents=True)
    (root / "RelayStudio.exe").write_bytes(b"mock executable")
    (root / "config" / "default.json").write_text(json.dumps({"vpn_backend": "mihomo"}))


def test_release_zip_contains_app_docs_and_checksum(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    make_package(source)
    output = tmp_path / "release"

    archive_path = build_release(source, output)

    assert archive_path.is_file()
    assert (output / "RelayStudio" / "RelayStudio.exe").is_file()
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert "RelayStudio/RelayStudio.exe" in names
    assert "RelayStudio/config/default.json" in names
    assert {"README.md", "LICENSE"} <= names
    expected = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    assert archive_path.with_suffix(".zip.sha256").read_text().startswith(expected)


def test_release_rejects_user_runtime_state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    make_package(source)
    (source / "state.json").write_text("{}")

    with pytest.raises(ValueError, match="private runtime file"):
        build_release(source, tmp_path / "release")
