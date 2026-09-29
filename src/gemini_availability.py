"""Independent, privacy-conscious diagnostics for Gemini Web reachability."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import ipaddress
import json
from pathlib import Path
import re
from typing import Callable

import requests


class RegionSupport(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class GeminiNetworkStatus:
    network_available: bool | None = None
    website_reachable: bool | None = None
    dns_managed: bool | None = None
    exit_country: str | None = None
    external_ip: str | None = None
    region_supported: RegionSupport = RegionSupport.UNKNOWN
    account_status: str = "UNKNOWN"
    website_http_status: int | None = None


def load_region_snapshot(path: Path | None = None) -> dict:
    """Load the versioned snapshot derived from Google's Gemini Web help page."""
    snapshot_path = path or Path(__file__).with_name("data") / "gemini_web_regions.json"
    with snapshot_path.open("r", encoding="utf-8") as stream:
        snapshot = json.load(stream)
    if not snapshot.get("source") or not snapshot.get("checked_at"):
        raise ValueError("Gemini region snapshot is missing source metadata")
    return snapshot


def classify_region(country_code: str | None, snapshot: dict | None = None) -> RegionSupport:
    if not country_code or not re.fullmatch(r"[A-Za-z]{2}", country_code):
        return RegionSupport.UNKNOWN
    code = country_code.upper()
    data = snapshot if snapshot is not None else load_region_snapshot()
    if code in set(data.get("supported_country_codes", ())):
        return RegionSupport.SUPPORTED
    if code in set(data.get("known_country_codes", ())):
        return RegionSupport.UNSUPPORTED
    return RegionSupport.UNKNOWN


class GeminiAvailabilityClient:
    """Probe transport, Gemini Web, Suite-managed DNS and egress separately.

    A reachable Gemini page never changes account_status: account eligibility
    requires authenticated evidence, which this client intentionally does not
    collect.
    """

    NETWORK_URL = "https://www.google.com/generate_204"
    WEBSITE_URL = "https://gemini.google.com/"
    EGRESS_URL = "https://www.cloudflare.com/cdn-cgi/trace"

    def __init__(self, *, session_factory=requests.Session, dns_status: Callable | None = None,
                 region_snapshot: dict | None = None, timeout: tuple[int, int] = (3, 6)):
        self._session_factory = session_factory
        self._dns_status = dns_status
        self._region_snapshot = region_snapshot
        self._timeout = timeout

    def _get(self, url: str):
        with self._session_factory() as session:
            session.trust_env = False
            return session.get(url, timeout=self._timeout)

    def _probe_reachable(self, url: str) -> bool:
        try:
            self._get(url)
            return True
        except requests.RequestException:
            return False

    def _probe_website(self) -> tuple[bool, int | None]:
        try:
            response = self._get(self.WEBSITE_URL)
            return True, int(response.status_code)
        except requests.RequestException:
            return False, None

    def _probe_exit(self) -> tuple[str | None, str | None]:
        try:
            response = self._get(self.EGRESS_URL)
            if not 200 <= response.status_code < 300:
                return None, None
            country_match = re.search(r"(?m)^loc=([A-Z]{2})\s*$", response.text)
            ip_match = re.search(r"(?m)^ip=([^\s]+)\s*$", response.text)
            try:
                address = str(ipaddress.ip_address(ip_match.group(1))) if ip_match else None
            except ValueError:
                address = None
            return country_match.group(1) if country_match else None, address
        except requests.RequestException:
            return None, None

    def _probe_dns_managed(self) -> bool | None:
        if self._dns_status is None:
            return None
        try:
            status = self._dns_status()
            return bool(status.get("managed")) if isinstance(status, dict) else None
        except Exception:
            return None

    def check(self) -> GeminiNetworkStatus:
        network = self._probe_reachable(self.NETWORK_URL)
        website, http_status = self._probe_website()
        country, external_ip = self._probe_exit()
        return GeminiNetworkStatus(
            network_available=network,
            website_reachable=website,
            dns_managed=self._probe_dns_managed(),
            exit_country=country,
            external_ip=external_ip,
            region_supported=classify_region(country, self._region_snapshot),
            account_status="UNKNOWN",
            website_http_status=http_status,
        )
