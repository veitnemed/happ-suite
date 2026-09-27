"""
Core orchestrator — manages lifecycle of all bypass components.

Components:
  1. Zapret (winws.exe) — DPI bypass for YouTube/Discord
  2. Happ VPN (Happ.exe → happd.exe → xray.exe) — VPN tunnel
  3. AG Unlocker (ag_dns.exe) — Gemini DNS proxy

Design:
  - No UI flickering: Happ.exe is launched normally, then its window is hidden
    via Win32 ShowWindow after the tunnel establishes.
  - Process health is monitored via TCP port checks, not process polling.
  - Components can be started/stopped independently or all at once.
"""
import ctypes
import ctypes.wintypes
import logging
import os
import socket
import subprocess
import time
import winreg
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from .config import Config

logger = logging.getLogger("happ_suite.core")


class ComponentState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"
    RECOVERING = "recovering"


class Component:
    """Base class for a managed bypass component."""

    def __init__(self, name: str):
        self.name = name
        self.state = ComponentState.STOPPED
        self._on_state_change: Optional[Callable] = None

    def set_state(self, new_state: ComponentState):
        old = self.state
        self.state = new_state
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

def _find_and_hide_window(process_name: str = "Happ"):
    """Find a window by process name and hide it with ShowWindow(hWnd, 0)."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        EnumWindows = user32.EnumWindows
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        IsWindowVisible = user32.IsWindowVisible
        ShowWindow = user32.ShowWindow

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        # Find PIDs of the target process
        import psutil
        target_pids = set()
        for proc in psutil.process_iter(["pid", "name"]):
            if proc.info["name"] and proc.info["name"].lower().startswith(process_name.lower()):
                target_pids.add(proc.info["pid"])

        if not target_pids:
            return False

        hidden = False

        def enum_callback(hwnd, _lparam):
            nonlocal hidden
            if IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in target_pids:
                    ShowWindow(hwnd, 0)  # SW_HIDE
                    hidden = True
            return True

        EnumWindows(WNDENUMPROC(enum_callback), 0)
        return hidden
    except Exception as e:
        logger.warning(f"Failed to hide window for {process_name}: {e}")
        return False


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

    def is_running(self) -> bool:
        """Check if winws.exe is running (any instance)."""
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                if proc.info["name"] and proc.info["name"].lower() == "winws.exe":
                    return True
        except Exception:
            pass
        return False

    def start(self) -> bool:
        """Start zapret if not already running."""
        if self.is_running():
            self.set_state(ComponentState.RUNNING)
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
            self._process = subprocess.Popen(
                ["cmd.exe", "/c", bat_path],
                cwd=zapret_dir,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(2)
            if self.is_running():
                self.set_state(ComponentState.RUNNING)
                logger.info("Zapret started successfully")
                return True
            else:
                self.set_state(ComponentState.ERROR)
                logger.error("Zapret failed to start (winws.exe not found after launch)")
                return False
        except Exception as e:
            self.set_state(ComponentState.ERROR)
            logger.error(f"Zapret start failed: {e}")
            return False

    def stop(self):
        """Stop zapret."""
        try:
            import psutil
            for proc in psutil.process_iter(["name", "pid"]):
                if proc.info["name"] and proc.info["name"].lower() == "winws.exe":
                    proc.kill()
                    logger.info(f"Killed winws.exe (PID {proc.info['pid']})")
        except Exception as e:
            logger.warning(f"Failed to stop Zapret: {e}")
        self.set_state(ComponentState.STOPPED)


# ─── Happ VPN Component ───────────────────────────────────────

class HappVPNComponent(Component):
    """Manages Happ.exe → happd.exe → xray.exe VPN tunnel."""

    def __init__(self, config: Config):
        super().__init__("Happ VPN")
        self.config = config

    def is_running(self) -> bool:
        """Check if the VPN tunnel is up (proxy port open)."""
        return is_port_open("127.0.0.1", self.config.proxy_port, timeout_ms=600)

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

    def _write_server_to_registry(self, server: Dict):
        """Write the target server to Windows registry so Happ connects to it."""
        reg = self.config.registry_pref
        # Server ID may exceed Int32 — write as DWORD (unsigned 32-bit)
        server_id = server["id"] & 0xFFFFFFFF
        _reg_set_value(reg, "lastServer", server_id, winreg.REG_DWORD)
        _reg_set_value(reg, "lastServerName", server["name"])
        _reg_set_value(reg, "startminimized", "true")
        _reg_set_value(reg, "windowVisibility", 0, winreg.REG_DWORD)

        subs_path = reg + "\\Subscriptions"
        _reg_set_value(subs_path, "subsConnectOnOpen", "true")
        _reg_set_value(subs_path, "subsConnectTypeOnOpen", "lastused")
        _reg_set_value(subs_path, "subsPingOnOpen", "true")

    def start(self, server: Optional[Dict] = None) -> bool:
        """Start Happ VPN. If server is specified, switch to it first."""
        if self.is_running():
            self.set_state(ComponentState.RUNNING)
            logger.info("Happ VPN tunnel already up")
            return True

        self.set_state(ComponentState.STARTING)

        if server:
            self._write_server_to_registry(server)

        # Kill existing Happ.exe if present but not connected
        if self.is_happ_process_alive():
            logger.info("Killing stale Happ.exe...")
            self._kill_happ()
            time.sleep(0.5)

        # Launch Happ.exe normally (required for auto-connect)
        happ_exe = self.config.happ_exe
        if not os.path.exists(happ_exe):
            logger.error(f"Happ.exe not found: {happ_exe}")
            self.set_state(ComponentState.ERROR)
            return False

        logger.info(f"Launching Happ.exe --autostart")
        try:
            subprocess.Popen(
                [happ_exe, "--autostart"],
                creationflags=subprocess.DETACHED_PROCESS,
            )
        except Exception as e:
            logger.error(f"Failed to launch Happ.exe: {e}")
            self.set_state(ComponentState.ERROR)
            return False

        # Wait for tunnel to establish
        timeout = self.config.tunnel_wait_timeout
        logger.info(f"Waiting for tunnel (port {self.config.proxy_port})... timeout={timeout}s")
        for i in range(timeout):
            time.sleep(1)
            if self.is_running():
                # Hide the window after connection
                _find_and_hide_window("Happ")
                self.set_state(ComponentState.RUNNING)
                logger.info(f"Tunnel established in {i + 1}s")
                return True

        logger.warning(f"Tunnel timeout after {timeout}s")
        # Try hiding anyway
        _find_and_hide_window("Happ")
        self.set_state(ComponentState.ERROR)
        return False

    def stop(self):
        """Stop Happ VPN."""
        self._kill_happ()
        self.set_state(ComponentState.STOPPED)

    def switch_server(self, server: Dict) -> bool:
        """Switch to a different VPN server."""
        logger.info(f"Switching to server: {server['label']}")
        self._write_server_to_registry(server)
        self._kill_happ()
        time.sleep(0.5)
        return self.start(server)

    def _kill_happ(self):
        try:
            import psutil
            for proc in psutil.process_iter(["name", "pid"]):
                if proc.info["name"] and proc.info["name"].lower() == "happ.exe":
                    proc.kill()
                    logger.info(f"Killed Happ.exe (PID {proc.info['pid']})")
        except Exception as e:
            logger.warning(f"Failed to kill Happ.exe: {e}")


# ─── AG Unlocker Component ────────────────────────────────────

class AGUnlockerComponent(Component):
    """Manages ag_dns.exe (Gemini DNS proxy for Russian accounts)."""

    def __init__(self, config: Config):
        super().__init__("AG Unlocker")
        self.config = config

    def is_running(self) -> bool:
        """Check if ag_dns.exe is running."""
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                if proc.info["name"] and proc.info["name"].lower() == "ag_dns.exe":
                    return True
        except Exception:
            pass
        return False

    def start(self) -> bool:
        """AG Unlocker is typically managed by its own installer/service.
        We just detect if it's running."""
        if self.is_running():
            self.set_state(ComponentState.RUNNING)
            logger.info("AG Unlocker already running")
            return True

        ag_path = self.config.ag_dns_path
        if not ag_path or not os.path.exists(ag_path):
            logger.info("AG Unlocker not found — Gemini unblocking will not be available")
            self.set_state(ComponentState.STOPPED)
            return False

        # ag_dns is usually managed by the AGUnlocker application, we don't start it ourselves
        logger.info("AG Unlocker found but not running — it should be started via the Antigravity Unlocker app")
        self.set_state(ComponentState.STOPPED)
        return False

    def stop(self):
        """We don't stop ag_dns — it's managed externally."""
        self.set_state(ComponentState.STOPPED)


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
        self.happ = HappVPNComponent(config)
        self.ag_unlocker = AGUnlockerComponent(config)

        self._components: List[Component] = [self.zapret, self.happ, self.ag_unlocker]
        self._on_state_change: Optional[Callable] = None

    def on_state_change(self, callback: Callable):
        """Register a callback for component state changes."""
        self._on_state_change = callback
        for comp in self._components:
            comp.on_state_change(callback)

    @property
    def overall_state(self) -> ComponentState:
        """Get the overall state of the suite."""
        states = [c.state for c in self._components]

        if all(s == ComponentState.STOPPED for s in states):
            return ComponentState.STOPPED
        if any(s == ComponentState.ERROR for s in states):
            return ComponentState.ERROR
        if any(s == ComponentState.STARTING or s == ComponentState.RECOVERING for s in states):
            return ComponentState.STARTING
        if self.happ.state == ComponentState.RUNNING:
            return ComponentState.RUNNING
        return ComponentState.STARTING

    def start_all(self, target_server: Optional[Dict] = None) -> bool:
        """Start all components. Returns True if VPN tunnel is up."""
        logger.info("=== Starting all components ===")

        # 1. Zapret (DPI bypass) — first, so it's ready when VPN connects
        self.zapret.start()

        # 2. AG Unlocker — just detect
        self.ag_unlocker.start()

        # 3. Happ VPN — last, because it depends on network
        success = self.happ.start(server=target_server)

        logger.info(f"=== Start complete. VPN tunnel: {'UP' if success else 'DOWN'} ===")
        return success

    def stop_all(self):
        """Stop all components."""
        logger.info("=== Stopping all components ===")
        self.happ.stop()
        self.zapret.stop()
        # AG Unlocker is managed externally, we leave it
        logger.info("=== All components stopped ===")

    def restart_vpn(self, server: Optional[Dict] = None) -> bool:
        """Restart just the VPN component."""
        self.happ.stop()
        time.sleep(1)
        return self.happ.start(server=server)

    def get_status(self) -> Dict[str, str]:
        """Get status of all components."""
        return {comp.name: comp.state.value for comp in self._components}
