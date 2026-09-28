"""Conservative control and independent observation of the installed HAPP GUI.

The GUI IPC acknowledgement means that a command was accepted for delivery.
Only route and external HTTPS checks can mark a VPN connection as verified.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

try:
    from .happ_ipc import HappIpcClient
    from .happ_status import TunnelRoute, best_route_to
except ImportError:
    from happ_ipc import HappIpcClient
    from happ_status import TunnelRoute, best_route_to


logger = logging.getLogger("happ_suite.happ_controller")
_PROBE_URL = "https://www.google.com/generate_204"


@dataclass(frozen=True)
class HappStatus:
    route: TunnelRoute
    external_ok: bool
    gui_pid: int | None
    # This must come from live HAPP state, not the lastServer preference.
    active_profile_id: str | None = None

    @property
    def connected(self) -> bool:
        return self.route.through_happ and self.external_ok


def external_https_ok(timeout_s: float = 3.0) -> bool:
    """Probe the real system route, ignoring HTTP proxy environment settings."""
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(_PROBE_URL, timeout=timeout_s, allow_redirects=False)
            return response.status_code == 204
    except requests.RequestException:
        return False


class HappController:
    def __init__(self, happ_exe: str, timeout_s: float = 45.0, ipc: HappIpcClient | None = None):
        self.ipc = ipc or HappIpcClient(happ_exe)
        self.timeout_s = timeout_s

    def read_status(self, *, with_external_probe: bool = True) -> HappStatus:
        route = best_route_to()
        try:
            gui_pid = self.ipc.probe()
        except Exception as exc:
            logger.debug("HAPP GUI pipe unavailable: %s", exc)
            gui_pid = None
        external_ok = external_https_ok() if (route.through_happ and with_external_probe) else False
        return HappStatus(route, external_ok, gui_pid)

    def list_profiles(self) -> list[dict]:
        """No verified read-only catalog endpoint has been found yet."""
        return []

    def connect_current_profile(self) -> bool:
        """Request HAPP's own currently selected profile; wait for TUN and HTTPS."""
        if self.read_status().connected:
            return True
        self.ipc.send_deeplink("connect")
        return self._await_route(True)

    def disconnect(self) -> bool:
        """Request HAPP to drop its TUN; leave HAPP and unrelated services alive."""
        if not best_route_to().through_happ:
            return True
        self.ipc.send_deeplink("disconnect")
        return self._await_route(False)

    def _await_route(self, should_connect: bool) -> bool:
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            route_matches = best_route_to().through_happ == should_connect
            if route_matches and (not should_connect or external_https_ok()):
                return True
            time.sleep(0.5)
        return False
