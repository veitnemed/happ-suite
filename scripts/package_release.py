"""Create a verified Relay Studio ZIP in the repository's release directory."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SOURCE = ROOT / "dist" / "RelayStudio"
OUTPUT = ROOT / "release"


def app_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise ValueError("Cannot determine Relay Studio version")
    return match.group(1)


def build_release(source: Path = SOURCE, output: Path = OUTPUT) -> Path:
    """Copy a clean onedir build and create its ZIP and SHA-256 sidecar."""
    from src.package_inspection import inspect_package_tree

    source = Path(source).resolve()
    output = Path(output).resolve()
    inspect_package_tree(source)
    output.mkdir(parents=True, exist_ok=True)

    version = app_version()
    package_name = f"RelayStudio-{version}-win-x64"
    package_dir = output / "RelayStudio"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    shutil.copytree(source, package_dir)

    archive_path = output / f"{package_name}.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output).as_posix())
        archive.write(ROOT / "README.md", "README.md")
        archive.write(ROOT / "LICENSE", "LICENSE")

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        required = {
            "RelayStudio/RelayStudio.exe",
            "RelayStudio/config/default.json",
            "README.md",
            "LICENSE",
        }
        if not required <= names:
            raise RuntimeError("Release ZIP is missing required application files")
        forbidden = {"local.json", "hotkeys.json", "gate.json", "state.json", "current.json"}
        if any(Path(name).name.casefold() in forbidden for name in names):
            raise RuntimeError("Release ZIP contains user runtime settings")

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_suffix(archive_path.suffix + ".sha256").write_text(
        f"{digest}  {archive_path.name}\n", encoding="ascii"
    )
    print(f"Created {archive_path.name} (SHA-256 {digest})")
    return archive_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    build_release(args.source, args.output)
