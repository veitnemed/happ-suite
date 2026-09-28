"""Start the installed AG Unlocker relay and read its local readiness record.

The Unlocker installer owns the scheduled task and its DNS/proxy settings. This
module only starts that existing task; it never installs, reconfigures or stops
it. No Unlocker GUI or visible console window is launched by Happ Suite.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from typing import Optional


TASK_NAME = "AG Unlocker DNS"
TASK_STATE_RUNNING = 4
TASK_LOGON_S4U = 2
TASK_ACTION_EXEC = 0
RELAY_PORT = 53129
GATE_MAX_BYTES = 256 * 1024


@dataclass(frozen=True)
class RelayStartResult:
    ready: bool
    started_by_suite: bool
    reason: str = ""


@dataclass(frozen=True)
class GateReadiness:
    """Only non-sensitive facts from AG Unlocker's gate.json."""

    relay_fresh: bool
    model_response_fresh: bool
    network_reached_recently: bool
    blocked: bool
    report_at: Optional[int] = None
    model_response_at: Optional[int] = None

    @property
    def ready(self) -> bool:
        return (
            self.relay_fresh
            and self.model_response_fresh
            and self.network_reached_recently
            and not self.blocked
        )


@dataclass(frozen=True)
class ModelProbeResult:
    success: bool
    reason: str = ""
    model_response_at: Optional[int] = None


def _relay_listener_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", RELAY_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def _expected_relay_path() -> str:
    program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    return os.path.normcase(os.path.abspath(os.path.join(program_data, "AGUnlocker", "ag_dns.exe")))


def _task_is_safe_to_start(task) -> bool:
    """Reject tasks that could open a window or execute a different program."""
    definition = task.Definition
    if not task.Enabled or definition.Principal.LogonType != TASK_LOGON_S4U:
        return False
    actions = definition.Actions
    if actions.Count != 1:
        return False
    action = actions.Item(1)
    if action.Type != TASK_ACTION_EXEC or action.Arguments.strip() != "--dns-forwarder":
        return False
    path = os.path.normcase(os.path.abspath(os.path.expandvars(action.Path.strip().strip('"'))))
    return path == _expected_relay_path() and os.path.isfile(path)


def ensure_dns_relay(timeout_s: float = 5.0) -> RelayStartResult:
    """Start the existing S4U task if needed, without opening a console or GUI.

    Safe to call from a background thread. `started_by_suite` is true only when
    this invocation asked Task Scheduler to start a previously stopped task.
    """
    if os.name != "nt":
        return RelayStartResult(False, False, "Windows Task Scheduler is required")

    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return RelayStartResult(False, False, "Task Scheduler COM support is unavailable")

    started_by_suite = False
    try:
        pythoncom.CoInitialize()
    except Exception as exc:
        return RelayStartResult(False, False, f"COM initialization failed: {type(exc).__name__}")
    service = None
    task = None
    try:
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        task = service.GetFolder("\\").GetTask(TASK_NAME)
        if not _task_is_safe_to_start(task):
            return RelayStartResult(False, False, "Installed AG Unlocker task is not a hidden DNS relay")

        already_running = task.State == TASK_STATE_RUNNING
        if not already_running:
            task.Run("")
            started_by_suite = True

        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            if task.State == TASK_STATE_RUNNING and _relay_listener_open():
                return RelayStartResult(True, started_by_suite)
            if time.monotonic() >= deadline:
                return RelayStartResult(False, started_by_suite, "AG Unlocker task or listener did not become ready")
            time.sleep(0.25)
    except Exception as exc:
        return RelayStartResult(False, started_by_suite, f"Task Scheduler error: {type(exc).__name__}")
    finally:
        # Release COM objects before uninitializing the worker thread's COM.
        task = None
        service = None
        pythoncom.CoUninitialize()


def stop_dns_relay(timeout_s: float = 5.0) -> RelayStartResult:
    """Stop only the installed DNS scheduled task, keeping its registration.

    F9 can start the same task again. This does not remove the installer's NRPT
    or proxy settings; Antigravity traffic may fail while the relay is stopped.
    """
    if os.name != "nt":
        return RelayStartResult(False, False, "Windows Task Scheduler is required")
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return RelayStartResult(False, False, "Task Scheduler COM support is unavailable")

    pythoncom.CoInitialize()
    task = None
    service = None
    try:
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        task = service.GetFolder("\\").GetTask(TASK_NAME)
        if not _task_is_safe_to_start(task):
            return RelayStartResult(False, False, "Installed AG Unlocker task differs from the expected relay")
        if task.State == TASK_STATE_RUNNING:
            task.Stop(0)
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            if task.State != TASK_STATE_RUNNING and not _relay_listener_open():
                return RelayStartResult(True, False)
            if time.monotonic() >= deadline:
                return RelayStartResult(False, False, "AG Unlocker task or listener did not stop")
            time.sleep(0.25)
    except Exception as exc:
        return RelayStartResult(False, False, f"Task Scheduler error: {type(exc).__name__}")
    finally:
        task = None
        service = None
        pythoncom.CoUninitialize()


def _gate_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "AGUnlocker" / "gate.json"


def _unix_seconds(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def read_gate_readiness(
    after_unix: float,
    *,
    max_relay_age_s: int = 150,
    max_network_age_s: int = 240,
    gate_path: Optional[Path] = None,
    now_unix: Optional[float] = None,
) -> GateReadiness:
    """Check the installed relay's report without probing or changing its state.

    A model answer counts only if the relay recorded it after `after_unix`.
    `gate.json` may describe an old process, so its own heartbeat must also be
    recent. Missing, malformed and oversized records fail closed.
    """
    path = Path(gate_path) if gate_path is not None else _gate_path()
    empty = GateReadiness(False, False, False, False)
    try:
        if path.stat().st_size > GATE_MAX_BYTES:
            return empty
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return empty
    if not isinstance(report, dict):
        return empty

    now = time.time() if now_unix is None else now_unix
    report_at = _unix_seconds(report.get("at"))
    reached_at = _unix_seconds(report.get("reached_at"))
    last_ok = report.get("last_ok")
    model_at = _unix_seconds(last_ok.get("at")) if isinstance(last_ok, dict) else None
    blockers = report.get("blockers")
    blocked = not isinstance(blockers, list) or bool(blockers)
    relay_fresh = report_at is not None and 0 <= now - report_at <= max_relay_age_s
    network_recent = reached_at is not None and 0 <= now - reached_at <= max_network_age_s
    model_fresh = model_at is not None and after_unix <= model_at <= now
    return GateReadiness(
        relay_fresh=relay_fresh,
        model_response_fresh=model_fresh,
        network_reached_recently=network_recent,
        blocked=blocked,
        report_at=report_at,
        model_response_at=model_at,
    )


def probe_model_response(
    *,
    cli_timeout_s: int = 30,
    gate_timeout_s: int = 20,
) -> ModelProbeResult:
    """Ask the installed Antigravity CLI for one answer without showing a window.

    A successful CLI JSON result is paired with a new model-answer timestamp in
    the Unlocker relay's gate.json. The prompt uses no account or project data;
    CLI output is never returned or logged. Headless mode exits on missing auth.
    """
    if os.name != "nt":
        return ModelProbeResult(False, "Antigravity CLI probe requires Windows")
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    cli = base / "agy" / "bin" / "agy.exe"
    if not cli.is_file():
        return ModelProbeResult(False, "Antigravity CLI is not installed")
    if not _relay_listener_open():
        return ModelProbeResult(False, "AG Unlocker listener is not running")

    before = read_gate_readiness(time.time())
    if not before.relay_fresh or before.blocked:
        return ModelProbeResult(False, "AG Unlocker relay is not ready for a model probe")

    cli_limit = min(max(int(cli_timeout_s), 1), 60)
    gate_limit = min(max(int(gate_timeout_s), 1), 60)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    args = [
        str(cli),
        "--print", "Reply with exactly READY. Do not use tools or inspect files.",
        "--output-format", "json",
        "--print-timeout", f"{cli_limit}s",
        "--disable-slash-commands",
        "--mode", "plan",
    ]

    probe_started_at = int(time.time())
    try:
        with tempfile.TemporaryDirectory(prefix="HappSuite-AG-Probe-", ignore_cleanup_errors=True) as neutral_dir:
            completed = subprocess.run(
                args,
                cwd=neutral_dir,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=cli_limit + 5,
                creationflags=subprocess.CREATE_NO_WINDOW,
                startupinfo=startup,
                check=False,
            )
    except subprocess.TimeoutExpired:
        return ModelProbeResult(False, "Antigravity CLI model probe timed out")
    except OSError as exc:
        return ModelProbeResult(False, f"Antigravity CLI could not start: {type(exc).__name__}")

    try:
        payload = json.loads(completed.stdout)
    except ValueError:
        return ModelProbeResult(False, "Antigravity CLI returned invalid JSON")
    if not isinstance(payload, dict):
        return ModelProbeResult(False, "Antigravity CLI returned invalid JSON")
    if completed.returncode != 0 or payload.get("status") != "SUCCESS":
        error = str(payload.get("error", "")).lower()
        if "auth" in error or "login" in error or "sign in" in error:
            return ModelProbeResult(False, "Antigravity CLI authentication is required")
        if "location is not supported" in error or "region is not supported" in error:
            return ModelProbeResult(False, "Antigravity API rejected the current location (400)")
        return ModelProbeResult(False, "Antigravity CLI did not return a successful model response")
    if not isinstance(payload.get("response"), str) or not payload["response"].strip():
        return ModelProbeResult(False, "Antigravity CLI returned an empty model response")

    deadline = time.monotonic() + gate_limit
    while True:
        gate = read_gate_readiness(probe_started_at)
        if gate.ready:
            return ModelProbeResult(True, model_response_at=gate.model_response_at)
        if time.monotonic() >= deadline:
            return ModelProbeResult(False, "Unlocker did not record the new model response")
        time.sleep(0.5)
