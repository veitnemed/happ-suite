"""Authenticated, bounded Mihomo REST controller client."""

from __future__ import annotations

from urllib.parse import quote, urlencode

import requests

from .vpn_backend import (
    ApiAuthError,
    ApiUnavailableError,
    ProviderFormatError,
    SubscriptionError,
)


class MihomoApi:
    def __init__(self, secret: str, *, host: str = "127.0.0.1", port: int = 19090,
                 session=None):
        self.base_url = f"http://{host}:{int(port)}"
        self._session = session or requests.Session()
        self._session.trust_env = False
        self._headers = {"Authorization": f"Bearer {secret}"}

    def _request(self, method: str, path: str, *, timeout: float, json_body=None):
        try:
            response = self._session.request(
                method, self.base_url + path, headers=self._headers,
                timeout=timeout, json=json_body,
            )
        except requests.RequestException as exc:
            raise ApiUnavailableError("Mihomo controller is unavailable") from exc
        if response.status_code in (401, 403):
            raise ApiAuthError("Mihomo rejected the controller secret")
        if response.status_code == 404:
            raise SubscriptionError("Mihomo provider or proxy group was not found")
        if response.status_code == 400:
            raise ProviderFormatError("Mihomo rejected the requested configuration or provider")
        if response.status_code >= 500:
            raise ApiUnavailableError(f"Mihomo controller returned HTTP {response.status_code}")
        if response.status_code >= 400:
            raise SubscriptionError(f"Mihomo controller returned HTTP {response.status_code}")
        return response

    def _json(self, method: str, path: str, *, timeout: float):
        response = self._request(method, path, timeout=timeout)
        try:
            value = response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise ProviderFormatError("Mihomo returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ProviderFormatError("Mihomo returned an unexpected response")
        return value

    def version(self) -> dict:
        return self._json("GET", "/version", timeout=1.5)

    def config(self) -> dict:
        return self._json("GET", "/configs", timeout=2.0)

    def proxies(self) -> dict:
        return self._json("GET", "/proxies", timeout=3.0)

    def providers(self) -> dict:
        return self._json("GET", "/providers/proxies", timeout=3.0)

    def provider(self, name: str = "primary") -> dict:
        return self._json("GET", f"/providers/proxies/{quote(name, safe='')}", timeout=3.0)

    def update_provider(self, name: str = "primary") -> dict:
        self._request("PUT", f"/providers/proxies/{quote(name, safe='')}", timeout=15.0)
        return self.provider(name)

    def select_proxy(self, group: str, name: str):
        self._request(
            "PUT", f"/proxies/{quote(group, safe='')}", timeout=2.0,
            json_body={"name": name},
        )
        selected = self._json("GET", f"/proxies/{quote(group, safe='')}", timeout=2.0)
        if selected.get("now") != name:
            raise ApiUnavailableError("Mihomo did not confirm the selected node")

    def delay(self, name: str, *, url: str = "https://cp.cloudflare.com", timeout_ms: int = 3000) -> int | None:
        query = urlencode({"url": url, "timeout": int(timeout_ms), "expected": 204})
        try:
            result = self._json(
                "GET", f"/proxies/{quote(name, safe='')}/delay?{query}", timeout=timeout_ms / 1000 + 1,
            )
        except (ApiUnavailableError, SubscriptionError):
            return None
        delay = result.get("delay")
        return delay if isinstance(delay, int) and delay > 0 else None

    def group_delay(self, group: str = "VPN", *, url: str = "https://cp.cloudflare.com",
                    timeout_ms: int = 3000) -> dict[str, int | None]:
        query = urlencode({"url": url, "timeout": int(timeout_ms), "expected": 204})
        result = self._json(
            "GET", f"/group/{quote(group, safe='')}/delay?{query}",
            timeout=timeout_ms / 1000 * 30 + 2,
        )
        return {
            name: (value if isinstance(value, int) and value > 0 else None)
            for name, value in result.items()
        }
