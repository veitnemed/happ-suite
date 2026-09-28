"""
Core orchestrator — manages lifecycle of all bypass components.

Components:
  1. Zapret (winws.exe) — DPI bypass for YouTube/Discord
  2. Happ VPN (Happ.exe → happd.exe → xray.exe) — VPN tunnel
  3. AG Unlocker (ag_dns.exe) — Gemini DNS proxy

Design:
  - HAPP is launched with a pre-creation SW_HIDE startup hint; GUI applications
    may ignore it, so an invisible launch is not guaranteed or verified.
  - Process health is monitored via TCP port checks, not process polling.
  - Components can be started/stopped independently or all at once.
"""
import ctypes
import ctypes.wintypes
import logging
import os
import socket
import subprocess
import threading
import time
import winreg
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

try:
    from .app_config import Config
    from .happ_controller import HappController
    from .ag_unlocker import ensure_dns_relay, stop_dns_relay, read_gate_readiness, probe_model_response
except (ImportError, ValueError):
    from app_config import Config
    from happ_controller import HappController
    from ag_unlocker import ensure_dns_relay, stop_dns_relay, read_gate_readiness, probe_model_response

logger = logging.getLogger("happ_suite.core")


class ComponentState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    STOPPING = "stopping"
    RUNNING = "running"
    DEGRADED = "degraded"
    ERROR = "error"
    RECOVERING = "recovering"


class DesiredState(Enum):
    OFF = "off"
    ON = "on"


class ComponentOwnership(Enum):
    SUITE = "suite"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


@dataclass
class ComponentRuntimeState:
    desired_state: DesiredState = DesiredState.OFF
    observed_state: ComponentState = ComponentState.STOPPED
    ownership: ComponentOwnership = ComponentOwnership.UNKNOWN


class Component:
    """Base class for a managed bypass component."""

    def __init__(self, name: str):
        self.name = name
        self.state = ComponentState.STOPPED
        self.runtime = ComponentRuntimeState()
        self._on_state_change: Optional[Callable] = None

    @property
    def desired_state(self) -> DesiredState:
        return self.runtime.desired_state

    @desired_state.setter
    def desired_state(self, value: DesiredState):
        self.runtime.desired_state = value

    @property
    def observed_state(self) -> ComponentState:
        return self.runtime.observed_state

    @property
    def ownership(self) -> ComponentOwnership:
        return self.runtime.ownership

    @ownership.setter
    def ownership(self, value: ComponentOwnership):
        self.runtime.ownership = value

    def request_enabled(self, enabled: bool):
        self.desired_state = DesiredState.ON if enabled else DesiredState.OFF

    def observe(self, state: ComponentState, *, preserve_transition: bool = False):
        """Record a system observation without changing user intent."""
        if state in (ComponentState.STARTING, ComponentState.STOPPING, ComponentState.RECOVERING):
            raise ValueError("transitional states are not observations")
        self.runtime.observed_state = state
        if preserve_transition and self.state in (
            ComponentState.STARTING, ComponentState.STOPPING, ComponentState.RECOVERING
        ):
            return
        self.set_state(state)

    def set_state(self, new_state: ComponentState):
        old = self.state
        self.state = new_state
        if new_state not in (ComponentState.STARTING, ComponentState.STOPPING, ComponentState.RECOVERING):
            self.runtime.observed_state = new_state
        if self._on_state_change and old != new_state:
            self._on_state_change(self.name, old, new_state)

    def on_state_change(self, callback: Callable):
        self._on_state_change = callback


# ─── Network helpers ───────────────────────────────────────────

def tcp_ping(host: str, port: int, timeout_ms: int = 1200) -> Optional[float]:
    """TCP connect and return latency in ms, or None on timeout."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_ms / 1000.0)
    try:
        t0 = time.perf_counter()
        sock.connect((host, port))
        elapsed = (time.perf_counter() - t0) * 1000
        return round(elapsed, 1)
    except (socket.timeout, OSError):
        return None
    finally:
        sock.close()


def is_port_open(host: str, port: int, timeout_ms: int = 600) -> bool:
    """Quick check if a TCP port is open."""
    return tcp_ping(host, port, timeout_ms) is not None


# ─── Win32 helpers ─────────────────────────────────────────────

# ─── Registry helpers ──────────────────────────────────────────

def _reg_set_value(key_path: str, name: str, value, value_type=winreg.REG_SZ):
    """Set a registry value under HKCU."""
    # key_path like "Software\\Happ\\OrganizationDefaults\\Preferences"
    clean_path = key_path.replace("HKCU\\", "").replace("HKCU/", "")
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, clean_path, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, name, 0, value_type, value)
    except OSError as e:
        logger.error(f"Registry write failed: {clean_path}\\{name} = {value}: {e}")


def _reg_get_value(key_path: str, name: str, default=None):
    """Get a registry value from HKCU."""
    clean_path = key_path.replace("HKCU\\", "").replace("HKCU/", "")
    try:
        with winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER, clean_path, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, name)
            return val
    except (OSError, FileNotFoundError):
        return default


# ─── Zapret Component ─────────────────────────────────────────

class ZapretComponent(Component):
    """Manages winws.exe (DPI bypass via Flowseal/zapret)."""

    def __init__(self, config: Config):
        super().__init__("Zapret")
        self.config = config
        self._process: Optional[subprocess.Popen] = None
        self._owned_winws: Dict[int, Tuple[float, str]] = {}

    @staticmethod
    def _winws_processes() -> Optional[Dict[int, Tuple[float, str]]]:
        """Return process identities, or None when ownership cannot be checked."""
        try:
            import psutil
            found = {}
            for proc in psutil.process_iter(["name", "pid", "create_time", "exe"]):
                try:
                    name = proc.info.get("name")
                    if name and name.lower() == "winws.exe":
                        pid = int(proc.info["pid"])
                        created = float(proc.info["create_time"])
                        executable = os.path.normcase(os.path.abspath(proc.info["exe"]))
                        found[pid] = (created, executable)
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    continue
                except Exception:
                    return None
            return found
        except Exception:
            logger.debug("Could not establish winws.exe process ownership", exc_info=True)
            return None

    def is_running(self) -> bool:
        """Check if winws.exe is running (any instance)."""
        processes = self._winws_processes()
        return bool(processes)

    def start(self) -> bool:
        """Start zapret if not already running."""
        self.request_enabled(True)
        before = self._winws_processes()
        if before is None:
            self.ownership = ComponentOwnership.UNKNOWN
            self.observe(ComponentState.DEGRADED)
            logger.warning("Cannot safely determine existing winws.exe ownership; refusing to start Zapret")
            return False
        if before:
            self._owned_winws = {
                pid: identity for pid, identity in self._owned_winws.items()
                if before.get(pid) == identity
            }
            self.ownership = (
                ComponentOwnership.SUITE if self._owned_winws
                else ComponentOwnership.EXTERNAL
            )
            self.observe(ComponentState.RUNNING)
            logger.info("Zapret already running (winws.exe found)")
            return True

        zapret_dir = self.config.zapret_dir
        if not zapret_dir:
            logger.warning("Zapret directory not found — skipping")
            self.set_state(ComponentState.ERROR)
            return False

        strategy = self.config.zapret_strategy
        bat_path = os.path.join(zapret_dir, strategy)
        if not os.path.exists(bat_path):
            logger.error(f"Zapret strategy not found: {bat_path}")
            self.set_state(ComponentState.ERROR)
            return False

        self.set_state(ComponentState.STARTING)
        logger.info(f"Starting Zapret with strategy: {strategy}")
        try:
            launch_started = time.time() - 1.0
            self._process = subprocess.Popen(
                ["cmd.exe", "/c", bat_path],
                cwd=zapret_dir,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(2)
            after = self._winws_processes()
            try:
                root = os.path.normcase(os.path.abspath(zapret_dir))
                launched = {
                    pid: identity for pid, identity in (after or {}).items()
                    if pid not in before
                    and identity[0] >= launch_started
                    and os.path.commonpath([root, identity[1]]) == root
                }
            except (OSError, ValueError):
                launched = {}
            if launched:
                self._owned_winws = launched
                self.ownership = ComponentOwnership.SUITE
                self.observe(ComponentState.RUNNING)
                logger.info("Zapret started successfully")
                return True
            if after:
                self.ownership = ComponentOwnership.UNKNOWN
                self.observe(ComponentState.DEGRADED)
                logger.error("winws.exe is active but could not be attributed safely to this Suite start")
                return False
            self.ownership = ComponentOwnership.UNKNOWN
            self.observe(ComponentState.ERROR)
            logger.error("Zapret failed to start (winws.exe not found after launch)")
            return False
        except Exception as e:
            self.ownership = ComponentOwnership.UNKNOWN
            self.set_state(ComponentState.ERROR)
            logger.error(f"Zapret start failed: {e}")
            return False

    def stop(self):
        """Stop zapret."""
        self.request_enabled(False)
        if self.ownership != ComponentOwnership.SUITE or not self._owned_winws:
            logger.info("Leaving non-Suite-owned Zapret process untouched")
            return True
        current = self._winws_processes()
        if current is None:
            self.ownership = ComponentOwnership.UNKNOWN
            logger.warning("Could not verify Zapret process identities; refusing to stop winws.exe")
            return False
        try:
            import psutil
            for pid, (created, executable) in tuple(self._owned_winws.items()):
                if current.get(pid) != (created, executable):
                    continue
                proc = psutil.Process(pid)
                if (float(proc.create_time()), os.path.normcase(os.path.abspath(proc.exe()))) != (created, executable):
                    continue
                proc.terminate()
                proc.wait(timeout=5)
                logger.info("Stopped Suite-owned winws.exe (PID %s)", pid)
            self._owned_winws.clear()
            remaining = self._winws_processes()
            if remaining is None:
                self.ownership = ComponentOwnership.UNKNOWN
                self.observe(ComponentState.DEGRADED)
            elif remaining:
                self.ownership = ComponentOwnership.EXTERNAL
                self.observe(ComponentState.RUNNING)
            else:
                self.ownership = ComponentOwnership.UNKNOWN
                self.observe(ComponentState.STOPPED)
            return True
        except Exception as e:
            self.ownership = ComponentOwnership.UNKNOWN
            logger.warning("Failed to stop Suite-owned Zapret process safely: %s", e)
            return False


# ─── Happ VPN Component ───────────────────────────────────────

class HappVPNComponent(Component):
    """Manages Happ.exe → happd.exe → xray.exe VPN tunnel."""

    def __init__(self, config: Config):
        super().__init__("Happ VPN")
        self.config = config
        self.controller = HappController(config.happ_exe, config.tunnel_wait_timeout)
        self.cancel_requested = threading.Event()

    def is_running(self) -> bool:
        """Require a HAPP-owned system route and a direct external response."""
        return self.controller.read_status().connected

    def is_happ_process_alive(self) -> bool:
        """Check if Happ.exe process exists."""
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                if proc.info["name"] and proc.info["name"].lower() == "happ.exe":
                    return True
        except Exception:
            pass
        return False

    def can_recover(self) -> bool:
        """Recovery belongs to the independent bridge, not this monitor."""
        return False

    def _write_server_to_registry(self, server: Dict):
        """Refuse undocumented registry control rather than changing user settings."""
        logger.error("HAPP server selection through registry is undocumented; preferences were not changed")
        return False

    def get_status(self) -> Dict[str, object]:
        """Report route and external reachability as separate observations."""
        observed = self.controller.read_status()
        return {
            "state": self.state.value,
            "happ_process_alive": observed.gui_pid is not None,
            "happ_pid": observed.gui_pid,
            "proxy_responsive": is_port_open("127.0.0.1", self.config.proxy_port),
            "tunnel_verified": observed.connected,
            "route_interface": observed.route.interface_alias,
            "external_ok": observed.external_ok,
            "active_profile_id": observed.active_profile_id,
        }

    def get_servers(self) -> List[Dict]:
        return self.controller.list_profiles()

    def start(self, server: Optional[Dict] = None) -> bool:
        """Connect the profile already selected in HAPP through its existing GUI."""
        self.request_enabled(True)
        if server is not None:
            logger.error("HAPP profile selection is not verified; static config IDs are ignored")
            self.set_state(ComponentState.ERROR)
            return False
        if self.cancel_requested.is_set():
            self.set_state(ComponentState.STOPPED)
            return False
        self.set_state(ComponentState.STARTING)
        try:
            observed = self.controller.read_status()
            if observed.connected:
                if self.ownership == ComponentOwnership.UNKNOWN:
                    self.ownership = ComponentOwnership.EXTERNAL
                self.observe(ComponentState.RUNNING)
                return True
            if observed.route.through_happ:
                if self.ownership == ComponentOwnership.UNKNOWN:
                    self.ownership = ComponentOwnership.EXTERNAL
                self.observe(ComponentState.DEGRADED)
                logger.warning("HAPP route is active but unverified; leaving the existing session untouched")
                return False
            if observed.gui_pid is None:
                self.ownership = ComponentOwnership.UNKNOWN
                logger.error("Existing Happ.exe GUI IPC is unavailable; no second GUI is started")
                self.set_state(ComponentState.ERROR)
                return False
            connected = self.controller.connect_current_profile()
            if self.cancel_requested.is_set():
                if connected:
                    self.ownership = ComponentOwnership.SUITE
                    self.controller.disconnect()
                    self.ownership = ComponentOwnership.UNKNOWN
                    self.observe(ComponentState.STOPPED)
                else:
                    self.ownership = ComponentOwnership.UNKNOWN
                    self.observe(ComponentState.ERROR)
                return False
            self.ownership = ComponentOwnership.SUITE if connected else ComponentOwnership.UNKNOWN
            self.set_state(ComponentState.RUNNING if connected else ComponentState.ERROR)
            if not connected:
                logger.error("HAPP accepted connect but TUN route and HTTPS were not verified")
            return connected
        except Exception:
            logger.exception("HAPP connection through existing GUI failed")
            self.set_state(ComponentState.ERROR)
            return False

    def stop(self) -> bool:
        return self.stop_owned()

    def disconnect(self) -> bool:
        """Disconnect HAPP's tunnel without closing the existing GUI process."""
        self.request_enabled(False)
        if self.ownership != ComponentOwnership.SUITE:
            logger.info("Leaving non-Suite-owned HAPP tunnel untouched")
            self.set_state(ComponentState.ERROR)
            return False
        self.set_state(ComponentState.STOPPING)
        try:
            disconnected = self.controller.disconnect()
            if disconnected:
                self.ownership = ComponentOwnership.UNKNOWN
            self.set_state(ComponentState.STOPPED if disconnected else ComponentState.ERROR)
            if not disconnected:
                logger.error("HAPP accepted disconnect but the TUN route remained active")
            return disconnected
        except Exception:
            logger.exception("HAPP disconnect through existing GUI failed")
            self.set_state(ComponentState.ERROR)
            return False

    def stop_owned(self) -> bool:
        """Stop a HAPP session only when this Suite instance started it."""
        self.request_enabled(False)
        if self.ownership != ComponentOwnership.SUITE:
            logger.info("HAPP stop requested, but Suite does not own the active session")
            return True
        return self.disconnect()

    def switch_server(self, server: Dict) -> bool:
        """Selection requires a validated live HAPP catalog and command."""
        return self.start(server)

def _post_close_to_pid(target_pid: int):
    """Request a normal close from top-level windows owned by one PID."""
    user32 = ctypes.windll.user32
    get_pid = user32.GetWindowThreadProcessId
    post_message = user32.PostMessageW
    enum_windows = user32.EnumWindows
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    wm_close = 0x0010

    def visit(hwnd, _):
        pid = ctypes.wintypes.DWORD()
        get_pid(hwnd, ctypes.byref(pid))
        if pid.value == target_pid:
            post_message(hwnd, wm_close, 0, 0)
        return True

    callback = callback_type(visit)
    enum_windows(callback, 0)


# ─── AG Unlocker Component ────────────────────────────────────

class AGUnlockerComponent(Component):
    """Manages ag_dns.exe (Gemini DNS proxy for Russian accounts)."""

    def __init__(self, config: Config):
        super().__init__("AG Unlocker")
        self.config = config
        self.activation_unix: float = 0.0
        self.started_task_this_session = False

    @property
    def requested_enabled(self) -> bool:
        """Compatibility view used by the existing UI."""
        return self.desired_state == DesiredState.ON

    @requested_enabled.setter
    def requested_enabled(self, enabled: bool):
        self.request_enabled(bool(enabled))

    def is_running(self) -> bool:
        """Require both the AG unlocker process and its local listener."""
        from_process = False
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                if proc.info["name"] and proc.info["name"].lower() == "ag_dns.exe":
                    from_process = any(
                        conn.status == psutil.CONN_LISTEN
                        for conn in proc.net_connections(kind="tcp")
                    )
        except Exception:
            logger.debug("Could not inspect AG Unlocker listener through process metadata")
        return from_process or is_port_open(
            "127.0.0.1", self.config.ag_unlocker_port, timeout_ms=600
        )

    def start(self) -> bool:
        """Ensure the installer's headless Task Scheduler relay is available."""
        self.request_enabled(True)
        self.activation_unix = time.time()
        result = ensure_dns_relay()
        self.started_task_this_session = bool(result.started_by_suite)
        self.ownership = (
            ComponentOwnership.SUITE if result.started_by_suite else
            ComponentOwnership.EXTERNAL if result.ready else
            ComponentOwnership.UNKNOWN
        )
        if not result.ready:
            logger.error("AG Unlocker DNS relay unavailable: %s", result.reason)
            self.observe(ComponentState.ERROR)
            return False
        self.observe(ComponentState.DEGRADED)
        return True

    def model_verified(self) -> bool:
        if not self.activation_unix:
            return False
        return read_gate_readiness(self.activation_unix).ready

    def stop(self):
        """Stop only the installed relay task; F9 can restart it later."""
        self.request_enabled(False)
        if self.ownership != ComponentOwnership.SUITE or not self.started_task_this_session:
            logger.info("AG Unlocker stop requested, but Suite does not own the relay")
            return True
        self.set_state(ComponentState.STOPPING)
        result = stop_dns_relay()
        if result.ready:
            self.activation_unix = 0.0
            self.started_task_this_session = False
            self.ownership = ComponentOwnership.UNKNOWN
            self.set_state(ComponentState.STOPPED)
            return True
        logger.error("AG Unlocker relay could not stop: %s", result.reason)
        self.set_state(ComponentState.ERROR)
        return False


# ─── Orchestrator ──────────────────────────────────────────────

class Orchestrator:
    """
    Central orchestrator that manages all bypass components.
    
    Usage:
        config = Config()
        orch = Orchestrator(config)
        orch.start_all()
        # ... later ...
        orch.stop_all()
    """

    def __init__(self, config: Config):
        self.config = config
        self.zapret = ZapretComponent(config)
        if getattr(config, "vpn_backend", "mihomo").casefold() == "happ":
            self.vpn = HappVPNComponent(config)
        else:
            try:
                from .mihomo_backend import MihomoVPNComponent
            except ImportError:
                from mihomo_backend import MihomoVPNComponent
            self.vpn = MihomoVPNComponent(config)
        # Compatibility alias used by the existing tray and dashboard.
        self.happ = self.vpn
        self.ag_unlocker = AGUnlockerComponent(config)

        self._components: List[Component] = [self.zapret, self.happ, self.ag_unlocker]
        self._on_state_change: Optional[Callable] = None

    @property
    def desired_enabled(self) -> bool:
        """Compatibility view: the legacy global toggle represents HAPP intent."""
        return self.happ.desired_state == DesiredState.ON

    @desired_enabled.setter
    def desired_enabled(self, enabled: bool):
        self.happ.request_enabled(bool(enabled))

    def on_state_change(self, callback: Callable):
        """Register a callback for component state changes."""
        self._on_state_change = callback
        for comp in self._components:
            comp.on_state_change(callback)

    @property
    def overall_state(self) -> ComponentState:
        """Get the overall state of the suite."""
        states = [self.happ.state, self.ag_unlocker.state]
        if any(s == ComponentState.ERROR for s in states):
            return ComponentState.ERROR
        if any(s in (ComponentState.STARTING, ComponentState.STOPPING, ComponentState.RECOVERING) for s in states):
            return ComponentState.STARTING
        if not self.desired_enabled and self.happ.state == ComponentState.STOPPED:
            return ComponentState.STOPPED
        if self.happ.state == ComponentState.RUNNING and self.ag_unlocker.state == ComponentState.RUNNING:
            return ComponentState.RUNNING
        if any(s == ComponentState.DEGRADED for s in states):
            return ComponentState.DEGRADED
        return ComponentState.STOPPED if not self.desired_enabled else ComponentState.DEGRADED

    def start_all(self, target_server: Optional[Dict] = None) -> bool:
        """Start the requested services; full READY is a separate health state."""
        logger.info("=== Starting all components ===")
        self.happ.cancel_requested.clear()
        self.desired_enabled = True

        happ_ok = self.happ.start(server=target_server)
        unlocker_ok = self.ag_unlocker.start() if happ_ok else False
        if happ_ok and unlocker_ok:
            model_result = probe_model_response()
            if model_result.success:
                self.ag_unlocker.set_state(ComponentState.RUNNING)
            else:
                logger.error("Antigravity model response was not confirmed: %s", model_result.reason)
                self.ag_unlocker.set_state(ComponentState.ERROR)
                unlocker_ok = False

        if isinstance(self.happ, HappVPNComponent):
            happ_status = self.happ.get_status()
            route_name = happ_status["route_interface"]
            verified = happ_status["tunnel_verified"]
        else:
            observed = self.happ.read_status()
            route_name = observed.route.interface_alias
            verified = observed.connected
        logger.info(
            "=== Start complete. VPN route: %s, tunnel verified: %s, AG Unlocker: %s ===",
            route_name,
            verified,
            "RESPONSIVE" if unlocker_ok else "UNAVAILABLE",
        )
        return unlocker_ok and happ_ok

    def stop_all(self) -> bool:
        """Disconnect HAPP and reset only Suite-owned state."""
        logger.info("=== Stopping all components ===")
        self.happ.cancel_requested.set()
        disconnected = self.happ.stop_owned()
        self.happ.request_enabled(False)
        logger.info("=== VPN disconnected: %s ===", disconnected)
        return disconnected

    def cancel_start(self):
        """Cancel a pending startup without touching components owned by others."""
        self.desired_enabled = False
        self.happ.cancel_requested.set()

    # ── Independent per-component toggles ────────────────────────────────────

    def toggle_happ(self) -> bool:
        """Toggle only the Happ VPN tunnel without touching AG Unlocker."""
        controller = self.happ if hasattr(self.happ, "read_status") else self.happ.controller
        observed = controller.read_status(with_external_probe=False)
        if observed.route.through_happ:
            if self.happ.ownership == ComponentOwnership.UNKNOWN:
                self.happ.ownership = ComponentOwnership.EXTERNAL
            return self._stop_happ_only()
        else:
            return self._start_happ_only()

    def _start_happ_only(self) -> bool:
        """Connect Happ VPN without starting AG Unlocker."""
        logger.info("=== Starting Happ VPN only ===")
        self.happ.cancel_requested.clear()
        self.desired_enabled = True
        ok = self.happ.start()
        logger.info("=== Happ VPN start: %s ===", "OK" if ok else "FAILED")
        return ok

    def _stop_happ_only(self) -> bool:
        """Disconnect Happ VPN without stopping AG Unlocker."""
        logger.info("=== Stopping Happ VPN only ===")
        self.happ.cancel_requested.set()
        disconnected = self.happ.stop_owned()
        self.happ.request_enabled(False)
        logger.info("=== Happ VPN stop: %s ===", "OK" if disconnected else "FAILED")
        return disconnected

    def toggle_ag_unlocker(self) -> bool:
        """Toggle only AG Unlocker without touching Happ VPN."""
        if (
            self.ag_unlocker.observed_state in (ComponentState.RUNNING, ComponentState.DEGRADED)
            or (self.ag_unlocker.ownership == ComponentOwnership.SUITE
                and self.ag_unlocker.started_task_this_session)
        ):
            return self._stop_ag_only()
        else:
            return self._start_ag_only()

    def _start_ag_only(self) -> bool:
        """Start AG Unlocker relay without requiring Happ VPN."""
        logger.info("=== Starting AG Unlocker only ===")
        ok = self.ag_unlocker.start()
        # F9 controls the relay only. The model probe can take tens of seconds
        # and has failed on account-region restrictions; don't block a toggle
        # waiting for it. Health monitoring will mark Gemini ready only when a
        # fresh successful model response appears in the relay's gate record.
        logger.info("=== AG Unlocker start: %s ===", "OK" if ok else "FAILED")
        return ok

    def _stop_ag_only(self) -> bool:
        """Stop AG Unlocker relay without touching Happ VPN."""
        logger.info("=== Stopping AG Unlocker only ===")
        result = self.ag_unlocker.stop()
        logger.info("=== AG Unlocker stop: %s ===", "OK" if result else "FAILED")
        return result

    def restart_vpn(self, server: Optional[Dict] = None) -> bool:
        """Recovery must use the independent bridge with a restoration record."""
        logger.error("In-process VPN restart is disabled; use the recovery bridge")
        return False

    def get_status(self) -> Dict[str, str]:
        """Get status of all components."""
        return {comp.name: comp.state.value for comp in self._components}

    def refresh_status(self):
        """Read current service state without starting or stopping anything."""
        controller = self.happ if hasattr(self.happ, "read_status") else self.happ.controller
        observed = controller.read_status()
        if observed.route.through_happ and self.happ.ownership == ComponentOwnership.UNKNOWN:
            self.happ.ownership = ComponentOwnership.EXTERNAL
        self.happ.observe(
            ComponentState.RUNNING if observed.connected else
            ComponentState.DEGRADED if observed.route.through_happ else
            ComponentState.STOPPED,
            preserve_transition=True,
        )

        unlocker_up = self.ag_unlocker.is_running()
        if unlocker_up and self.ag_unlocker.ownership == ComponentOwnership.UNKNOWN:
            self.ag_unlocker.ownership = ComponentOwnership.EXTERNAL
        if not unlocker_up:
            self.ag_unlocker.ownership = ComponentOwnership.UNKNOWN
        self.ag_unlocker.observe(
            ComponentState.DEGRADED if unlocker_up else ComponentState.STOPPED,
            preserve_transition=True,
        )

        zapret_processes = self.zapret._winws_processes()
        if zapret_processes:
            if self.zapret.ownership == ComponentOwnership.UNKNOWN:
                self.zapret.ownership = ComponentOwnership.EXTERNAL
            self.zapret.observe(ComponentState.RUNNING, preserve_transition=True)
        elif zapret_processes == {}:
            self.zapret.ownership = ComponentOwnership.UNKNOWN
            self.zapret._owned_winws.clear()
            self.zapret.observe(ComponentState.STOPPED, preserve_transition=True)
        else:
            self.zapret.ownership = ComponentOwnership.UNKNOWN
            self.zapret.observe(ComponentState.DEGRADED, preserve_transition=True)
