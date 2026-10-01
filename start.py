"""Run Relay Studio directly from this checkout: py -3 start.py."""

from pathlib import Path
import ctypes
import os
import sys

import psutil


ROOT = Path(__file__).resolve().parent


def _session_id(pid: int) -> int | None:
    value = ctypes.c_ulong()
    if ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(value)):
        return value.value
    return None


def _known_packaged_suite(path: Path) -> bool:
    path = path.resolve()
    if path.name.casefold() not in {"start.exe", "happsuite.exe"}:
        return False
    installed = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "HappSuite"
    return path.is_relative_to(ROOT / "dist") or path.is_relative_to(installed)


def _known_source_suite(process: psutil.Process) -> bool:
    launchers = {ROOT / "start.py", ROOT / "start.pyw"}
    try:
        return any(Path(argument).resolve() in launchers for argument in process.cmdline()[1:])
    except (OSError, psutil.Error):
        return False


def _stop_old_suite() -> None:
    """Free the app's hotkeys without touching VPN or Antigravity processes."""
    session = _session_id(os.getpid())
    for process in psutil.process_iter(["pid", "exe"]):
        try:
            if process.pid == os.getpid():
                continue
            executable = process.info["exe"]
            if not executable or _session_id(process.pid) != session:
                continue
            if _known_packaged_suite(Path(executable)) or _known_source_suite(process):
                print(f"Stopping previous Relay Studio (PID {process.pid})", flush=True)
                process.terminate()
                process.wait(timeout=5)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def main() -> None:
    if "--bootstrap-mihomo" in sys.argv[1:]:
        from src.components.mihomo import bootstrap_mihomo
        raise SystemExit(bootstrap_mihomo())
    if sys.platform != "win32":
        raise SystemExit("Relay Studio needs Windows")
    from src.start import main as start_main
    start_main()


if __name__ == "__main__":
    main()
