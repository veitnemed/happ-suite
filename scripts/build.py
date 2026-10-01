"""Build a windowless Relay Studio directory with an external default config."""

import argparse
import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
from importlib import metadata


PRIVATE_CONFIG_FIELDS = {
    "ag_unlocker_key",
    "api_key",
    "password",
    "private_key",
    "secret",
    "token",
    "subscription_url",
    "mihomo_subscription_url",
}


def _exclude_windows_icu(package_dir):
    """Qt's Windows ICU API must not be shadowed by an ICU found on PATH.

    PyInstaller can collect e.g. Git's ICU 78 as icuuc.dll. Its versioned
    exports are incompatible with the unversioned Windows ICU imports used
    by the official PySide6 wheel. Inspect both APIs before excluding it.
    """
    internal = Path(package_dir) / "_internal"
    qt_core = internal / "PySide6" / "Qt6Core.dll"
    bundled = internal / "icuuc.dll"
    if not qt_core.is_file() or not bundled.is_file():
        return
    import pefile

    with pefile.PE(str(qt_core)) as binary:
        expected = {symbol.name for descriptor in binary.DIRECTORY_ENTRY_IMPORT
                    if descriptor.dll.lower() == b"icuuc.dll"
                    for symbol in descriptor.imports if symbol.name}
    # A custom Qt build using a versioned ICU has a different deployment contract.
    if b"ucnv_open" not in expected:
        return
    system = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32" / "icuuc.dll"
    with pefile.PE(str(system)) as binary:
        available = {symbol.name for symbol in binary.DIRECTORY_ENTRY_EXPORT.symbols}
    if not expected <= available:
        raise RuntimeError("Windows ICU does not provide the API required by Qt")
    bundled.unlink()
    print("Qt uses Windows ICU; excluded a DLL collected from PATH.")


def _check_public_config(value):
    """Keep credentials out of the distributable default configuration."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key.casefold() in PRIVATE_CONFIG_FIELDS:
                raise ValueError(f"config/default.json contains private field: {key}")
            _check_public_config(item)
    elif isinstance(value, list):
        for item in value:
            _check_public_config(item)


def build(dist_dir=None, work_dir=None, entry="tray", *, console=False):
    root_dir = Path(__file__).resolve().parent.parent
    if entry not in {"tray", "start"}:
        raise ValueError("entry must be tray or start")
    # "tray" remains accepted as a compatibility alias. The release executable
    # must go through start_app.py so imports retain the src.start package name.
    app_name = "RelayStudio"
    main_py = root_dir / "start_app.py"
    default_config = root_dir / "config" / "default.json"
    manifest = root_dir / "assets" / "relay-studio.manifest"
    app_icon = root_dir / "assets" / "relay-studio.ico"
    dist_dir = Path(dist_dir).resolve() if dist_dir else root_dir / "dist"
    work_dir = Path(work_dir).resolve() if work_dir else root_dir / "build" / app_name

    with default_config.open("r", encoding="utf-8") as source:
        _check_public_config(json.load(source))

    cmd = [
        sys.executable,
        "-m", "PyInstaller",
        f"--name={app_name}",
        "--onedir",
        "--console" if console else "--noconsole",
        "--clean",
        "--noconfirm",
        f"--manifest={manifest}",
        f"--icon={app_icon}",
        f"--paths={root_dir}",
        f"--add-data={root_dir / 'src' / 'data'};data",
        f"--distpath={dist_dir}",
        f"--workpath={work_dir}",
        f"--specpath={work_dir}",
        "--hidden-import=pystray",
        "--hidden-import=PIL",
        "--hidden-import=psutil",
        "--hidden-import=requests",
        str(main_py),
    ]

    print("Running PyInstaller build:")
    print(" ".join(cmd))
    subprocess.run(cmd, cwd=root_dir, check=True)

    package_dir = dist_dir / app_name
    exe_path = package_dir / f"{app_name}.exe"
    if not exe_path.is_file():
        raise FileNotFoundError(f"PyInstaller did not create {exe_path}")
    _exclude_windows_icu(package_dir)

    package_config = package_dir / "config" / "default.json"
    package_config.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(default_config, package_config)
    package_icon = package_dir / "assets" / "relay-studio.ico"
    package_icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(app_icon, package_icon)
    shutil.copyfile(root_dir / "THIRD_PARTY_NOTICES.md", package_dir / "THIRD_PARTY_NOTICES.md")
    shutil.copytree(root_dir / "licenses", package_dir / "licenses", dirs_exist_ok=True)
    for distribution_name in ("PySide6", "PySide6_Essentials", "shiboken6", "Pillow",
                              "requests", "PyYAML", "psutil", "pystray", "pywin32"):
        try:
            distribution = metadata.distribution(distribution_name)
        except metadata.PackageNotFoundError:
            continue
        for relative in distribution.files or ():
            if any(part.lower() == "licenses" for part in relative.parts) or relative.name.lower().startswith(("license", "copying")):
                source = Path(distribution.locate_file(relative))
                if source.is_file():
                    target = package_dir / "licenses" / "dependencies" / distribution_name / relative.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)

    print("\n" + "=" * 60)
    print(f"BUILD SUCCESSFUL: {exe_path}")
    print(f"CONFIG: {package_config}")
    print("=" * 60)
    return exe_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, help="PyInstaller output directory")
    parser.add_argument("--work-dir", type=Path, help="isolated PyInstaller work directory")
    parser.add_argument("--entry", choices=("tray", "start"), default="start",
                        help="windowed RelayStudio executable (tray is a legacy alias)")
    parser.add_argument("--console", action="store_true", help="diagnostic build with console output")
    args = parser.parse_args()
    build(args.dist_dir, args.work_dir, args.entry, console=args.console)
