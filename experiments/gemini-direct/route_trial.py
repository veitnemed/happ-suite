"""Independent, journaled HAPP routing trial for Gemini through Xbox DNS.

The Task Scheduler launcher starts the watchdog before the worker. The only
live mutation is HAPP's documented routing deeplink; rollback sends routing/off
because the launcher requires no previously active global routing profile.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys
import time

import psutil
import requests


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.happ_controller import HappController  # noqa: E402
from src.happ_ipc import HappIpcClient  # noqa: E402


RUN_ROOT = Path(os.environ["LOCALAPPDATA"]) / "HappSuite" / "gemini-route"
HAPP_ROUTING = Path(os.environ["LOCALAPPDATA"]) / "Happ" / "routing.json"
PROFILE = Path(__file__).with_name("profile.json")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _record(run_dir: Path, role: str, status: str, **details) -> None:
    _write(run_dir / f"{role}.json", {
        "status": status, "at": time.time(), "pid": os.getpid(), **details,
    })


def _controller(run: dict) -> HappController:
    return HappController(run["happ_exe"], timeout_s=30)


def _same_gui(run: dict) -> bool:
    return _controller(run).ipc.probe() == run["happ_pid"]


def _send_routing(run: dict, action: str) -> None:
    if action == "off":
        uri = "happ://routing/off"
    elif action == "onadd":
        profile = _read(PROFILE)
        if profile.get("Name") != run["profile_name"]:
            raise RuntimeError("Routing profile changed after preflight")
        if profile.get("DirectSites") != ["domain:gemini.google.com"]:
            raise RuntimeError("Unexpected direct domain rule")
        if profile.get("DomesticDNSIP") != run["xbox_dns"]:
            raise RuntimeError("Xbox DNS changed after preflight")
        encoded = base64.b64encode(json.dumps(profile, separators=(",", ":"), ensure_ascii=False).encode()).decode()
        uri = f"happ://routing/onadd/{encoded}"
    else:
        raise ValueError("Unsupported routing action")
    if not _same_gui(run):
        raise RuntimeError("Original HAPP GUI is unavailable")
    # SingleApplication IPC is already used by F8. It does not launch or focus HAPP.
    HappIpcClient(run["happ_exe"])._send(f"Happ.exe,{uri}".encode("utf-8"))


def _http_status(url: str, *, proxy: bool = False) -> int | None:
    try:
        with requests.Session() as session:
            session.trust_env = False
            if proxy:
                session.proxies = {"http": "http://127.0.0.1:10809", "https": "http://127.0.0.1:10809"}
            return session.get(url, timeout=8, allow_redirects=False).status_code
    except requests.RequestException:
        return None


def _restore(run_dir: Path, run: dict, role: str) -> bool:
    for attempt in range(1, 4):
        try:
            active = _read(HAPP_ROUTING).get("activeRoutingName")
            if active == run["profile_name"]:
                _send_routing(run, "off")
            elif active:
                raise RuntimeError("Another routing profile became active; refusing to replace it")
            controller = _controller(run)
            if not controller.read_status().route.through_happ:
                controller.connect_current_profile()
            status = controller.read_status()
            if status.connected and _http_status("https://www.google.com/generate_204") == 204:
                _record(run_dir, role, "restored", attempt=attempt)
                return True
        except Exception as exc:
            _record(run_dir, role, "restore_retry", attempt=attempt, error=type(exc).__name__)
        time.sleep(1)
    _record(run_dir, role, "restore_failed")
    return False


def worker(run_dir: Path, run: dict) -> int:
    try:
        if run.get("authorized") is not True or _read(HAPP_ROUTING).get("activeRoutingName"):
            raise RuntimeError("Live authorization or original routing state is missing")
        if not _same_gui(run) or not _controller(run).read_status().connected:
            raise RuntimeError("Original HAPP connection is no longer ready")
        _record(run_dir, "worker", "applying")
        _send_routing(run, "onadd")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if _read(HAPP_ROUTING).get("activeRoutingName") == run["profile_name"]:
                break
            time.sleep(0.3)
        else:
            raise RuntimeError("HAPP did not activate the routing profile")
        _record(run_dir, "worker", "probing")
        controller = _controller(run)
        route_deadline = time.monotonic() + 20
        while time.monotonic() < route_deadline:
            if controller.read_status().connected:
                break
            time.sleep(1)
        else:
            raise RuntimeError("HAPP tunnel or normal HTTPS was lost")
        direct = _http_status("https://gemini.google.com/")
        proxied = _http_status("https://gemini.google.com/", proxy=True)
        if direct is None or proxied is None or not (200 <= direct < 400 and 200 <= proxied < 400):
            raise RuntimeError(f"Gemini HTTPS failed: direct={direct}, proxy={proxied}")
        _record(run_dir, "worker", "completed", gemini_direct_http=direct, gemini_proxy_http=proxied)
        return 0
    except Exception as exc:
        _record(run_dir, "worker", "error", error=type(exc).__name__, detail=str(exc)[:200])
        return 0 if _restore(run_dir, run, "worker") else 1


def watchdog(run_dir: Path, run: dict) -> int:
    _record(run_dir, "watchdog", "ready")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        path = run_dir / "worker.json"
        if path.exists():
            try:
                state = _read(path)
                if state.get("status") == "completed":
                    gemini_status = _http_status("https://gemini.google.com/")
                    if (_controller(run).read_status().connected and gemini_status is not None
                            and 200 <= gemini_status < 400):
                        _record(run_dir, "watchdog", "worker_finished")
                        return 0
                    break
                if state.get("status") == "restored":
                    _record(run_dir, "watchdog", "worker_restored")
                    return 0
                if state.get("status") == "restore_failed":
                    break
                pid = state.get("pid")
                if pid and not psutil.pid_exists(int(pid)):
                    break
                if time.time() - float(state.get("at", 0)) > 60:
                    break
            except (OSError, ValueError, TypeError):
                pass
        time.sleep(0.5)
    return 0 if _restore(run_dir, run, "watchdog") else 1


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in {"worker", "watchdog"}:
        raise SystemExit(2)
    run_id = sys.argv[2].lower()
    if len(run_id) != 32 or any(char not in "0123456789abcdef" for char in run_id):
        raise SystemExit(2)
    directory = RUN_ROOT / run_id
    manifest = _read(directory / "run.json")
    if manifest.get("run_id") != run_id:
        raise SystemExit(2)
    raise SystemExit(worker(directory, manifest) if sys.argv[1] == "worker" else watchdog(directory, manifest))
