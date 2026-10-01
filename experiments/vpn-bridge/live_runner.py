"""Independent Task Scheduler worker/watchdog for a five-second HAPP cycle.

The launcher writes run.json before either process starts. DRY_RUN uses a local
mock state file and never sends a HAPP command. LIVE requires the launcher's
explicit confirmation gate and uses the same HAPP controller as F8.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace
import winreg

import psutil


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.happ_controller import HappController  # noqa: E402


RUN_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "HappSuite" / "vpn-bridge"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _event(run_dir: Path, source: str, event: str, detail: str = "") -> None:
    record = {
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source, "event": event, "detail": detail,
    }
    with (run_dir / f"{source}-events.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False) + "\n")
        output.flush()


def _session_id() -> int:
    session_id = wintypes.DWORD()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
        raise OSError("Cannot identify scheduled task session")
    return session_id.value


def _state(run_dir: Path, source: str, status: str, **extra) -> None:
    record = {
        "status": status, "updated_unix": time.time(),
        "pid": os.getpid(), "process_started_unix": psutil.Process().create_time(),
        "session_id": _session_id(),
        **extra,
    }
    _write(run_dir / f"{source}.json", record)
    _event(run_dir, source, status)


class MockController:
    def __init__(self, run_dir: Path, pid: int):
        self.path = run_dir / "mock-network.json"
        self.pid = pid

    def read_status(self):
        connected = bool(_read(self.path)["connected"])
        return SimpleNamespace(
            connected=connected,
            gui_pid=self.pid,
            route=SimpleNamespace(through_happ=connected),
            external_ok=connected,
        )

    def disconnect(self) -> bool:
        _write(self.path, {"connected": False, "updated_unix": time.time()})
        return True

    def connect_current_profile(self) -> bool:
        _write(self.path, {"connected": True, "updated_unix": time.time()})
        return True


def _controller(run_dir: Path, run: dict):
    if run["mode"] == "DRY_RUN":
        return MockController(run_dir, run["happ_pid"])
    if run["mode"] != "LIVE" or run.get("confirmed_network_interruption") is not True:
        raise RuntimeError("Live mode was not authorized in the run manifest")
    return HappController(run["happ_exe"], timeout_s=45)


def _same_happ(run: dict, controller) -> bool:
    observed = controller.read_status()
    return observed.gui_pid == run["happ_pid"]


def _selection_unchanged(run: dict) -> bool:
    if run["mode"] == "DRY_RUN":
        return True
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Happ\OrganizationDefaults\Preferences",
        ) as key:
            server, _ = winreg.QueryValueEx(key, "lastServer")
            subscription, _ = winreg.QueryValueEx(key, "lastSubscription")
        return server == run["last_server_id"] and subscription == run["last_subscription_id"]
    except OSError:
        return False


def _worker_still_alive(state: dict) -> bool:
    try:
        process = psutil.Process(int(state["pid"]))
        return abs(process.create_time() - float(state["process_started_unix"])) < 1
    except (KeyError, ValueError, psutil.Error):
        return False


def _verify_restored(controller) -> bool:
    observed = controller.read_status()
    return bool(observed.route.through_happ and observed.external_ok)


def _restore(run_dir: Path, run: dict, source: str) -> bool:
    controller = _controller(run_dir, run)
    for attempt in range(1, 4):
        try:
            if not _same_happ(run, controller):
                raise RuntimeError("HAPP GUI PID changed; refusing to contact a different process")
            _event(run_dir, source, "restore_attempt", f"attempt={attempt}")
            controller.connect_current_profile()
            if _verify_restored(controller):
                _state(
                    run_dir, source, "restored", attempt=attempt, connected=True,
                    profile_preference_preserved=_selection_unchanged(run),
                )
                return True
        except Exception as exc:
            _event(run_dir, source, "restore_attempt_failed", type(exc).__name__)
        time.sleep(1)
    _state(run_dir, source, "restore_failed", connected=False)
    return False


def worker(run_dir: Path, run: dict) -> int:
    controller = _controller(run_dir, run)
    try:
        observed = controller.read_status()
        if observed.gui_pid != run["happ_pid"] or not observed.connected:
            raise RuntimeError("Preflight HAPP PID or connected route changed")
        if run["mode"] == "LIVE" and _session_id() != run["happ_session_id"]:
            raise RuntimeError("Worker is not in HAPP's user session")
        if not _selection_unchanged(run):
            raise RuntimeError("HAPP selected profile changed before disconnect")
        _state(run_dir, "worker", "disconnecting")
        if not controller.disconnect():
            raise RuntimeError("HAPP did not remove its TUN route")
        _state(run_dir, "worker", "disconnected", disconnected_unix=time.time())

        if run["mode"] == "DRY_RUN" and run.get("scenario") == "hang":
            _event(run_dir, "worker", "simulated_crash", "watchdog must restore mock state")
            os._exit(2)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            _state(run_dir, "worker", "waiting_five_seconds")
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))

        _state(run_dir, "worker", "connecting")
        if not controller.connect_current_profile() or not _verify_restored(controller):
            raise RuntimeError("HAPP route and HTTPS were not restored")
        _state(run_dir, "worker", "completed", connected=True)
        return 0
    except Exception as exc:
        _event(run_dir, "worker", "error", f"{type(exc).__name__}: {exc}")
        _state(run_dir, "worker", "error", error_type=type(exc).__name__)
        return 0 if _restore(run_dir, run, "worker") else 1


def watchdog(run_dir: Path, run: dict) -> int:
    _state(run_dir, "watchdog", "ready")
    deadline = time.monotonic() + 85
    worker_seen = False
    while time.monotonic() < deadline:
        path = run_dir / "worker.json"
        if path.exists():
            worker_seen = True
            try:
                state = _read(path)
            except (OSError, ValueError):
                time.sleep(0.3)
                continue
            status = state.get("status")
            if status in {"completed", "restored"}:
                try:
                    if _verify_restored(_controller(run_dir, run)):
                        _state(run_dir, "watchdog", "worker_finished", connected=True)
                        return 0
                    _event(run_dir, "watchdog", "worker_finished_without_connection")
                except Exception as exc:
                    _event(run_dir, "watchdog", "final_check_failed", type(exc).__name__)
                return 0 if _restore(run_dir, run, "watchdog") else 1
            elapsed = time.time() - float(state.get("updated_unix", 0))
            stage_limit = 55 if status in {"connecting", "disconnecting", "error"} else 10
            dead = not _worker_still_alive(state)
            if status == "restore_failed" or dead or elapsed > stage_limit:
                _event(run_dir, "watchdog", "takeover", f"stage={status};age={elapsed:.1f};dead={dead}")
                return 0 if _restore(run_dir, run, "watchdog") else 1
        elif not worker_seen and time.monotonic() > deadline - 65:
            _state(run_dir, "watchdog", "worker_never_started")
            return 1
        time.sleep(0.3)
    _event(run_dir, "watchdog", "deadline", "attempting recovery")
    return 0 if _restore(run_dir, run, "watchdog") else 1


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in {"worker", "watchdog"}:
        return 2
    run_id = sys.argv[2].lower()
    if len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        return 2
    run_dir = RUN_ROOT / run_id
    try:
        run = _read(run_dir / "run.json")
        if run.get("run_id") != run_id:
            raise RuntimeError("Run manifest mismatch")
        return worker(run_dir, run) if sys.argv[1] == "worker" else watchdog(run_dir, run)
    except Exception:
        run_dir.mkdir(parents=True, exist_ok=True)
        _event(run_dir, sys.argv[1], "fatal", traceback.format_exc(limit=3)[-1500:])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
