"""
Server picker — pings all VPN servers and selects the best one.
Implements the white-list detection logic:
  - If ALL foreign servers are unreachable (n/a) → white lists active → pick domestic
  - Otherwise → pick the foreign server with the lowest ping
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .core import tcp_ping
from .config import Config

logger = logging.getLogger("happ_suite.server_picker")


@dataclass
class PingResult:
    server: Dict
    ping_ms: Optional[float]  # None = unreachable / timeout

    @property
    def is_alive(self) -> bool:
        return self.ping_ms is not None

    @property
    def is_foreign(self) -> bool:
        return self.server.get("category") == "foreign"

    @property
    def is_domestic(self) -> bool:
        return self.server.get("category") == "domestic"


def ping_all_servers(config: Config) -> List[PingResult]:
    """Ping all configured servers and return results."""
    results = []
    for srv in config.servers:
        ms = tcp_ping(srv["host"], srv["port"], timeout_ms=1500)
        results.append(PingResult(server=srv, ping_ms=ms))
        status = f"{ms:.1f}ms" if ms is not None else "n/a"
        logger.debug(f"  {srv['label']}: {status}")
    return results


def detect_white_lists(results: List[PingResult]) -> bool:
    """
    Detect if white lists (БПЛА jammers) are active.
    White lists = ALL foreign VPN servers are unreachable.
    """
    foreign = [r for r in results if r.is_foreign]
    if not foreign:
        return False  # No foreign servers configured
    alive_foreign = [r for r in foreign if r.is_alive]
    is_active = len(alive_foreign) == 0
    if is_active:
        logger.warning("⚠ WHITE LISTS DETECTED: All foreign VPN servers unreachable!")
    return is_active


def pick_best_server(results: List[PingResult]) -> Optional[Dict]:
    """
    Pick the optimal server based on ping results.
    
    Priority rules (from user requirements):
    1. Foreign servers ALWAYS have priority when available
    2. Domestic (Russia/Антизаглушка) ONLY when ALL foreign are n/a
    3. Among available servers, pick the one with lowest ping
    """
    white_lists = detect_white_lists(results)

    if white_lists:
        # All foreign down → use domestic (антизаглушка)
        domestic = [r for r in results if r.is_domestic]
        if domestic:
            best = domestic[0]  # Usually just one domestic server
            logger.info(f"→ White lists active. Selected domestic: {best.server['label']}")
            return best.server
        logger.error("White lists active but no domestic server configured!")
        return None
    else:
        # Foreign available → pick best ping
        alive_foreign = sorted(
            [r for r in results if r.is_foreign and r.is_alive],
            key=lambda r: r.ping_ms,
        )
        if alive_foreign:
            best = alive_foreign[0]
            logger.info(f"→ Best foreign server: {best.server['label']} ({best.ping_ms:.1f}ms)")
            return best.server
        logger.warning("No alive servers found at all!")
        return None


def get_current_server_id(config: Config) -> Optional[int]:
    """Read the current server ID from Windows registry."""
    from .core import _reg_get_value
    return _reg_get_value(config.registry_pref, "lastServer")


def should_switch(config: Config, target_server: Dict) -> bool:
    """Check if we need to switch servers."""
    current_id = get_current_server_id(config)
    target_id = target_server["id"] & 0xFFFFFFFF
    if current_id is None:
        return True
    return current_id != target_id
