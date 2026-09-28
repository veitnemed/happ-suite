"""Fetch and validate Remnawave-compatible Mihomo subscriptions."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
import yaml

from .mihomo_config import _atomic_write, validate_subscription_url
from .vpn_backend import ProviderFormatError, SubscriptionError


@dataclass(frozen=True)
class SubscriptionResult:
    profile: dict = field(repr=False)
    nodes: tuple[dict, ...] = field(repr=False)
    hostname: str
    redacted_id: str
    content_type: str
    used_mihomo_suffix: bool
    hwid_active: bool
    update_interval_hours: int | None = None
    expires_at: int | None = None


def load_or_create_hwid(directory: Path) -> str:
    """Return a stable random, non-hardware-derived Remnawave device ID."""
    path = Path(directory) / "installation-id"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        value = ""
    if re.fullmatch(r"[A-Za-z0-9=-]{10,64}", value):
        return value

    candidate = secrets.token_hex(20)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            value = path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            value = ""
        if re.fullmatch(r"[A-Za-z0-9=-]{10,64}", value):
            return value
        _atomic_write(path, candidate.encode("ascii"))
        return candidate
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(candidate)
        stream.flush()
        os.fsync(stream.fileno())
    return candidate


class SubscriptionClient:
    """Fetch a subscription with Mihomo identity and return validated YAML."""

    USER_AGENT = "mihomo/HappSuite-2.2.0"
    ACCEPT = "application/yaml, text/yaml, */*"
    MAX_BYTES = 8 * 1024 * 1024
    MAX_REDIRECTS = 5

    def __init__(self, hwid_directory: Path, *, session=None, user_agent: str | None = None):
        self.hwid = load_or_create_hwid(hwid_directory)
        self.session = session or requests.Session()
        self.user_agent = user_agent or self.USER_AGENT

    def fetch(self, url: str) -> SubscriptionResult:
        source = validate_subscription_url(url)
        source_host = (urlsplit(source).hostname or "provider").lower()
        fingerprint = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
        response = self._get(source)
        suffix_used = False
        try:
            self._check_response(response)
            body = self._read_body(response)
        except Exception:
            response.close()
            raise
        if self._looks_like_html(body, response.headers.get("content-type", "").split(";", 1)[0].strip().lower()):
            response.close()
            fallback = self._mihomo_endpoint(source)
            if fallback == source:
                raise ProviderFormatError("Сервер вернул веб-страницу вместо конфигурации VPN")
            response = self._get(fallback)
            suffix_used = True

        try:
            self._check_response(response)
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if suffix_used:
                body = self._read_body(response)
            if self._looks_like_html(body, content_type):
                raise ProviderFormatError("Сервер вернул веб-страницу вместо конфигурации VPN")
            profile = self._parse_mihomo_yaml(body)
            info = self._userinfo(response.headers.get("subscription-userinfo", ""))
            expiry = info.get("expire", 0) or 0
            if expiry and expiry <= int(time.time()):
                raise SubscriptionError("Подписка истекла")
            interval = self._positive_int(response.headers.get("profile-update-interval"))
            return SubscriptionResult(
                profile=profile,
                nodes=tuple(profile["proxies"]),
                hostname=source_host,
                redacted_id=fingerprint,
                content_type=content_type or "unknown",
                used_mihomo_suffix=suffix_used,
                hwid_active=self._true(response.headers.get("x-hwid-active")),
                update_interval_hours=interval,
                expires_at=expiry or None,
            )
        finally:
            response.close()

    def _get(self, url: str):
        headers = {
            "User-Agent": self.user_agent,
            "Accept": self.ACCEPT,
            "x-hwid": self.hwid,
            "x-device-os": "Windows",
            "x-ver-os": self._windows_version(),
        }
        current = url
        original_host = (urlsplit(url).hostname or "").casefold()
        for redirect_count in range(self.MAX_REDIRECTS + 1):
            try:
                cookies = getattr(self.session, "cookies", None)
                if cookies is not None:
                    cookies.clear()
                response = self.session.get(
                    current, headers=headers, timeout=(8, 25), allow_redirects=False,
                    stream=True,
                )
            except requests.Timeout as exc:
                raise SubscriptionError("Истекло время ожидания ответа сервера подписки") from None
            except requests.RequestException as exc:
                raise SubscriptionError("Сервис подписки недоступен") from None

            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location") or response.headers.get("Location")
            response.close()
            if not location or redirect_count == self.MAX_REDIRECTS:
                raise SubscriptionError("Не удалось выполнить перенаправление подписки")
            target = urljoin(current, location)
            try:
                validate_subscription_url(target)
            except SubscriptionError:
                raise SubscriptionError("Сервер перенаправил подписку на небезопасный адрес") from None
            target_host = (urlsplit(target).hostname or "").casefold()
            if target_host != original_host:
                # A signed CDN redirect does not need the subscriber's device ID.
                headers = {key: value for key, value in headers.items() if key.casefold() != "x-hwid"}
            current = target
        raise SubscriptionError("Не удалось выполнить перенаправление подписки")

    @staticmethod
    def _windows_version() -> str:
        try:
            import sys
            return ".".join(str(part) for part in sys.getwindowsversion()[:3])
        except (AttributeError, OSError):
            return "Windows"

    @staticmethod
    def _true(value) -> bool:
        return str(value or "").strip().casefold() in {"1", "true", "yes"}

    def _check_response(self, response):
        headers = response.headers
        if self._true(headers.get("x-hwid-max-devices-reached")) or self._true(headers.get("x-hwid-limit")):
            raise SubscriptionError("Достигнут лимит устройств подписки")
        if self._true(headers.get("x-hwid-not-supported")):
            raise SubscriptionError("Провайдер требует идентификатор устройства (HWID)")
        if response.status_code == 403:
            raise SubscriptionError("Подписка недоступна или истекла (HTTP 403)")
        if response.status_code == 404:
            raise SubscriptionError("Подписка недоступна или истекла (HTTP 404)")
        if response.status_code >= 500:
            raise SubscriptionError("Сервис подписки временно недоступен")
        if response.status_code != 200:
            raise SubscriptionError(f"Сервис подписки ответил HTTP {response.status_code}")
        if self._true(headers.get("x-hwid-active")) and not self.hwid:
            raise SubscriptionError("Провайдер требует идентификатор устройства (HWID)")

    @classmethod
    def _mihomo_endpoint(cls, url: str) -> str:
        parts = urlsplit(url)
        if parts.path.rstrip("/").lower().endswith("/mihomo"):
            return url
        path = parts.path.rstrip("/") + "/mihomo"
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))

    def _read_body(self, response) -> bytes:
        parts = []
        size = 0
        try:
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > self.MAX_BYTES:
                    raise ProviderFormatError("Ответ подписки превышает 8 МБ")
                parts.append(chunk)
        except requests.RequestException:
            raise SubscriptionError("Не удалось загрузить подписку") from None
        return b"".join(parts)

    @staticmethod
    def _looks_like_html(body: bytes, content_type: str) -> bool:
        if content_type == "text/html":
            return True
        prefix = body[:4096].lstrip().lower()
        return prefix.startswith((b"<!doctype html", b"<html"))

    @staticmethod
    def _parse_mihomo_yaml(body: bytes) -> dict:
        try:
            profile = yaml.safe_load(body.decode("utf-8-sig"))
        except (UnicodeError, yaml.YAMLError):
            raise ProviderFormatError("Некорректная конфигурация Mihomo") from None
        if not isinstance(profile, dict):
            raise ProviderFormatError("Некорректная конфигурация Mihomo")
        nodes = profile.get("proxies")
        if not isinstance(nodes, list) or not nodes:
            raise ProviderFormatError("Конфигурация Mihomo не содержит узлов")
        seen = set()
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("name"), str) or not node.get("name"):
                raise ProviderFormatError("В конфигурации Mihomo найден некорректный узел")
            if not isinstance(node.get("type"), str) or not node.get("type"):
                raise ProviderFormatError("В конфигурации Mihomo найден некорректный узел")
            if node["name"] in seen:
                raise ProviderFormatError("В конфигурации Mihomo повторяется имя узла")
            seen.add(node["name"])
        return profile

    @staticmethod
    def _userinfo(value: str) -> dict[str, int]:
        info = {}
        for piece in value.split(";"):
            key, separator, raw = piece.strip().partition("=")
            if separator and key.strip().casefold() in {"upload", "download", "total", "expire"}:
                try:
                    info[key.strip().casefold()] = int(raw.strip())
                except ValueError:
                    continue
        return info

    @staticmethod
    def _positive_int(value) -> int | None:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else None
        except (TypeError, ValueError):
            return None
