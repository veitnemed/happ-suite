"""
Build script to compile Happ Suite into a single standalone executable using PyInstaller.
"""
import os
import subprocess
import sys

def build():
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main_py = os.path.join(root_dir, "src", "main.py")
    config_dir = os.path.join(root_dir, "config")
    dist_dir = os.path.join(root_dir, "dist")
    build_dir = os.path.join(root_dir, "build")

    cmd = [
        sys.executable,
        "-m", "PyInstaller",
        "--name=HappSuite",
        "--onefile",
        "--noconsole",
        "--clean",
        f"--add-data={config_dir};config",
        f"--distpath={dist_dir}",
        f"--workpath={build_dir}",
        "--hidden-import=pystray",
        "--hidden-import=PIL",
        "--hidden-import=psutil",
        "--hidden-import=requests",
        main_py
    ]

    print("Running PyInstaller build:")
    print(" ".join(cmd))
    result = subprocess.run(cmd, cwd=root_dir)
    if result.returncode == 0:
        exe_path = os.path.join(dist_dir, "HappSuite.exe")
        print("\n" + "=" * 60)
        print(f"BUILD SUCCESSFUL: {exe_path}")
        print("=" * 60)
    else:
        print(f"\nBUILD FAILED with exit code {result.returncode}")
        sys.exit(result.returncode)

if __name__ == "__main__":
    build()
