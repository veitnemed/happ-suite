"""
Health monitor — periodic checks for VPN tunnel, service reachability, 
and auto-recovery after sleep/resume.
"""
import logging
import threading
import time
from typing import Callable, Dict, List, Optional

import requests

try:
    from .app_config import Config
    from .core import Orchestrator, ComponentState, ComponentOwnership, is_port_open
    from .mihomo_backend import MihomoVPNComponent
except (ImportError, ValueError):
    from app_config import Config
    from core import Orchestrator, ComponentState, ComponentOwnership, is_port_open
    from mihomo_backend import MihomoVPNComponent

logger = logging.getLogger("happ_suite.health")


class HealthStatus:
    """Snapshot of system health."""

    def __init__(self):
        self.tunnel_up: bool = False
        self.services: Dict[str, bool] = {}
        self.ip_info: Optional[Dict] = None
        self.white_lists_active: bool = False
        self.current_server: str = "Не определен"

    @property
    def is_healthy(self) -> bool:
        return self.tunnel_up

    @property
    def external_ip(self) -> str:
        if self.ip_info:
            return self.ip_info.get("query", "?")
        return "?"

    @property
    def country_code(self) -> str:
        if self.ip_info:
            return self.ip_info.get("countryCode", "??")
        return "??"


def check_tunnel(config: Config) -> bool:
    """Check the selected Mihomo tunnel without starting another process."""
    return MihomoVPNComponent(config).read_status().connected


def check_service(url: str, proxy_url: str, timeout: int = 5) -> bool:
    """Check if a web service is reachable through the proxy."""
    try:
        resp = requests.get(
            url,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=timeout,
            allow_redirects=True,
        )
        return resp.status_code < 500
    except Exception:
        return False


def get_ip_info(proxy_url: str) -> Optional[Dict]:
    """Get external IP info through the proxy."""
    try:
        resp = requests.get(
            "http://ip-api.com/json",
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=5,
        )
        return resp.json()
    except Exception:
        return None


def full_health_check(config: Config) -> HealthStatus:
    """Run a comprehensive health check."""
    status = HealthStatus()

    # 1. Tunnel
    status.tunnel_up = check_tunnel(config)
    if not status.tunnel_up:
        return status

    # 2. Services
    for name, url in config.check_urls.items():
        status.services[name] = check_service(url, config.proxy_url)

    # 3. IP info
    status.ip_info = get_ip_info(config.proxy_url)

    return status


class HealthMonitor:
    """
    Background thread that periodically checks health and triggers recovery.
    """

    def __init__(self, orchestrator: Orchestrator, config: Config):
        self.orchestrator = orchestrator
        self.config = config
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._on_health_update: Optional[Callable] = None
        self._consecutive_fails = 0
        self.last_status: Optional[HealthStatus] = None

    def on_health_update(self, callback: Callable):
        """Register callback for health updates: callback(HealthStatus)."""
        self._on_health_update = callback

    def start(self):
        """Start the health monitor background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="HealthMonitor")
        self._thread.start()
        logger.info("Health monitor started")

    def stop(self):
        """Stop the health monitor."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Health monitor stopped")

    def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                self._check_once()
            except Exception as e:
                logger.error(f"Health check error: {e}")

            # Sleep in small increments so we can stop quickly
            for _ in range(self.config.health_check_interval):
                if not self._running:
                    return
                time.sleep(1)

    def _check_once(self):
        """Run a single health check cycle."""
        vpn = self.orchestrator.vpn
        vpn_status = vpn.read_status()
        tunnel_up = vpn_status.connected
        unlocker_up = self.orchestrator.ag_unlocker.is_running()
        if vpn_status.route.through_happ and vpn.ownership == ComponentOwnership.UNKNOWN:
            vpn.ownership = ComponentOwnership.EXTERNAL
        vpn.observe(
            ComponentState.RUNNING if tunnel_up else
            ComponentState.DEGRADED if vpn_status.route.through_happ else
            ComponentState.STOPPED,
            preserve_transition=True,
        )

        unlocker = self.orchestrator.ag_unlocker
        if unlocker_up and unlocker.ownership == ComponentOwnership.UNKNOWN:
            unlocker.ownership = ComponentOwnership.EXTERNAL
        elif not unlocker_up:
            unlocker.ownership = ComponentOwnership.UNKNOWN
        if unlocker_up and unlocker.model_verified():
            unlocker.observe(ComponentState.RUNNING, preserve_transition=True)
        elif not unlocker_up:
            unlocker.observe(ComponentState.STOPPED, preserve_transition=True)
        else:
            unlocker.observe(ComponentState.DEGRADED, preserve_transition=True)

        if tunnel_up:
            self._consecutive_fails = 0
            status = HealthStatus()
            status.tunnel_up = True

            status.current_server = getattr(vpn, "_last_good_node", None) or "Не определён"

            self.last_status = status
            if self._on_health_update:
                self._on_health_update(status)
        else:
            if getattr(self.orchestrator, "desired_enabled", False):
                self._consecutive_fails += 1
                logger.warning(f"Tunnel DOWN (consecutive fails: {self._consecutive_fails})")

            status = HealthStatus()
            status.tunnel_up = False
            self.last_status = status
            if self._on_health_update:
                self._on_health_update(status)
