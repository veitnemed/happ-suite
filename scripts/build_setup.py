"""Compile the clean Relay Studio onedir tree into a per-user Inno Setup EXE."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.package_inspection import inspect_package_tree


DEFAULT_SOURCE = ROOT / "dist" / "RelayStudio"
DEFAULT_OUTPUT = ROOT / "dist" / "release"
ISS_FILE = ROOT / "installer" / "RelayStudio.iss"


def app_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise ValueError("Cannot determine Relay Studio version")
    return match.group(1)


def find_iscc(explicit: Path | None = None) -> Path:
    if explicit is not None:
        compiler = Path(explicit)
        if compiler.is_file():
            return compiler
        raise FileNotFoundError("Inno Setup compiler was not found")
    found = shutil.which("ISCC.exe") or shutil.which("iscc")
    if found:
        return Path(found)
    for candidate in (
        Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
        Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Install Inno Setup 6 to build the Windows Setup")


def build_setup(source: Path = DEFAULT_SOURCE, output: Path = DEFAULT_OUTPUT,
                compiler: Path | None = None) -> Path:
    source = Path(source).resolve()
    output = Path(output).resolve()
    inspect_package_tree(source)
    iscc = find_iscc(compiler)
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        str(iscc),
        f"/DAppVersion={app_version()}",
        f"/DSourceDir={source}",
        f"/O{output}",
        str(ISS_FILE),
    ], cwd=ROOT, check=True)
    setup = output / f"RelayStudio-Setup-{app_version()}-win-x64.exe"
    if not setup.is_file() or setup.stat().st_size == 0:
        raise FileNotFoundError("Inno Setup did not create the expected installer")
    return setup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--iscc", type=Path)
    args = parser.parse_args()
    print(build_setup(args.source, args.output, args.iscc))
