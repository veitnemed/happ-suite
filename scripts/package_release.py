"""Make two clean Windows ZIPs from the built start.exe directory."""

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


VERSION = "2.3.0"
ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "dist" / "start"
OUTPUT = ROOT / "dist" / "release"


def build_zip(variant: str, source: Path = SOURCE, output: Path = OUTPUT) -> Path:
    if variant not in {"setup", "lite"}:
        raise ValueError(variant)
    if not (source / "start.exe").is_file() or not (source / "config" / "default.json").is_file():
        raise FileNotFoundError("Build start.exe first: py -3 scripts/build.py --entry start")
    output.mkdir(parents=True, exist_ok=True)
    zip_path = output / f"RelayStudio-{variant}-{VERSION}-win-x64.zip"
    readme = (ROOT / "README.md").read_bytes()
    license_text = (ROOT / "LICENSE").read_bytes()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                relative = path.relative_to(source)
                if any(part in {"logs", "__pycache__"} for part in relative.parts):
                    continue
                archive.write(path, relative.as_posix())
        archive.writestr("variant.json", json.dumps({"variant": variant}, ensure_ascii=False) + "\n")
        archive.writestr("README.txt", readme)
        archive.writestr("LICENSE.txt", license_text)
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        if {"start.exe", "config/default.json", "variant.json", "README.txt"} - names:
            raise RuntimeError("Release ZIP is missing required files")
        if any("local.json" in name or "hotkeys.json" in name or "gate.json" in name
               for name in names):
            raise RuntimeError("Release ZIP contains user settings")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_suffix(zip_path.suffix + ".sha256").write_text(
        f"{digest}  {zip_path.name}\n", encoding="ascii"
    )
    print(f"{zip_path} | SHA256 {digest}")
    return zip_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    options = parser.parse_args()
    build_zip("setup", options.source.resolve(), options.output.resolve())
    build_zip("lite", options.source.resolve(), options.output.resolve())
