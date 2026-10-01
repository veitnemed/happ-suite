import json
from pathlib import Path

import pytest

from src.package_inspection import inspect_package_tree
from src.gemini_availability import load_region_snapshot, classify_region, RegionSupport


def package(tmp_path):
    (tmp_path / "RelayStudio.exe").write_bytes(b"mock")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "default.json").write_text("{}", encoding="utf-8")
    (tmp_path / "_internal" / "PySide6").mkdir(parents=True)
    return tmp_path / "_internal" / "src" / "data" / "gemini_web_regions.json"


def test_qt_package_rejects_misplaced_gemini_resource(tmp_path):
    package(tmp_path)
    with pytest.raises(FileNotFoundError, match="Gemini region snapshot"):
        inspect_package_tree(tmp_path)


def test_gemini_resource_is_readable_at_packaged_module_location(tmp_path):
    resource = package(tmp_path)
    resource.parent.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "src" / "data" / resource.name
    resource.write_bytes(source.read_bytes())
    inspect_package_tree(tmp_path)
    # Mirrors the __file__-relative reader's exact frozen location, without HTTP.
    snapshot = load_region_snapshot(resource)
    assert snapshot["source"] and snapshot["checked_at"]
    assert classify_region("US", snapshot) is RegionSupport.SUPPORTED
