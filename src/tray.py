"""
System Tray UI for Relay Studio.
Shows 🟢/🟡/🔴 icon + context menu with two independent controls:
  • Ctrl+Alt+H — toggle VPN
  • Ctrl+Alt+G — toggle AG Unlocker
"""
import logging
import threading
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

try:
    from .app_config import Config
    from .core import Orchestrator, ComponentState, ComponentOwnership
    from .health import HealthMonitor, HealthStatus
    from .hotkey import (GlobalHotkey, HotkeyChoice, NextKeyCapture, VK_H, VK_G,
                         VK_D, VK_F8, VK_F9, VK_F10, SHORTCUT_MODIFIERS, load_hotkey_choices,
                         save_hotkey_choices)
    from .windows_elevation import is_admin, relaunch_vpn_elevated
except (ImportError, ValueError):
    from app_config import Config
    from core import Orchestrator, ComponentState, ComponentOwnership
    from health import HealthMonitor, HealthStatus
    from hotkey import (GlobalHotkey, HotkeyChoice, NextKeyCapture, VK_H, VK_G,
                        VK_D, VK_F8, VK_F9, VK_F10, SHORTCUT_MODIFIERS, load_hotkey_choices,
                        save_hotkey_choices)
    from windows_elevation import is_admin, relaunch_vpn_elevated

logger = logging.getLogger("happ_suite.tray")


def _create_icon_image(color: str, letter: str = "H") -> Image.Image:
    """Create one distinguishable colored system tray icon."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    colors = {
        "gray": (128, 128, 128),
        "green": (46, 204, 113),
        "yellow": (241, 196, 15),
        "red": (231, 76, 60),
        "half": (52, 152, 219),   # blue — one of two components active
    }
    rgb = colors.get(color, colors["yellow"])

    # Filled circle
    draw.ellipse([4, 4, size - 4, size - 4], fill=rgb + (255,))
    # Border
    draw.ellipse([4, 4, size - 4, size - 4], outline=(0, 0, 0, 80), width=2)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 36)
    except OSError:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), letter, font=font)
    text_width = box[2] - box[0]
    text_height = box[3] - box[1]
    draw.text(
        ((size - text_width) / 2, (size - text_height) / 2 - box[1]),
        letter,
        fill=(255, 255, 255, 255),
        font=font,
    )

    return img


class TrayApp:
    """System tray application for Relay Studio."""

    def __init__(self, orchestrator: Orchestrator, config: Config, health_monitor: HealthMonitor,
                 on_open_dashboard=None, on_exit=None, on_toggle_dns=None):
        self.orchestrator = orchestrator
        self.config = config
        self.health_monitor = health_monitor
        self.on_open_dashboard = on_open_dashboard
        self.on_exit = on_exit
        self.on_toggle_dns = on_toggle_dns
        self._icon = None
        self._happ_icon = None
        self._gemini_icon = None
        self._icons = []
        self._current_color = "gray"

        # Two independent, persisted hotkeys. New bindings are registered before
        # replacing old ones, so a busy/reserved key never leaves control lost.
        self._hotkey_choices = load_hotkey_choices()
        self._hotkey_happ = GlobalHotkey(
            self._hotkey_choices["happ"].vk,
            self._on_happ_shortcut,
            self._hotkey_choices["happ"].modifiers,
        )
        self._hotkey_ag = GlobalHotkey(
            self._hotkey_choices["gemini"].vk,
            self._on_gemini_shortcut,
            self._hotkey_choices["gemini"].modifiers,
        )
        self._hotkey_dns = GlobalHotkey(
            self._hotkey_choices["dns"].vk,
            self._on_dns_shortcut,
            self._hotkey_choices["dns"].modifiers,
        )
        self._happ_hotkey_registered = False
        self._gemini_hotkey_registered = False
        self._dns_hotkey_registered = False
        self._hotkey_change_lock = threading.Lock()
        self._capture_active = False

        # One shared action lock — prevents two toggles running concurrently
        self._action_lock = threading.Lock()

        # Register callbacks
        self.orchestrator.on_state_change(self._on_component_state_change)
        self.health_monitor.on_health_update(self._on_health_update)

    # ── State callbacks ───────────────────────────────────────────────────────

    def _on_component_state_change(self, name: str, old: ComponentState, new: ComponentState):
        """Called when any component changes state."""
        logger.info("[%s] %s → %s", name, old.value, new.value)
        self._update_icon()

    def _on_health_update(self, status: HealthStatus):
        """Called on each health check cycle."""
        self._update_icon()

    # ── Icon / tooltip ────────────────────────────────────────────────────────

    def _icon_color(self) -> str:
        """Choose tray icon color based on both components."""
        happ_st = self.orchestrator.happ.state
        ag_st = self.orchestrator.ag_unlocker.state

        if happ_st == ComponentState.ERROR or ag_st == ComponentState.ERROR:
            return "red"
        if happ_st in (ComponentState.STARTING, ComponentState.STOPPING,
                       ComponentState.RECOVERING):
            return "yellow"
        if ag_st in (ComponentState.STARTING, ComponentState.STOPPING,
                     ComponentState.RECOVERING):
            return "yellow"
        if happ_st == ComponentState.DEGRADED or ag_st == ComponentState.DEGRADED:
            return "yellow"

        happ_on = happ_st == ComponentState.RUNNING
        ag_on = ag_st == ComponentState.RUNNING

        if happ_on and ag_on:
            return "green"
        if happ_on or ag_on:
            return "half"   # blue — partial
        return "gray"

    @staticmethod
    def _component_color(state: ComponentState) -> str:
        if state == ComponentState.ERROR:
            return "red"
        if state in (
            ComponentState.STARTING,
            ComponentState.STOPPING,
            ComponentState.RECOVERING,
            ComponentState.DEGRADED,
        ):
            return "yellow"
        if state == ComponentState.RUNNING:
            return "green"
        return "gray"

    def _update_icon(self):
        """Update the tray icon color and menu."""
        if not self._icons:
            return

        states = (
            (self._happ_icon, self.orchestrator.happ.state, "V", self._happ_tooltip()),
            (self._gemini_icon, self.orchestrator.ag_unlocker.state, "G", self._gemini_tooltip()),
        )
        for icon, state, letter, title in states:
            if icon is None:
                continue
            color = self._component_color(state)
            icon.icon = _create_icon_image(color, letter)
            icon.title = title
            icon.update_menu()

    def _happ_label(self) -> str:
        state = self.orchestrator.happ.state
        icons = {
            ComponentState.RUNNING:   "🟢",
            ComponentState.STARTING:  "🟡",
            ComponentState.STOPPING:  "🟡",
            ComponentState.RECOVERING:"🟡",
            ComponentState.DEGRADED:  "🟡",
            ComponentState.ERROR:     "🔴",
            ComponentState.STOPPED:   "⚪",
        }
        em = icons.get(state, "⚪")
        return f"{em} {self._hotkey_choices['happ'].label} — VPN  [{state.value}]"

    def _ag_label(self) -> str:
        state = self.orchestrator.ag_unlocker.state
        icons = {
            ComponentState.RUNNING:   "🟢",
            ComponentState.STARTING:  "🟡",
            ComponentState.STOPPING:  "🟡",
            ComponentState.RECOVERING:"🟡",
            ComponentState.DEGRADED:  "🟡",
            ComponentState.ERROR:     "🔴",
            ComponentState.STOPPED:   "⚪",
        }
        em = icons.get(state, "⚪")
        return f"{em} {self._hotkey_choices['gemini'].label} — AG Unlocker  [{state.value}]"

    def _get_tooltip(self) -> str:
        """Generate tooltip text."""
        happ_st = self.orchestrator.happ.state
        ag_st = self.orchestrator.ag_unlocker.state

        happ_names = {
            ComponentState.RUNNING:   "🟢 VPN: Работает",
            ComponentState.STARTING:  "🟡 VPN: Запуск...",
            ComponentState.STOPPING:  "🟡 VPN: Отключение...",
            ComponentState.RECOVERING:"🟡 VPN: Восстановление...",
            ComponentState.DEGRADED:  "🟡 VPN: Деградировано",
            ComponentState.ERROR:     "🔴 VPN: Ошибка",
            ComponentState.STOPPED:   "⚪ VPN: Выключен",
        }
        ag_names = {
            ComponentState.RUNNING:   "AG: Работает",
            ComponentState.STARTING:  "AG: Запуск...",
            ComponentState.STOPPING:  "AG: Остановка...",
            ComponentState.DEGRADED:  "AG: Частично готов",
            ComponentState.ERROR:     "AG: Ошибка",
            ComponentState.STOPPED:   "AG: Выключен",
        }
        parts = [
            "Relay Studio",
            happ_names.get(happ_st, "VPN: ?"),
            ag_names.get(ag_st, "AG: ?"),
        ]
        if not self._happ_hotkey_registered:
            parts.append(f"⚠ {self._hotkey_choices['happ'].label} недоступна")
        if not self._gemini_hotkey_registered:
            parts.append(f"⚠ {self._hotkey_choices['gemini'].label} недоступна")
        if not self._dns_hotkey_registered:
            parts.append(f"⚠ {self._hotkey_choices['dns'].label} недоступна")

        health = self.health_monitor.last_status
        if health and health.tunnel_up and health.current_server != "Не определен":
            parts.append(health.current_server)

        return " | ".join(parts)

    def _happ_tooltip(self) -> str:
        labels = {
            ComponentState.RUNNING: "VPN включён",
            ComponentState.STARTING: "VPN подключается",
            ComponentState.STOPPING: "VPN отключается",
            ComponentState.RECOVERING: "VPN восстанавливается",
            ComponentState.DEGRADED: "VPN не подтверждён",
            ComponentState.ERROR: "Ошибка VPN",
            ComponentState.STOPPED: "VPN выключен",
        }
        return "VPN — " + labels.get(self.orchestrator.happ.state, "состояние неизвестно")

    def _gemini_tooltip(self) -> str:
        labels = {
            ComponentState.RUNNING: "Gemini готов",
            ComponentState.STARTING: "Gemini запускается",
            ComponentState.STOPPING: "Gemini останавливается",
            ComponentState.RECOVERING: "Gemini восстанавливается",
            ComponentState.DEGRADED: "relay включён, ответ не подтверждён",
            ComponentState.ERROR: "Ошибка Gemini relay",
            ComponentState.STOPPED: "Gemini relay выключен",
        }
        return "Gemini — " + labels.get(self.orchestrator.ag_unlocker.state, "состояние неизвестно")

    @staticmethod
    def _notify(icon, message: str, title: str) -> None:
        """Show a short Windows tray balloon without activating another window."""
        try:
            if icon is not None:
                icon.notify(message, title)
        except Exception:
            logger.debug("Tray balloon could not be shown", exc_info=True)

    def _notification_icon(self, component: str):
        if component == "happ":
            return getattr(self, "_happ_icon", None) or getattr(self, "_icon", None)
        return getattr(self, "_gemini_icon", None) or getattr(self, "_icon", None)

    def _vpn_name(self) -> str:
        return "Mihomo"

    def _setup_gemini_icon(self, icon) -> None:
        """Show the second icon and confirm the tray app has started."""
        icon.visible = True
        happ_key = self._hotkey_choices["happ"].label
        gemini_key = self._hotkey_choices["gemini"].label
        dns_key = self._hotkey_choices["dns"].label
        self._notify(icon, f"{happ_key} — VPN · {dns_key} — DNS · {gemini_key} — relay", "Relay Studio запущен")

    # ── Menu ──────────────────────────────────────────────────────────────────

    def _build_happ_menu(self):
        import pystray

        def on_toggle(icon, item):
            self._schedule_toggle_happ()

        items = [
            pystray.MenuItem(lambda item: self._happ_label(), on_toggle),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Горячая клавиша VPN", self._shortcut_menu("happ")),
            pystray.Menu.SEPARATOR,
        ]
        if self.on_open_dashboard:
            items.append(pystray.MenuItem("Открыть Relay Studio",
                                          lambda icon, item: self.on_open_dashboard()))
        items.append(pystray.MenuItem("Выход из Relay Studio", lambda icon, item: self._action_exit()))
        return pystray.Menu(*items)

    def _build_gemini_menu(self):
        import pystray

        def on_toggle(icon, item):
            self._schedule_toggle_ag()

        items = [
            pystray.MenuItem(lambda item: self._ag_label(), on_toggle),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Gemini Web DNS", lambda icon, item: self._schedule_toggle_dns()),
            pystray.MenuItem("Клавиша Gemini Web", self._shortcut_menu("dns")),
            pystray.MenuItem("Клавиша Antigravity", self._shortcut_menu("gemini")),
            pystray.Menu.SEPARATOR,
        ]
        if self.on_open_dashboard:
            items.append(pystray.MenuItem("Открыть Relay Studio",
                                          lambda icon, item: self.on_open_dashboard()))
        items.append(pystray.MenuItem("Выход из Relay Studio", lambda icon, item: self._action_exit()))
        return pystray.Menu(*items)

    def _shortcut_menu(self, component: str):
        import pystray

        default = HotkeyChoice({"happ": VK_H, "dns": VK_D, "gemini": VK_G}[component],
                               SHORTCUT_MODIFIERS)
        legacy = HotkeyChoice({"happ": VK_F8, "dns": VK_F10, "gemini": VK_F9}[component])
        return pystray.Menu(
            pystray.MenuItem("Нажать нужную клавишу (10 секунд)",
                             lambda icon, item: self._capture_shortcut(component)),
            pystray.MenuItem(default.label,
                             lambda icon, item: self._set_shortcut(component, default),
                             checked=lambda item: self._hotkey_choices[component] == default),
            pystray.MenuItem(legacy.label,
                             lambda icon, item: self._set_shortcut(component, legacy),
                             checked=lambda item: self._hotkey_choices[component] == legacy),
        )

    def _capture_shortcut(self, component: str):
        if not self._hotkey_change_lock.acquire(blocking=False):
            self._notify(self._notification_icon(component), "Назначение клавиши уже идёт.", "Relay Studio")
            return

        def worker():
            self._capture_active = True
            try:
                self._notify(self._notification_icon(component),
                             "Нажмите нужную клавишу сейчас. Esc — отмена.", "Назначение клавиши")
                choice = NextKeyCapture().capture(timeout=10)
                if choice is None:
                    self._notify(self._notification_icon(component),
                                 "Клавиша не обнаружена или назначение отменено.", "Relay Studio")
                else:
                    self._set_shortcut_locked(component, choice)
            except Exception:
                logger.exception("Could not capture shortcut")
                self._notify(self._notification_icon(component), "Ошибка распознавания клавиши.", "Relay Studio")
            finally:
                self._capture_active = False
                self._hotkey_change_lock.release()

        threading.Thread(target=worker, name="ShortcutCapture", daemon=True).start()

    def _set_shortcut(self, component: str, choice: HotkeyChoice):
        if not self._hotkey_change_lock.acquire(blocking=False):
            self._notify(self._notification_icon(component), "Назначение клавиши уже идёт.", "Relay Studio")
            return
        try:
            self._set_shortcut_locked(component, choice)
        finally:
            self._hotkey_change_lock.release()

    def _set_shortcut_locked(self, component: str, choice: HotkeyChoice):
        if choice == self._hotkey_choices[component]:
            return
        if choice in (value for name, value in self._hotkey_choices.items() if name != component):
            self._notify(self._notification_icon(component), "Эта клавиша уже назначена другой функции.", "Relay Studio")
            return
        callback = {"happ": self._on_happ_shortcut, "dns": self._on_dns_shortcut,
                    "gemini": self._on_gemini_shortcut}[component]
        candidate = GlobalHotkey(choice.vk, callback, choice.modifiers)
        if not candidate.start():
            self._notify(self._notification_icon(component),
                         f"Windows не разрешила назначить {choice.label}. Старая клавиша работает.", "Relay Studio")
            return
        updated = dict(self._hotkey_choices)
        updated[component] = choice
        try:
            save_hotkey_choices(updated)
        except OSError:
            candidate.stop()
            logger.exception("Could not save shortcut")
            self._notify(self._notification_icon(component), "Не удалось сохранить клавишу.", "Relay Studio")
            return
        old = {"happ": self._hotkey_happ, "dns": self._hotkey_dns,
               "gemini": self._hotkey_ag}[component]
        if component == "happ":
            self._hotkey_happ = candidate
            self._happ_hotkey_registered = True
        elif component == "dns":
            self._hotkey_dns = candidate
            self._dns_hotkey_registered = True
        else:
            self._hotkey_ag = candidate
            self._gemini_hotkey_registered = True
        self._hotkey_choices = updated
        old.stop()
        self._update_icon()
        self._notify(self._notification_icon(component), f"Назначено: {choice.label}", "Relay Studio")

    # ── Hotkey handlers ───────────────────────────────────────────────────────

    def _on_happ_shortcut(self):
        if self._capture_active:
            return
        logger.info("Global %s hotkey received → toggle VPN", self._hotkey_choices["happ"].label)
        self._notify(self._notification_icon("happ"), "Переключаю VPN…", "VPN")
        self._schedule_toggle_happ()

    def _on_gemini_shortcut(self):
        if self._capture_active:
            return
        logger.info("Global %s hotkey received → toggle AG Unlocker", self._hotkey_choices["gemini"].label)
        self._notify(self._notification_icon("gemini"), "Переключаю Gemini relay…", "Gemini")
        self._schedule_toggle_ag()

    def _on_dns_shortcut(self):
        if self._capture_active:
            return
        logger.info("Global %s hotkey received → toggle Gemini Web DNS",
                    self._hotkey_choices["dns"].label)
        self._schedule_toggle_dns()

    def _schedule_toggle_dns(self):
        if self.on_toggle_dns is None:
            self._notify(self._notification_icon("gemini"),
                         "Откройте окно Relay Studio для настройки Gemini Web DNS.", "Gemini Web")
            return
        self.on_toggle_dns()

    # ── Toggle helpers ────────────────────────────────────────────────────────

    def _schedule_toggle_happ(self, *, best_foreign: bool = False):
        """Schedule the selected VPN backend in a background thread."""
        component = self.orchestrator.happ
        vpn_name = self._vpn_name()
        backend = getattr(getattr(self, "config", None), "vpn_backend", "happ")
        if backend == "mihomo":
            observed = component.read_status(with_external_probe=False)
            connect_requested = not observed.route.through_happ
            route_alias = (observed.route.interface_alias or "").casefold()
            if connect_requested and route_alias.startswith("happ-"):
                message = ("Сейчас подключён другой VPN. Отключите его перед запуском Mihomo; "
                           "Relay Studio не трогает чужой туннель.")
                component.last_error = message
                component.set_state(ComponentState.ERROR)
                logger.info("Mihomo start blocked by active external VPN route")
                self._notify(self._notification_icon("happ"), message, "Mihomo — ошибка")
                return
            if hasattr(component, "observe"):
                component.observe(
                    ComponentState.RUNNING if observed.connected else
                    ComponentState.DEGRADED if observed.route.through_happ else
                    ComponentState.STOPPED,
                    preserve_transition=True,
                )
        else:
            connect_requested = component.state not in (ComponentState.RUNNING, ComponentState.DEGRADED)
        needs_elevation = (
            backend == "mihomo" and not is_admin()
            and (connect_requested or component.ownership == ComponentOwnership.SUITE)
        )
        if needs_elevation:
            elevated, result = relaunch_vpn_elevated(
                connect=connect_requested, best_foreign=best_foreign,
            )
            if elevated:
                self._notify(self._notification_icon("happ"),
                             "Подтвердите запрос UAC. Suite перезапустится и выполнит команду VPN.", "VPN")
                self._action_exit()
            else:
                logger.warning("Could not start elevated Suite (ShellExecute result %s)", result)
                self._notify(self._notification_icon("happ"),
                             "Для системного TUN нужно подтвердить UAC и перезапустить Suite.", "VPN")
            return
        if not self._action_lock.acquire(blocking=False):
            # If we're in the middle of starting, allow cancel
            if self.orchestrator.happ.state == ComponentState.STARTING:
                self.orchestrator.cancel_start()
                self._notify(self._notification_icon("happ"), "Отменяю подключение…", vpn_name)
            else:
                self._notify(self._notification_icon("happ"), "Подождите: операция ещё выполняется.", vpn_name)
            return

        def do_toggle():
            try:
                ok = self.orchestrator.toggle_happ()
                if not ok:
                    logger.error("%s VPN toggle did not reach the requested state", vpn_name)
                    reason = getattr(component, "last_error", None)
                    self._notify(self._notification_icon("happ"),
                                 reason or "Не удалось переключить VPN. Подробности в журнале.",
                                 f"{vpn_name} — ошибка")
                elif (getattr(self.orchestrator.happ, "ownership", ComponentOwnership.UNKNOWN)
                      != ComponentOwnership.SUITE
                      and getattr(getattr(self.orchestrator.happ, "desired_state", None), "value", None) == "off"
                      and self.orchestrator.happ.state in (ComponentState.RUNNING, ComponentState.DEGRADED)):
                    self._notify(self._notification_icon("happ"),
                                 "Внешний туннель активен. Suite оставил его без изменений.", "VPN")
                elif self.orchestrator.happ.state == ComponentState.RUNNING:
                    self._notify(self._notification_icon("happ"), "VPN подключён.", vpn_name)
                elif self.orchestrator.happ.state == ComponentState.STOPPED:
                    self._notify(self._notification_icon("happ"), "VPN отключён.", vpn_name)
                else:
                    self._notify(self._notification_icon("happ"), "Состояние VPN не подтверждено.", vpn_name)
                self.health_monitor.start()
            except Exception:
                logger.exception("%s VPN toggle failed", vpn_name)
                self.orchestrator.happ.set_state(ComponentState.ERROR)
            finally:
                self._action_lock.release()
                self._update_icon()

        threading.Thread(target=do_toggle, name="VpnToggle", daemon=True).start()

    def _schedule_toggle_ag(self):
        """Schedule AG Unlocker toggle in a background thread."""
        if not self._action_lock.acquire(blocking=False):
            logger.info("Action already in progress — AG Unlocker toggle skipped")
            self._notify(self._notification_icon("gemini"), "Подождите: операция ещё выполняется.", "Gemini")
            return

        def do_toggle():
            try:
                ok = self.orchestrator.toggle_ag_unlocker()
                if not ok:
                    logger.error("AG Unlocker toggle did not reach the requested state")
                    self._notify(self._notification_icon("gemini"), "Не удалось переключить relay. Подробности в журнале.", "Gemini — ошибка")
                elif self.orchestrator.ag_unlocker.state == ComponentState.STOPPED:
                    self._notify(self._notification_icon("gemini"), "Gemini relay выключен.", "Gemini")
                elif self.orchestrator.ag_unlocker.state == ComponentState.RUNNING:
                    self._notify(self._notification_icon("gemini"), "Gemini готов.", "Gemini")
                else:
                    self._notify(self._notification_icon("gemini"), "Relay запущен; ответ Gemini пока не подтверждён.", "Gemini")
                self.health_monitor.start()
            except Exception:
                logger.exception("AG Unlocker toggle failed")
                self.orchestrator.ag_unlocker.set_state(ComponentState.ERROR)
            finally:
                self._action_lock.release()
                self._update_icon()

        threading.Thread(target=do_toggle, name="AGToggle", daemon=True).start()

    # ── Legacy helpers kept for health_monitor wiring ─────────────────────────

    def _action_start_all(self):
        """Start all components (used by health monitor recovery path)."""
        logger.info("User action: Enable VPN and Antigravity Unlocker")
        success = self.orchestrator.start_all()
        if not success:
            logger.error("Relay Studio could not enable every requested component")
        self.health_monitor.start()
        self._update_icon()
        return success

    def _action_stop_all(self):
        """Stop all components."""
        logger.info("User action: Stop All")
        disconnected = self.orchestrator.stop_all()
        self.health_monitor.start()
        self._update_icon()
        return disconnected

    def _action_exit(self):
        """Exit the tray application."""
        logger.info("User action: Exit")
        self.health_monitor.stop()
        for icon in self._icons:
            try:
                icon.stop()
            except Exception:
                logger.debug("Could not stop tray icon", exc_info=True)
        if self.on_exit:
            self.on_exit()

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self):
        """Run the tray application (blocks until exit)."""
        import pystray

        self._happ_icon = pystray.Icon(
            name="MihomoVPN",
            icon=_create_icon_image("gray", "V"),
            title="VPN выключен",
            menu=self._build_happ_menu(),
        )
        self._gemini_icon = pystray.Icon(
            name="GeminiAG",
            icon=_create_icon_image("gray", "G"),
            title="Gemini — relay выключен",
            menu=self._build_gemini_menu(),
        )
        self._icon = self._happ_icon  # Backward-compatible internal alias.
        self._icons = [self._happ_icon, self._gemini_icon]

        self.orchestrator.refresh_status()

        # Register both hotkeys
        self._happ_hotkey_registered = self._hotkey_happ.start()
        happ_key = self._hotkey_choices["happ"].label
        if self._happ_hotkey_registered:
            logger.info("Global %s hotkey registered (Happ VPN toggle)", happ_key)
        else:
            logger.error("%s not registered; it may already be used by another application", happ_key)

        self._gemini_hotkey_registered = self._hotkey_ag.start()
        gemini_key = self._hotkey_choices["gemini"].label
        if self._gemini_hotkey_registered:
            logger.info("Global %s hotkey registered (AG Unlocker toggle)", gemini_key)
        else:
            logger.error("%s not registered; it may already be used by another application", gemini_key)

        self._dns_hotkey_registered = self._hotkey_dns.start()
        dns_key = self._hotkey_choices["dns"].label
        if self._dns_hotkey_registered:
            logger.info("Global %s hotkey registered (Gemini Web DNS toggle)", dns_key)
        else:
            logger.error("%s not registered; it may already be used by another application", dns_key)

        # Continue observing externally started components while Suite is idle.
        self.health_monitor.start()

        self._update_icon()

        # Windows supports separate notification icons with independent loops.
        self._gemini_icon.run_detached(setup=self._setup_gemini_icon)
        try:
            self._happ_icon.run()
        finally:
            self._hotkey_happ.stop()
            self._hotkey_ag.stop()
            self._hotkey_dns.stop()
            self.health_monitor.stop()
            for icon in self._icons:
                try:
                    icon.stop()
                except Exception:
                    logger.debug("Could not stop tray icon", exc_info=True)
