"""Build a windowless Relay Studio directory with an external default config."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


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


def build(dist_dir=None, work_dir=None, entry="tray"):
    root_dir = Path(__file__).resolve().parent.parent
    if entry not in {"tray", "start"}:
        raise ValueError("entry must be tray or start")
    app_name = "RelayStudio" if entry == "tray" else "start"
    main_py = root_dir / "src" / "main.py" if entry == "tray" else root_dir / "start_app.py"
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
        "--noconsole",
        "--clean",
        "--noconfirm",
        f"--manifest={manifest}",
        f"--icon={app_icon}",
        f"--paths={root_dir / 'src' if entry == 'tray' else root_dir}",
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

    package_config = package_dir / "config" / "default.json"
    package_config.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(default_config, package_config)
    package_icon = package_dir / "assets" / "relay-studio.ico"
    package_icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(app_icon, package_icon)

    print("\n" + "=" * 60)
    print(f"BUILD SUCCESSFUL: {exe_path}")
    print(f"CONFIG: {package_config}")
    print("=" * 60)
    return exe_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, help="PyInstaller output directory")
    parser.add_argument("--work-dir", type=Path, help="isolated PyInstaller work directory")
    parser.add_argument("--entry", choices=("tray", "start"), default="tray",
                        help="tray legacy executable or windowed start.exe")
    args = parser.parse_args()
    build(args.dist_dir, args.work_dir, args.entry)
