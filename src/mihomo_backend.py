"""Mihomo process, controller, TUN and ownership lifecycle."""

from __future__ import annotations

import hashlib
import json
import logging
import msvcrt
import os
import ctypes
import ctypes.wintypes
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
import socket
from dataclasses import dataclass

import psutil
import requests

from .core import Component, ComponentOwnership, ComponentState, DesiredState
from .happ_status import best_route_to
from .mihomo_api import MihomoApi
from .mihomo_config import RuntimePaths, SecretStore, new_controller_secret, render_config
from .mihomo_installer import installed_integrity, install_mihomo, sha256_file
from .mihomo_subscription import SubscriptionClient, SubscriptionResult
import yaml
from .vpn_backend import (
    ApiAuthError, ApiUnavailableError, BackendObservation, BinaryIntegrityError,
    Capability, ConfigValidationError, NetworkUnavailableError,
    OwnershipConflictError, ProviderFormatError, SubscriptionError, TunStartTimeout,
)

logger = logging.getLogger("happ_suite.mihomo")


@dataclass(frozen=True)
class _RouteObservation:
    interface_alias: str | None
    through_happ: bool


@dataclass(frozen=True)
class _StatusObservation:
    connected: bool
    route: _RouteObservation
    external_ok: bool
    gui_pid: int | None = None
    active_profile_id: str | None = None


def _file_hash(path: Path) -> str:
    return sha256_file(path)


def _same_path(first: str | Path, second: str | Path) -> bool:
    try:
        return os.path.normcase(str(Path(first).resolve())) == os.path.normcase(str(Path(second).resolve()))
    except OSError:
        return False


class MihomoVPNComponent(Component):
    """A VPN component whose stop operation requires persisted process proof."""

    capabilities = frozenset({
        Capability.START, Capability.STOP, Capability.VERIFY, Capability.TUN,
        Capability.SELECT_NODE, Capability.PROVIDERS, Capability.LATENCY_TEST,
    })

    def __init__(self, config, *, paths: RuntimePaths | None = None, popen=None,
                 wait_timeout: float | None = None):
        super().__init__("VPN")
        self.config = config
        self.paths = paths or RuntimePaths()
        self.subscription_client = SubscriptionClient(self.paths.state_root)
        self._popen = popen or subprocess.Popen
        self.wait_timeout = float(wait_timeout or getattr(config, "mihomo_wait_timeout", 50))
        self._lock = threading.RLock()
        self._process = None
        self._api: MihomoApi | None = None
        self._instance: dict | None = None
        self._launch_lock_file = None
        self._last_good_node: str | None = self._load_last_good_node()
        self.cancel_requested = threading.Event()
        self.runtime_data = {}
        self.last_error: str | None = None
        self._subscription_result = self._load_subscription_cache()

    @property
    def controller(self):
        return self._api

    def _provider_url(self) -> str:
        values = SecretStore(self.paths.secrets).load()
        url = values.get("subscription_url", "")
        if not url:
            raise SubscriptionError("Добавьте HTTPS-ссылку на совместимую подписку Mihomo/Clash")
        return url

    def _load_last_good_node(self) -> str | None:
        try:
            value = json.loads(self.paths.state.read_text(encoding="utf-8"))
            node = value.get("last_good_node") if isinstance(value, dict) else None
            return node if isinstance(node, str) and node else None
        except (OSError, ValueError):
            return None

    def _save_last_good_node(self, node: str):
        from .mihomo_config import _atomic_write
        payload = json.dumps({"last_good_node": node}, ensure_ascii=False).encode("utf-8")
        _atomic_write(self.paths.state, payload)

    def _acquire_launch_lock(self) -> bool:
        if self._launch_lock_file is not None:
            return True
        self.paths.ensure()
        stream = self.paths.launch_lock.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            stream.close()
            return False
        self._launch_lock_file = stream
        return True

    def _release_launch_lock(self):
        stream, self._launch_lock_file = self._launch_lock_file, None
        if stream is None:
            return
        try:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        stream.close()

    def set_subscription_url(self, url: str):
        from .mihomo_config import validate_subscription_url
        url = validate_subscription_url(url)
        result = self.subscription_client.fetch(url)
        self._save_subscription_cache(result)
        values = SecretStore(self.paths.secrets).load()
        values["subscription_url"] = url
        SecretStore(self.paths.secrets).save(values)
        self._subscription_result = result
        return result

    def _load_subscription_cache(self) -> SubscriptionResult | None:
        try:
            profile = yaml.safe_load(self.paths.subscription_config.read_text(encoding="utf-8"))
            nodes = SubscriptionClient._parse_mihomo_yaml(
                yaml.safe_dump(profile, allow_unicode=True).encode("utf-8")
            )
            return SubscriptionResult(
                profile=nodes, nodes=tuple(nodes["proxies"]), hostname="cached",
                redacted_id="cached", content_type="application/yaml",
                used_mihomo_suffix=False, hwid_active=False,
            )
        except (OSError, ValueError, yaml.YAMLError, ProviderFormatError):
            return None

    def _save_subscription_cache(self, result: SubscriptionResult):
        from .mihomo_config import _atomic_write
        allowed = {
            "proxies", "dns", "hosts", "sniffer", "ip-version", "ipv6",
            "tcp-concurrent", "unified-delay", "global-client-fingerprint",
        }
        cached_profile = {key: value for key, value in result.profile.items() if key in allowed}
        content = yaml.safe_dump(cached_profile, allow_unicode=True, sort_keys=False).encode("utf-8")
        _atomic_write(self.paths.subscription_config, content)

    def _secret(self) -> str:
        values = SecretStore(self.paths.secrets).load()
        secret = values.get("controller_secret")
        if not secret:
            secret = new_controller_secret()
            values["controller_secret"] = secret
            SecretStore(self.paths.secrets).save(values)
        return secret

    def _config_hash(self) -> str:
        return _file_hash(self.paths.config)

    def _find_process(self, pid: int):
        try:
            return psutil.Process(int(pid))
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            return None

    def _identity_matches(self, record: dict, process=None) -> bool:
        process = process or self._find_process(record.get("pid", -1))
        if process is None:
            return False
        try:
            with process.oneshot():
                if abs(process.create_time() - float(record["create_time"])) > 0.05:
                    return False
                if not _same_path(process.exe(), record["executable"]):
                    return False
            if not _same_path(record["executable"], self.paths.binary):
                return False
            if _file_hash(Path(record["executable"])) != record["executable_sha256"]:
                return False
            return self.paths.config.is_file() and self._config_hash() == record["config_sha256"]
        except (OSError, KeyError, TypeError, ValueError, psutil.Error):
            return False

    def detect(self) -> BackendObservation:
        """Adopt only a process matching the durable identity written by start()."""
        with self._lock:
            try:
                record = json.loads(self.paths.instance.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._instance = None
                self.ownership = ComponentOwnership.UNKNOWN
                external = self._external_mihomo_processes()
                if external:
                    self.ownership = ComponentOwnership.EXTERNAL
                    return BackendObservation("running", "external", external[0])
                conflict = self._active_external_tunnel()
                if conflict is not None:
                    state, owner, detail = conflict
                    self.ownership = owner
                    return BackendObservation(state, owner.value, detail=detail)
                return BackendObservation("stopped", "unknown")
            if not isinstance(record, dict) or not self._identity_matches(record):
                self._instance = None
                self.ownership = ComponentOwnership.UNKNOWN
                # A Mihomo process may belong to another application/session.
                external = self._external_mihomo_processes()
                if external:
                    self.ownership = ComponentOwnership.EXTERNAL
                    return BackendObservation("running", "external", external[0])
                conflict = self._active_external_tunnel()
                if conflict is not None:
                    state, owner, detail = conflict
                    self.ownership = owner
                    return BackendObservation(state, owner.value, detail=detail)
                return BackendObservation("stopped", "unknown")
            self._instance = record
            self.ownership = ComponentOwnership.SUITE
            self._process = self._find_process(record["pid"])
            return BackendObservation("running", "suite", int(record["pid"]), record.get("version"))

    @staticmethod
    def _active_external_tunnel():
        if sys.platform != "win32":
            return None
        route = best_route_to("1.1.1.1")
        alias = (route.interface_alias or "").casefold()
        if alias.startswith("happ-"):
            return "running", ComponentOwnership.EXTERNAL, {"interface": route.interface_alias}
        if alias in {"mihomo", "tun"}:
            # A route remains but no matching Suite process record exists.
            return "running", ComponentOwnership.UNKNOWN, {"interface": route.interface_alias}
        return None

    def _external_mihomo_processes(self) -> list[int]:
        found = []
        try:
            for proc in psutil.process_iter(["name", "exe"]):
                info = proc.info
                if str(info.get("name") or "").casefold() == "mihomo.exe":
                    found.append(proc.pid)
        except (psutil.Error, OSError):
            return found
        return found

    def backend_observation(self) -> BackendObservation:
        """Return Mihomo process and controller health without changing component intent."""
        observed = self.detect()
        if observed.state != "running":
            return observed
        try:
            api = self._api or self._api_for_record(self._instance)
            version = api.version().get("version")
            self._api = api
            return BackendObservation("running", observed.ownership, observed.pid, str(version or ""))
        except Exception:
            return BackendObservation("degraded", observed.ownership, observed.pid)

    def read_status(self, with_external_probe: bool = True):
        """Expose a small compatibility status shape for current monitor/UI code."""
        observation = self.detect()
        alias = None
        routed = False
        if sys.platform == "win32":
            route = best_route_to("1.1.1.1")
            alias = route.interface_alias
            routed = bool(alias and alias.casefold() in {"mihomo", "tun"})
        api_ready = False
        if observation.ownership == "suite":
            try:
                self._api = self._api or self._api_for_record(self._instance)
                self._api.version()
                api_ready = True
            except (ApiUnavailableError, ApiAuthError):
                pass
        external_ok = False
        if with_external_probe and api_ready:
            try:
                session = requests.Session()
                session.trust_env = False
                external_ok = session.get(
                    "https://cp.cloudflare.com", timeout=(3, 5), allow_redirects=False
                ).status_code == 204
            except requests.RequestException:
                pass
        return _StatusObservation(
            connected=bool(routed and api_ready and (external_ok or not with_external_probe)),
            route=_RouteObservation(alias, routed), external_ok=external_ok,
            gui_pid=observation.pid,
        )

    def _api_for_record(self, record: dict | None) -> MihomoApi:
        values = SecretStore(self.paths.secrets).load()
        port = (record or {}).get("controller_port", 19090)
        return MihomoApi(values.get("controller_secret", ""), port=port)

    def _validate_install(self):
        integrity = installed_integrity(self.paths)
        if integrity is None:
            raise BinaryIntegrityError("Установленный mihomo.exe не прошёл проверку SHA-256")
        version, digest = integrity
        try:
            result = subprocess.run(
                [str(self.paths.binary), "-v"], capture_output=True, text=True,
                timeout=8, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BinaryIntegrityError("Не удалось проверить версию Mihomo") from exc
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0 or version not in output:
            raise BinaryIntegrityError("Версия mihomo.exe не совпадает с закреплённым релизом")
        return version, digest

    @staticmethod
    def _available_controller_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    def _render_and_validate(self, controller_port: int, profile: dict) -> tuple[str, str]:
        self.paths.ensure()
        api_secret = self._secret()
        text = render_config(profile, api_secret, controller_port=controller_port)
        temporary = self.paths.config.with_suffix(".yaml.new")
        temporary.write_text(text, encoding="utf-8", newline="\n")
        binary = self.paths.binary
        result = subprocess.run(
            [str(binary), "-t", "-d", str(self.paths.mihomo_root), "-f", str(temporary)],
            capture_output=True, text=True, timeout=15, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            temporary.unlink(missing_ok=True)
            raise ConfigValidationError("Mihomo отклонил конфигурацию; предыдущая рабочая конфигурация сохранена")
        os.replace(temporary, self.paths.config)
        return api_secret, self._config_hash()

    @staticmethod
    def _network_probe() -> bool:
        try:
            session = requests.Session()
            session.trust_env = False
            return session.get(
                "https://cp.cloudflare.com", timeout=(2, 3), allow_redirects=False
            ).status_code == 204
        except requests.RequestException:
            return False

    @staticmethod
    def _send_ctrl_break(pid: int) -> bool:
        if os.name != "nt":
            return False
        kernel = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        kernel.AttachConsole.argtypes = (ctypes.wintypes.DWORD,)
        kernel.AttachConsole.restype = ctypes.wintypes.BOOL
        kernel.GenerateConsoleCtrlEvent.argtypes = (ctypes.wintypes.DWORD, ctypes.wintypes.DWORD)
        kernel.GenerateConsoleCtrlEvent.restype = ctypes.wintypes.BOOL
        kernel.FreeConsole.restype = ctypes.wintypes.BOOL
        if not kernel.AttachConsole(int(pid)):
            return False
        try:
            # Mihomo is launched with CREATE_NEW_CONSOLE, so this PID identifies
            # a dedicated console. CREATE_NEW_PROCESS_GROUP is ignored alongside
            # CREATE_NEW_CONSOLE; group 0 targets only processes in this console.
            return bool(kernel.GenerateConsoleCtrlEvent(1, 0))
        finally:
            kernel.FreeConsole()

    def _wait_for_route_cleanup(self, record: dict, timeout: float = 8.0) -> bool:
        if sys.platform != "win32":
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            route = best_route_to("1.1.1.1")
            alias = (route.interface_alias or "").casefold()
            if alias in {"mihomo", "tun"}:
                time.sleep(0.25)
                continue
            if record.get("network_online_before") and not self._network_probe():
                time.sleep(0.5)
                continue
            return True
        return False

    def start(self, server=None) -> bool:
        with self._lock:
            if server is not None:
                name = server.get("name") if isinstance(server, dict) else None
                if name and self._api:
                    try:
                        return self.select_node(name)
                    except Exception:
                        logger.info("Ignoring unsupported configured VPN profile", exc_info=True)
                return False
            self.request_enabled(True)
            self.cancel_requested.clear()
            self.set_state(ComponentState.STARTING)
            self.last_error = None
            try:
                if not self._acquire_launch_lock():
                    raise OwnershipConflictError("Другой экземпляр Suite уже управляет Mihomo")
                existing = self.detect()
                if existing.state == "running":
                    if existing.ownership == "external":
                        raise OwnershipConflictError("Обнаружен Mihomo, запущенный вне Happ Suite")
                    if existing.ownership == ComponentOwnership.SUITE.value:
                        self._api = self._api_for_record(self._instance)
                        if self.verify():
                            self.set_state(ComponentState.RUNNING)
                            self.runtime_data["started_at"] = time.time()
                            return True
                        self.last_error = "Найден процесс Mihomo Suite, но TUN/маршрут/HTTPS пока не подтверждены"
                        self.observe(ComponentState.DEGRADED)
                        self._release_launch_lock()
                        return False
                    raise OwnershipConflictError("Mihomo уже работает; Suite не запускает второй TUN")
                if self._external_mihomo_processes():
                    raise OwnershipConflictError("Обнаружен внешний mihomo.exe; второй TUN не запускается")
                route_conflict = self._active_external_tunnel()
                if route_conflict is not None:
                    _, owner, detail = route_conflict
                    self.ownership = owner
                    interface = (detail or {}).get("interface", "unknown")
                    raise OwnershipConflictError(
                        f"Обнаружен активный внешний туннель через {interface}; новый TUN не запускается"
                    )
                provider_url = self._provider_url()
                subscription = self.subscription_client.fetch(provider_url)
                self._save_subscription_cache(subscription)
                self._subscription_result = subscription
                if not self.paths.binary.is_file() or installed_integrity(self.paths) is None:
                    install_mihomo(self.paths)
                version, executable_sha = self._validate_install()
                controller_port = self._available_controller_port()
                api_secret, config_sha = self._render_and_validate(controller_port, subscription.profile)
                network_online_before = self._network_probe()
                startupinfo = None
                # Keep Mihomo in a private console so Ctrl+Break cannot reach
                # unrelated console applications. NEW_PROCESS_GROUP is ignored
                # by Windows when NEW_CONSOLE is also set.
                flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                if os.name == "nt":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = 0
                process = self._popen(
                    [str(self.paths.binary), "-d", str(self.paths.mihomo_root), "-f", str(self.paths.config)],
                    cwd=str(self.paths.mihomo_root), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=flags, startupinfo=startupinfo,
                )
                proc = psutil.Process(process.pid)
                record = {
                    "instance_id": str(uuid.uuid4()), "pid": int(process.pid),
                    "create_time": proc.create_time(), "executable": str(self.paths.binary.resolve()),
                    "executable_sha256": executable_sha, "config_sha256": config_sha,
                    "controller_port": controller_port, "version": version,
                    "network_online_before": network_online_before,
                }
                self._instance = record
                self._process = process
                from .mihomo_config import _atomic_write
                _atomic_write(self.paths.instance, (json.dumps(record, sort_keys=True) + "\n").encode())
                self.ownership = ComponentOwnership.SUITE
                self._api = MihomoApi(api_secret, port=controller_port)
                deadline = time.monotonic() + self.wait_timeout
                last_error = None
                selected_node_applied = False
                while time.monotonic() < deadline and not self.cancel_requested.is_set():
                    if process.poll() is not None:
                        raise TunStartTimeout("Mihomo завершился при запуске; проверьте права администратора и журнал")
                    try:
                        self._api.version()
                        self._api.config()
                        if (not selected_node_applied and self._last_good_node and
                                self._last_good_node in {node["name"] for node in subscription.nodes}):
                            self._api.select_proxy("VPN", self._last_good_node)
                            selected_node_applied = True
                        if self.verify():
                            self.set_state(ComponentState.RUNNING)
                            self.runtime_data["started_at"] = time.time()
                            return True
                    except (ApiUnavailableError, ApiAuthError, NetworkUnavailableError) as exc:
                        last_error = exc
                    time.sleep(0.5)
                if self.cancel_requested.is_set():
                    self._stop_locked()
                    self.observe(ComponentState.STOPPED)
                    return False
                raise TunStartTimeout("Не удалось подтвердить TUN, маршрут и HTTPS через Mihomo") from last_error
            except Exception as exc:
                logger.warning("Mihomo start failed: %s", type(exc).__name__)
                public_errors = (
                    SubscriptionError, ProviderFormatError, ApiAuthError,
                    ApiUnavailableError, BinaryIntegrityError, ConfigValidationError,
                    OwnershipConflictError, NetworkUnavailableError, TunStartTimeout,
                )
                self.last_error = (str(exc) if isinstance(exc, public_errors)
                                   else "Не удалось запустить Mihomo. Подробности сохранены в журнале.")
                if self.ownership == ComponentOwnership.SUITE:
                    self._stop_locked()
                else:
                    self._release_launch_lock()
                self.set_state(ComponentState.ERROR)
                return False

    def verify(self) -> bool:
        if not self._instance or not self._identity_matches(self._instance):
            return False
        try:
            config = (self._api or self._api_for_record(self._instance)).config()
            tun = config.get("tun")
            if not isinstance(tun, dict) or tun.get("enable") is not True:
                return False
            if sys.platform == "win32":
                route = best_route_to("1.1.1.1")
                if not route.interface_alias or route.interface_alias.casefold() not in {"mihomo", "tun"}:
                    return False
            session = requests.Session()
            session.trust_env = False
            response = session.get("https://cp.cloudflare.com", timeout=(3, 5), allow_redirects=False)
            return response.status_code == 204
        except (ApiUnavailableError, ApiAuthError, requests.RequestException, OSError, ValueError):
            return False

    def _stop_locked(self) -> bool:
        if self.ownership != ComponentOwnership.SUITE or not self._instance:
            return True
        record = self._instance
        if not self._identity_matches(record):
            self.ownership = ComponentOwnership.UNKNOWN
            self._instance = None
            self._release_launch_lock()
            self.last_error = "Идентичность процесса Mihomo изменилась; Suite не посылал ему команды остановки"
            return False
        process = self._find_process(record["pid"])
        if process is None:
            if not self._wait_for_route_cleanup(record):
                logger.error("Suite-owned Mihomo exited but its route or network did not recover")
                self.ownership = ComponentOwnership.UNKNOWN
                self.last_error = "Mihomo завершился, но восстановление сетевого маршрута не подтверждено"
                return False
            self.paths.instance.unlink(missing_ok=True)
            self.ownership = ComponentOwnership.UNKNOWN
            self._instance = None
            self._release_launch_lock()
            return True
        try:
            self._send_ctrl_break(int(record["pid"]))
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                # Revalidate immediately before escalation to avoid PID reuse.
                if not self._identity_matches(record):
                    self.ownership = ComponentOwnership.UNKNOWN
                    self.last_error = "Идентичность Mihomo изменилась до эскалации; процесс не завершался"
                    return False
                process.terminate()
                try:
                    process.wait(timeout=3)
                except psutil.TimeoutExpired:
                    if not self._identity_matches(record):
                        self.ownership = ComponentOwnership.UNKNOWN
                        self.last_error = "Идентичность Mihomo изменилась до принудительного завершения"
                        return False
                    process.kill()
                process.wait(timeout=3)
            if not self._wait_for_route_cleanup(record):
                logger.error("Suite-owned Mihomo exited but its route or network did not recover")
                self.ownership = ComponentOwnership.UNKNOWN
                self.last_error = "Mihomo завершился, но восстановление сетевого маршрута не подтверждено"
                return False
            self.paths.instance.unlink(missing_ok=True)
            self._instance = None
            self._process = None
            self._api = None
            self.ownership = ComponentOwnership.UNKNOWN
            self._release_launch_lock()
            return True
        except (psutil.Error, OSError):
            self.ownership = ComponentOwnership.UNKNOWN
            self.last_error = "Не удалось безопасно завершить Mihomo или подтвердить восстановление маршрута"
            return False

    def stop(self) -> bool:
        with self._lock:
            self.request_enabled(False)
            if self.ownership != ComponentOwnership.SUITE:
                self.detect()
            if self.ownership != ComponentOwnership.SUITE:
                logger.info("Leaving Mihomo with non-Suite ownership untouched")
                return True
            self.set_state(ComponentState.STOPPING)
            result = self._stop_locked()
            self.last_error = None if result else (self.last_error or
                "Mihomo мог быть остановлен, но Suite не подтвердил восстановление маршрута; "
                "запись владения сохранена для диагностики."
            )
            self.set_state(ComponentState.STOPPED if result else ComponentState.ERROR)
            return result

    def stop_owned(self) -> bool:
        return self.stop()

    def recover(self) -> bool:
        # Recovery is deliberately limited to a process we can prove is ours.
        observation = self.detect()
        if observation.ownership != "suite":
            return False
        return self.verify()

    def provider_nodes(self) -> list[dict]:
        result = self._subscription_result or self._load_subscription_cache()
        proxies = list(result.nodes) if result else []
        if not isinstance(proxies, list) or not proxies:
            raise ProviderFormatError("Subscription has no cached Mihomo nodes; save the subscription first")
        return [node for node in proxies if isinstance(node, dict) and node.get("name")]

    def update_nodes(self) -> list[dict]:
        if self._api:
            group = self._api.proxies().get("VPN", {})
            current_names = group.get("all")
            if isinstance(current_names, list):
                return [{"name": name} for name in current_names if name not in {"DIRECT", "REJECT"}]
        result = self.subscription_client.fetch(self._provider_url())
        self._save_subscription_cache(result)
        self._subscription_result = result
        return list(result.nodes)

    def select_node(self, name: str) -> bool:
        valid = {node["name"] for node in self.provider_nodes()}
        if name not in valid:
            raise ProviderFormatError("Узел отсутствует в текущем каталоге подписки")
        if self._api:
            current = self._api.proxies().get("VPN", {}).get("all", [])
            if name not in current:
                raise ProviderFormatError("Этот узел загрузится при следующем запуске Mihomo")
            self._api.select_proxy("VPN", name)
        self._last_good_node = name
        self._save_last_good_node(name)
        return True

    def choose_best_node(self) -> str | None:
        if not self._api:
            raise NetworkUnavailableError("Включите Mihomo, чтобы проверить задержку узлов")
        delays = self._api.group_delay("VPN")
        candidates = [(delay, name) for name, delay in delays.items()
                      if delay is not None and name.casefold() != "direct"]
        if not candidates:
            if self._last_good_node:
                return self._last_good_node
            raise NetworkUnavailableError("Нет доступных VPN-узлов; DIRECT fallback запрещён")
        delay, selected = min(candidates)
        current = self._api.proxies().get("VPN", {}).get("now")
        if current and current in delays and delays[current] is not None:
            current_delay = delays[current]
            if current_delay - delay < max(50, current_delay * 0.15):
                selected = current
        self.select_node(selected)
        return selected
