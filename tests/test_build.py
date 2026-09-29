from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.build import build


@pytest.mark.parametrize("entry", ["start", "tray"])
def test_build_uses_package_aware_dashboard_entry(tmp_path, entry):
    dist = tmp_path / "dist"
    work = tmp_path / "work"

    def fake_pyinstaller(command, cwd, check):
        assert command[-1] == str(Path(__file__).resolve().parents[1] / "start_app.py")
        assert "--name=RelayStudio" in command
        package = dist / "RelayStudio"
        package.mkdir(parents=True)
        (package / "RelayStudio.exe").write_bytes(b"mock executable")

    with patch("scripts.build.subprocess.run", side_effect=fake_pyinstaller):
        executable = build(dist_dir=dist, work_dir=work, entry=entry)

    assert executable == dist / "RelayStudio" / "RelayStudio.exe"
    assert (dist / "RelayStudio" / "config" / "default.json").is_file()


def test_build_rejects_unknown_entry(tmp_path):
    with pytest.raises(ValueError, match="entry must be"):
        build(dist_dir=tmp_path / "dist", work_dir=tmp_path / "work", entry="unknown")
