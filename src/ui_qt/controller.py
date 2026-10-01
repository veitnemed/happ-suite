"""Qt-facing commands. Blocking runtime work stays off the GUI thread."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import threading
import time

from PySide6.QtCore import QObject, Signal, Slot, QTimer

from ..components import (MihomoComponent, AgUnlockerComponent, VSCodeComponent,
                          GoogleAntigravityComponent, ComponentOperationError, ComponentStatus,
                          ComponentInstallation)
from ..core import ComponentState
from ..gemini_availability import GeminiAvailabilityClient
from ..gemini_dns import DnsManager, apply_action
from ..vpn_backend import SubscriptionError, ProviderFormatError, NetworkUnavailableError
from ..node_region import country_hint
from ..windows_elevation import is_admin
from ..autostart import is_enabled, set_enabled
from ..hotkey import HotkeyChoice, MOD_CONTROL, MOD_ALT, MOD_SHIFT

logger = logging.getLogger("happ_suite.ui_qt")


class Controller(QObject):
    message = Signal(str)
    changed = Signal(object)
    busy_changed = Signal(str, bool)
    _finished = Signal(str, object, str)
    show_requested = Signal()
    quit_requested = Signal()
    dns_requested = Signal()
    best_requested = Signal()
    subscription_loaded = Signal(str)

    def __init__(self, tray, config, parent=None):
        super().__init__(parent)
        self.tray = tray
        self.config = config
        self.vpn = tray.orchestrator.vpn
        self._active = set()
        self._closed = False
        self._nodes = []
        self._dns = None
        self._dns_error = ""
        self._gemini = None
        self._active_node = None
        self._installations = {}
        self._pending = {}
        self._next_inspect = 0
        self._autostart = is_enabled()
        self._exit_country = None
        self.components = {
            "mihomo": MihomoComponent(), "ag_unlocker": AgUnlockerComponent(),
            "vscode": VSCodeComponent(), "google_antigravity": GoogleAntigravityComponent(),
        }
        self._finished.connect(self._complete)
        self.dns_requested.connect(self.toggle_dns)
        self.best_requested.connect(self.choose_best)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)

    def start(self):
        self.timer.start()
        if hasattr(self.vpn, "provider_nodes"):
            self._run("nodes", self.vpn.provider_nodes)
        self.refresh()

    def close(self):
        self._closed = True
        self.timer.stop()

    def _run(self, key, command):
        if self._closed or key in self._active:
            return False
        self._active.add(key)
        self.busy_changed.emit(key, True)
        self._publish()

        def worker():
            result, error = None, ""
            try:
                result = command()
            except (SubscriptionError, ProviderFormatError, NetworkUnavailableError,
                    ComponentOperationError) as exc:
                error = str(exc)
            except ValueError as exc:
                logger.error("Qt command %s failed: %s", key, type(exc).__name__)
                error = ("Нужна корректная HTTPS-ссылка подписки." if key == "subscription" else
                         "Некорректное значение для операции.")
            except Exception as exc:
                # Arbitrary exception text/tracebacks can contain a subscription URL.
                logger.error("Qt command %s failed: %s", key, type(exc).__name__)
                error = f"Операция не завершена: {type(exc).__name__}. Подробности в журнале."
            if not self._closed:
                self._finished.emit(key, result, error)

        threading.Thread(target=worker, name=f"QtCommand-{key}", daemon=True).start()
        return True

    @Slot(str, object, str)
    def _complete(self, key, result, error):
        if self._closed:
            return
        self._active.discard(key)
        self.busy_changed.emit(key, False)
        if error:
            self.message.emit(error)
            if key == "inspect":
                self._dns_error = error
        elif key in ("nodes", "subscription", "refresh_nodes"):
            nodes = result.nodes if key == "subscription" else result
            self._nodes = [{"name": node["name"], "type": node.get("type", "")}
                           for node in nodes]
            if key != "nodes":
                warning = (" Провайдер сообщил о лимите устройств." if
                           key == "subscription" and result.device_limit_warning else "")
                self.message.emit(f"Загружено серверов: {len(self._nodes)}.{warning}")
        elif key == "inspect":
            self._dns, self._dns_error, self._installations, self._active_node = result
            for name in list(self._pending):
                if self._installations[name].installed:
                    del self._pending[name]
                    self.message.emit("Установка завершена и обнаружена.")
                elif time.monotonic() > self._pending[name]:
                    del self._pending[name]
                    self.message.emit("Установка ещё не обнаружена. Проверьте официальный установщик.")
        elif key == "gemini":
            self._gemini = result
            self._exit_country = result.exit_country
            self.message.emit("Диагностика Gemini завершена; доступ аккаунта проверяется в браузере.")
        elif key == "dns":
            self.message.emit(result["message"])
            self._next_inspect = 0
            self._gemini = None
        elif key.startswith("install:"):
            name = key.split(":", 1)[1]
            self._installations[name] = result
            if result.status is ComponentStatus.INSTALLING:
                self._pending[name] = time.monotonic() + 300
                self.message.emit("Открыт официальный установщик. Ожидаю обнаружения компонента.")
            else:
                self.message.emit("Компонент установлен и проверен.")
            self._next_inspect = 0
        elif key == "autostart":
            self._autostart = result
            self.message.emit("Автозапуск обновлён.")
        elif key == "read_subscription":
            self.subscription_loaded.emit(result or "")
        elif key.startswith("open:"):
            self.message.emit("Приложение открыто." if result else "Установленное приложение не удалось открыть.")
        elif key in ("toggle", "toggle_ag", "elevated"):
            self._next_inspect = 0
        elif key in ("apply", "best"):
            self._gemini = None
            self._active_node = None
            self._exit_country = getattr(self.vpn, "last_exit_country", None) if key == "best" else None
            self._next_inspect = 0
            self.message.emit("Сервер выбран." if key == "apply" else result)
        self._publish()

    def _publish(self):
        self.changed.emit({
            "vpn_state": self.vpn.state,
            "vpn_error": getattr(self.vpn, "last_error", None),
            "ownership": self.vpn.ownership.value,
            "ag_state": self.tray.orchestrator.ag_unlocker.state,
            "nodes": self._nodes,
            "preferred_node": getattr(self.vpn, "preferred_node", None),
            "active_node": self._active_node,
            "exit_country": self._exit_country,
            "dns": self._dns, "dns_error": self._dns_error,
            "gemini": self._gemini, "installations": dict(self._installations),
            "hotkeys": {key: value.label for key, value in self.tray._hotkey_choices.items()},
            "autostart": self._autostart,
            "busy": set(self._active), "pending": set(self._pending),
            "network_busy": self.tray._action_lock.locked(),
        })

    def refresh(self):
        if self._closed:
            return
        if time.monotonic() >= self._next_inspect and "dns" not in self._active:
            self._next_inspect = time.monotonic() + (3 if self._pending else 15)

            def inspect():
                dns, error = None, ""
                try:
                    dns = DnsManager().status()
                except Exception as exc:
                    error = f"Проверка DNS не завершена: {type(exc).__name__}"
                installations = {}
                for key, component in self.components.items():
                    try:
                        installations[key] = component.detect()
                    except Exception as exc:
                        logger.error("Component detection %s failed: %s", key, type(exc).__name__)
                        installations[key] = ComponentInstallation(key, component.display_name, False,
                            None, False, False, ComponentStatus.UNKNOWN)
                active = None
                try:
                    active = self.vpn.active_node()
                except Exception as exc:
                    logger.warning("Active server inspection failed: %s", type(exc).__name__)
                return dns, error, installations, active
            self._run("inspect", inspect)
        self._publish()

    def save_subscription(self, url):
        if not url.strip():
            self.message.emit("Вставьте HTTPS-ссылку подписки.")
            return
        if self._active & {"subscription", "refresh_nodes", "nodes", "best", "apply"}:
            return
        if self._node_action("subscription", lambda: self.vpn.set_subscription_url(url.strip())):
            self.message.emit("Проверяю подписку как Mihomo-клиент…")

    def load_subscription(self):
        if hasattr(self.vpn, "saved_subscription_url"):
            self._run("read_subscription", self.vpn.saved_subscription_url)

    def refresh_nodes(self):
        if not self._active & {"subscription", "refresh_nodes", "nodes", "best", "apply"}:
            self._node_action("refresh_nodes", self.vpn.update_nodes)

    def _node_action(self, key, command):
        # The tray/hotkeys and the window share the runtime operation lock.
        def locked():
            if not self.tray._action_lock.acquire(blocking=False):
                raise NetworkUnavailableError("Подождите: выполняется другая операция VPN или relay.")
            try:
                return command()
            finally:
                self.tray._action_lock.release()
        return self._run(key, locked)

    def apply_node(self, name):
        if name and not self._active & {"subscription", "refresh_nodes", "nodes", "best", "apply"}:
            self._node_action("apply", lambda: self.vpn.select_node(name))

    def choose_best(self):
        if "best" in self._active:
            return
        # Before connect, keep the established elevation flow and continuation flag.
        if self.vpn.state not in (ComponentState.RUNNING, ComponentState.DEGRADED):
            first = next((node["name"] for node in self._nodes
                          if country_hint(node["name"]) not in (None, "RU")), None)
            if not first:
                self.message.emit("В подписке нет узлов с указанной зарубежной страной.")
                return

            def connect():
                if not self.tray._action_lock.acquire(blocking=False):
                    raise NetworkUnavailableError("Подождите: выполняется другая операция.")
                try:
                    self.vpn.select_node(first)
                finally:
                    self.tray._action_lock.release()
                self.tray.request_toggle_vpn(best_foreign=True)
                if not is_admin():
                    return "Подтвердите UAC; проверка узлов продолжится после перезапуска."
                deadline = time.monotonic() + 150
                while time.monotonic() < deadline:
                    if self.vpn.read_status(with_external_probe=False).connected:
                        break
                    if self.vpn.state is ComponentState.ERROR:
                        raise NetworkUnavailableError(self.vpn.last_error or "VPN не подключился")
                    time.sleep(1)
                else:
                    raise NetworkUnavailableError("Время подключения истекло.")
                if not self.tray._action_lock.acquire(timeout=10):
                    raise NetworkUnavailableError("Предыдущая операция VPN ещё не завершилась.")
                try:
                    return self._best_connected()
                finally:
                    self.tray._action_lock.release()
            self._run("best", connect)
        else:
            self._node_action("best", self._best_connected)

    def _best_connected(self):
        self.vpn.choose_best_foreign_node(progress=self.message.emit)
        return "Лучший зарубежный сервер выбран и проверен."

    def toggle_vpn(self):
        if not self._active & {"best", "apply", "subscription", "refresh_nodes"}:
            self._run("toggle", self.tray.request_toggle_vpn)

    def toggle_ag(self):
        self._run("toggle_ag", self.tray.request_toggle_antigravity)

    @Slot()
    def toggle_dns(self):
        if self._dns is None or self._dns_error or "inspect" in self._active:
            self.message.emit("DNS ещё проверяется. Повторите через несколько секунд.")
            return
        action = "disable" if self._dns["managed"] else "enable"
        self._run("dns", lambda: apply_action(action))

    def check_gemini(self):
        self._run("gemini", lambda: GeminiAvailabilityClient(dns_status=DnsManager().status).check())

    def component_action(self, name):
        if name in self._pending or f"install:{name}" in self._active:
            return
        component = self.components[name]
        installed = self._installations.get(name)
        if installed and installed.installed and name != "mihomo":
            if name == "ag_unlocker":
                self.toggle_ag()
            else:
                self._run(f"open:{name}", component.open)
        else:
            self._run(f"install:{name}", component.install)

    def set_autostart(self, enabled):
        def update():
            set_enabled(enabled)
            return is_enabled()
        self._run("autostart", update)

    def capture_hotkey(self, component):
        self.tray._capture_shortcut(component)
        self.message.emit("Нажмите сочетание в течение 10 секунд. Esc — отмена.")

    def set_hotkey(self, component, text):
        if text.startswith("Ctrl+Alt+"):
            choice = HotkeyChoice(ord(text[-1]), MOD_CONTROL | MOD_ALT)
        elif text.startswith("Ctrl+Shift+"):
            choice = HotkeyChoice(ord(text[-1]), MOD_CONTROL | MOD_SHIFT)
        elif text in {"F8", "F9", "F10"}:
            choice = HotkeyChoice(0x6F + int(text[1:]))
        else:
            return
        self._run("hotkey", lambda: self.tray.set_shortcut(component, choice))

    def open_log(self):
        path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "HappSuite" / "logs" / "happ_suite.log"
        self._run("log", lambda: os.startfile(path))
