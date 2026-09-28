"""
System Tray UI for Happ Suite.
Shows 🟢/🟡/🔴 icon + context menu with controls.
"""
import logging
import threading
from typing import Optional

from PIL import Image, ImageDraw

try:
    from .app_config import Config
    from .core import Orchestrator, ComponentState
    from .health import HealthMonitor, HealthStatus
    from .hotkey import GlobalF8Hotkey
except (ImportError, ValueError):
    from app_config import Config
    from core import Orchestrator, ComponentState
    from health import HealthMonitor, HealthStatus
    from hotkey import GlobalF8Hotkey

logger = logging.getLogger("happ_suite.tray")


def _create_icon_image(color: str) -> Image.Image:
    """Create a colored circle icon for the system tray."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    colors = {
        "gray": (128, 128, 128),
        "green": (46, 204, 113),
        "yellow": (241, 196, 15),
        "red": (231, 76, 60),
    }
    rgb = colors.get(color, colors["yellow"])

    # Filled circle
    draw.ellipse([4, 4, size - 4, size - 4], fill=rgb + (255,))
    # Border
    draw.ellipse([4, 4, size - 4, size - 4], outline=(0, 0, 0, 80), width=2)

    return img


class TrayApp:
    """System tray application for Happ Suite."""

    def __init__(self, orchestrator: Orchestrator, config: Config, health_monitor: HealthMonitor):
        self.orchestrator = orchestrator
        self.config = config
        self.health_monitor = health_monitor
        self._icon = None
        self._current_color = "gray"
        self._hotkey = GlobalF8Hotkey(self._on_f8)
        self._hotkey_registered = False
        self._action_lock = threading.Lock()

        # Register callbacks
        self.orchestrator.on_state_change(self._on_component_state_change)
        self.health_monitor.on_health_update(self._on_health_update)

    def _on_component_state_change(self, name: str, old: ComponentState, new: ComponentState):
        """Called when any component changes state."""
        logger.info(f"[{name}] {old.value} → {new.value}")
        self._update_icon()

    def _on_health_update(self, status: HealthStatus):
        """Called on each health check cycle."""
        self._update_icon()

    def _update_icon(self):
        """Update the tray icon color based on overall state."""
        state = self.orchestrator.overall_state
        if state == ComponentState.RUNNING:
            new_color = "green"
        elif state in (ComponentState.STARTING, ComponentState.RECOVERING):
            new_color = "yellow"
        elif state in (ComponentState.DEGRADED, ComponentState.STOPPING):
            new_color = "yellow"
        elif state == ComponentState.ERROR:
            new_color = "red"
        elif state == ComponentState.STOPPED:
            new_color = "gray"
        else:
            new_color = "yellow"

        if new_color != self._current_color and self._icon:
            self._current_color = new_color
            self._icon.icon = _create_icon_image(new_color)
            self._icon.title = self._get_tooltip()
            self._icon.update_menu()

    def _get_tooltip(self) -> str:
        """Generate tooltip text."""
        state = self.orchestrator.overall_state
        status_map = {
            ComponentState.RUNNING: "🟢 Работает",
            ComponentState.STARTING: "🟡 Запуск...",
            ComponentState.STOPPING: "🟡 Отключение...",
            ComponentState.RECOVERING: "🟡 Восстановление...",
            ComponentState.DEGRADED: "🟡 Частично готово; полная проверка не пройдена",
            ComponentState.ERROR: "🔴 Ошибка",
            ComponentState.STOPPED: "⚪ Выключен",
        }
        status_text = status_map.get(state, "Неизвестно")
        if not self._hotkey_registered:
            status_text += " | F8 недоступна"

        health = self.health_monitor.last_status
        if health and health.tunnel_up:
            return f"Happ Suite: {status_text} | {health.current_server}"
        return f"Happ Suite: {status_text}"

    def _build_menu(self):
        """Build the context menu."""
        import pystray

        def on_toggle(icon, item):
            self._schedule_toggle()

        def on_exit(icon, item):
            self._action_exit()

        menu = pystray.Menu(
            pystray.MenuItem("Happ Suite", None, enabled=False),
            pystray.MenuItem(lambda item: self._get_tooltip(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("F8 — включить / выключить", on_toggle),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", on_exit),
        )
        return menu

    def _action_start_all(self):
        """Start all components with auto-server selection."""
        logger.info("User action: Enable VPN and Antigravity Unlocker")
        success = self.orchestrator.start_all()
        if not success:
            logger.error("Happ Suite could not enable every requested component")
        if self.orchestrator.desired_enabled:
            self.health_monitor.start()
        else:
            self.health_monitor.stop()
        self._update_icon()
        return success

    def _action_stop_all(self):
        """Stop all components."""
        logger.info("User action: Stop All")
        disconnected = self.orchestrator.stop_all()
        if disconnected:
            self.health_monitor.stop()
        self._update_icon()
        return disconnected

    def _on_f8(self):
        logger.info("Global F8 hotkey received")
        self._schedule_toggle()

    def _schedule_toggle(self):
        if not self._action_lock.acquire(blocking=False):
            if self.orchestrator.overall_state == ComponentState.STARTING:
                self.orchestrator.cancel_start()
            return

        def toggle():
            try:
                route_active = self.orchestrator.happ.controller.read_status(
                    with_external_probe=False
                ).route.through_happ
                success = self._action_stop_all() if route_active else self._action_start_all()
                if not success:
                    logger.error("F8 VPN toggle did not reach the requested state")
            except Exception:
                logger.exception("F8 toggle failed")
                for component in (self.orchestrator.happ, self.orchestrator.ag_unlocker):
                    component.set_state(ComponentState.ERROR)
            finally:
                self._action_lock.release()
                self._update_icon()

        threading.Thread(target=toggle, name="ComponentToggle", daemon=True).start()

    def _action_exit(self):
        """Exit the tray application."""
        logger.info("User action: Exit")
        self.health_monitor.stop()
        if self._icon:
            self._icon.stop()

    def run(self):
        """Run the tray application (blocks until exit)."""
        import pystray

        self._icon = pystray.Icon(
            name="HappSuite",
            icon=_create_icon_image(self._current_color),
            title=self._get_tooltip(),
            menu=self._build_menu(),
        )

        self.orchestrator.refresh_status()
        self._hotkey_registered = self._hotkey.start()
        if self._hotkey_registered:
            logger.info("Global F8 hotkey registered")
        else:
            logger.error("F8 not registered; it may already be used by another application")
        if self.orchestrator.desired_enabled:
            self.health_monitor.start()
        self._update_icon()

        # This blocks until icon.stop() is called
        try:
            self._icon.run()
        finally:
            self._hotkey.stop()
            self.health_monitor.stop()
