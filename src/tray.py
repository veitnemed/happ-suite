"""
System Tray UI for Happ Suite.
Shows 🟢/🟡/🔴 icon + context menu with two independent controls:
  • Ctrl+Alt+H — toggle Happ VPN
  • Ctrl+Alt+G — toggle AG Unlocker
"""
import logging
import threading
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

try:
    from .app_config import Config
    from .core import Orchestrator, ComponentState
    from .health import HealthMonitor, HealthStatus
    from .hotkey import (GlobalHotkey, HotkeyChoice, NextKeyCapture, VK_H, VK_G,
                         VK_F8, VK_F9, SHORTCUT_MODIFIERS, load_hotkey_choices,
                         save_hotkey_choices)
except (ImportError, ValueError):
    from app_config import Config
    from core import Orchestrator, ComponentState
    from health import HealthMonitor, HealthStatus
    from hotkey import (GlobalHotkey, HotkeyChoice, NextKeyCapture, VK_H, VK_G,
                        VK_F8, VK_F9, SHORTCUT_MODIFIERS, load_hotkey_choices,
                        save_hotkey_choices)

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
    """System tray application for Happ Suite."""

    def __init__(self, orchestrator: Orchestrator, config: Config, health_monitor: HealthMonitor,
                 on_open_dashboard=None, on_exit=None):
        self.orchestrator = orchestrator
        self.config = config
        self.health_monitor = health_monitor
        self.on_open_dashboard = on_open_dashboard
        self.on_exit = on_exit
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
        self._happ_hotkey_registered = False
        self._gemini_hotkey_registered = False
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
            (self._happ_icon, self.orchestrator.happ.state, "H", self._happ_tooltip()),
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
        return f"{em} {self._hotkey_choices['happ'].label} — Happ VPN  [{state.value}]"

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
            "Happ Suite",
            happ_names.get(happ_st, "VPN: ?"),
            ag_names.get(ag_st, "AG: ?"),
        ]
        if not self._happ_hotkey_registered:
            parts.append(f"⚠ {self._hotkey_choices['happ'].label} недоступна")
        if not self._gemini_hotkey_registered:
            parts.append(f"⚠ {self._hotkey_choices['gemini'].label} недоступна")

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
        return "HAPP — " + labels.get(self.orchestrator.happ.state, "состояние неизвестно")

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

    def _setup_gemini_icon(self, icon) -> None:
        """Show the second icon and confirm the tray app has started."""
        icon.visible = True
        happ_key = self._hotkey_choices["happ"].label
        gemini_key = self._hotkey_choices["gemini"].label
        self._notify(icon, f"{happ_key} — HAPP VPN   •   {gemini_key} — Gemini relay", "Happ Suite запущен")

    # ── Menu ──────────────────────────────────────────────────────────────────

    def _build_happ_menu(self):
        import pystray

        def on_toggle(icon, item):
            self._schedule_toggle_happ()

        items = [
            pystray.MenuItem(lambda item: self._happ_label(), on_toggle),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Горячая клавиша HAPP", self._shortcut_menu("happ")),
            pystray.Menu.SEPARATOR,
        ]
        if self.on_open_dashboard:
            items.append(pystray.MenuItem("Открыть окно Happ Suite",
                                          lambda icon, item: self.on_open_dashboard()))
        items.append(pystray.MenuItem("Выход Happ Suite", lambda icon, item: self._action_exit()))
        return pystray.Menu(*items)

    def _build_gemini_menu(self):
        import pystray

        def on_toggle(icon, item):
            self._schedule_toggle_ag()

        items = [
            pystray.MenuItem(lambda item: self._ag_label(), on_toggle),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Горячая клавиша Gemini", self._shortcut_menu("gemini")),
            pystray.Menu.SEPARATOR,
        ]
        if self.on_open_dashboard:
            items.append(pystray.MenuItem("Открыть окно Happ Suite",
                                          lambda icon, item: self.on_open_dashboard()))
        items.append(pystray.MenuItem("Выход Happ Suite", lambda icon, item: self._action_exit()))
        return pystray.Menu(*items)

    def _shortcut_menu(self, component: str):
        import pystray

        default = HotkeyChoice(VK_H if component == "happ" else VK_G, SHORTCUT_MODIFIERS)
        legacy = HotkeyChoice(VK_F8 if component == "happ" else VK_F9)
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
            self._notify(self._notification_icon(component), "Назначение клавиши уже идёт.", "Happ Suite")
            return

        def worker():
            self._capture_active = True
            try:
                self._notify(self._notification_icon(component),
                             "Нажмите нужную клавишу сейчас. Esc — отмена.", "Назначение клавиши")
                choice = NextKeyCapture().capture(timeout=10)
                if choice is None:
                    self._notify(self._notification_icon(component),
                                 "Клавиша не обнаружена или назначение отменено.", "Happ Suite")
                else:
                    self._set_shortcut_locked(component, choice)
            except Exception:
                logger.exception("Could not capture shortcut")
                self._notify(self._notification_icon(component), "Ошибка распознавания клавиши.", "Happ Suite")
            finally:
                self._capture_active = False
                self._hotkey_change_lock.release()

        threading.Thread(target=worker, name="ShortcutCapture", daemon=True).start()

    def _set_shortcut(self, component: str, choice: HotkeyChoice):
        if not self._hotkey_change_lock.acquire(blocking=False):
            self._notify(self._notification_icon(component), "Назначение клавиши уже идёт.", "Happ Suite")
            return
        try:
            self._set_shortcut_locked(component, choice)
        finally:
            self._hotkey_change_lock.release()

    def _set_shortcut_locked(self, component: str, choice: HotkeyChoice):
        if choice == self._hotkey_choices[component]:
            return
        other = "gemini" if component == "happ" else "happ"
        if choice == self._hotkey_choices[other]:
            self._notify(self._notification_icon(component), "Эта клавиша уже назначена другой функции.", "Happ Suite")
            return
        callback = self._on_happ_shortcut if component == "happ" else self._on_gemini_shortcut
        candidate = GlobalHotkey(choice.vk, callback, choice.modifiers)
        if not candidate.start():
            self._notify(self._notification_icon(component),
                         f"Windows не разрешила назначить {choice.label}. Старая клавиша работает.", "Happ Suite")
            return
        updated = dict(self._hotkey_choices)
        updated[component] = choice
        try:
            save_hotkey_choices(updated)
        except OSError:
            candidate.stop()
            logger.exception("Could not save shortcut")
            self._notify(self._notification_icon(component), "Не удалось сохранить клавишу.", "Happ Suite")
            return
        old = self._hotkey_happ if component == "happ" else self._hotkey_ag
        if component == "happ":
            self._hotkey_happ = candidate
            self._happ_hotkey_registered = True
        else:
            self._hotkey_ag = candidate
            self._gemini_hotkey_registered = True
        self._hotkey_choices = updated
        old.stop()
        self._update_icon()
        self._notify(self._notification_icon(component), f"Назначено: {choice.label}", "Happ Suite")

    # ── Hotkey handlers ───────────────────────────────────────────────────────

    def _on_happ_shortcut(self):
        if self._capture_active:
            return
        logger.info("Global %s hotkey received → toggle Happ VPN", self._hotkey_choices["happ"].label)
        self._notify(self._notification_icon("happ"), "Переключаю VPN…", "HAPP")
        self._schedule_toggle_happ()

    def _on_gemini_shortcut(self):
        if self._capture_active:
            return
        logger.info("Global %s hotkey received → toggle AG Unlocker", self._hotkey_choices["gemini"].label)
        self._notify(self._notification_icon("gemini"), "Переключаю Gemini relay…", "Gemini")
        self._schedule_toggle_ag()

    # ── Toggle helpers ────────────────────────────────────────────────────────

    def _schedule_toggle_happ(self):
        """Schedule Happ VPN toggle in a background thread."""
        if not self._action_lock.acquire(blocking=False):
            # If we're in the middle of starting, allow cancel
            if self.orchestrator.happ.state == ComponentState.STARTING:
                self.orchestrator.cancel_start()
                self._notify(self._notification_icon("happ"), "Отменяю подключение…", "HAPP")
            else:
                self._notify(self._notification_icon("happ"), "Подождите: операция ещё выполняется.", "HAPP")
            return

        def do_toggle():
            try:
                ok = self.orchestrator.toggle_happ()
                if not ok:
                    logger.error("Happ VPN toggle did not reach the requested state")
                    self._notify(self._notification_icon("happ"), "Не удалось переключить VPN. Подробности в журнале.", "HAPP — ошибка")
                elif self.orchestrator.happ.state == ComponentState.RUNNING:
                    self._notify(self._notification_icon("happ"), "VPN подключён.", "HAPP")
                elif self.orchestrator.happ.state == ComponentState.STOPPED:
                    self._notify(self._notification_icon("happ"), "VPN отключён.", "HAPP")
                else:
                    self._notify(self._notification_icon("happ"), "Состояние VPN не подтверждено.", "HAPP")
                if self.orchestrator.desired_enabled or self.orchestrator.ag_unlocker.requested_enabled:
                    self.health_monitor.start()
                else:
                    self.health_monitor.stop()
            except Exception:
                logger.exception("Happ VPN toggle failed")
                self.orchestrator.happ.set_state(ComponentState.ERROR)
            finally:
                self._action_lock.release()
                self._update_icon()

        threading.Thread(target=do_toggle, name="HappToggle", daemon=True).start()

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
                if self.orchestrator.desired_enabled or self.orchestrator.ag_unlocker.requested_enabled:
                    self.health_monitor.start()
                else:
                    self.health_monitor.stop()
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
            logger.error("Happ Suite could not enable every requested component")
        if self.orchestrator.desired_enabled or self.orchestrator.ag_unlocker.requested_enabled:
            self.health_monitor.start()
        else:
            self.health_monitor.stop()
        self._update_icon()
        return success

    def _action_stop_all(self):
        """Stop all components."""
        logger.info("User action: Stop All")
        disconnected = self.orchestrator.stop_all()
        if disconnected and not self.orchestrator.ag_unlocker.requested_enabled:
            self.health_monitor.stop()
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
            name="HappVPN",
            icon=_create_icon_image("gray", "H"),
            title="HAPP — VPN выключен",
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

        if self.orchestrator.desired_enabled or self.orchestrator.ag_unlocker.requested_enabled:
            self.health_monitor.start()

        self._update_icon()

        # Windows supports separate notification icons with independent loops.
        self._gemini_icon.run_detached(setup=self._setup_gemini_icon)
        try:
            self._happ_icon.run()
        finally:
            self._hotkey_happ.stop()
            self._hotkey_ag.stop()
            self.health_monitor.stop()
            for icon in self._icons:
                try:
                    icon.stop()
                except Exception:
                    logger.debug("Could not stop tray icon", exc_info=True)
