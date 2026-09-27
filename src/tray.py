"""
System Tray UI for Happ Suite.
Shows 🟢/🟡/🔴 icon + context menu with controls.
"""
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Optional

from PIL import Image, ImageDraw

from .config import Config
from .core import Orchestrator, ComponentState
from .health import HealthMonitor, HealthStatus
from . import server_picker

logger = logging.getLogger("happ_suite.tray")


def _create_icon_image(color: str) -> Image.Image:
    """Create a colored circle icon for the system tray."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    colors = {
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
        self._current_color = "yellow"

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
        elif state == ComponentState.ERROR:
            new_color = "red"
        elif state == ComponentState.STOPPED:
            new_color = "red"
        else:
            new_color = "yellow"

        if new_color != self._current_color and self._icon:
            self._current_color = new_color
            self._icon.icon = _create_icon_image(new_color)
            self._icon.title = self._get_tooltip()

    def _get_tooltip(self) -> str:
        """Generate tooltip text."""
        state = self.orchestrator.overall_state
        status_map = {
            ComponentState.RUNNING: "🟢 Работает",
            ComponentState.STARTING: "🟡 Запуск...",
            ComponentState.RECOVERING: "🟡 Восстановление...",
            ComponentState.ERROR: "🔴 Ошибка",
            ComponentState.STOPPED: "🔴 Выключен",
        }
        status_text = status_map.get(state, "Неизвестно")

        health = self.health_monitor.last_status
        if health and health.tunnel_up:
            return f"Happ Suite: {status_text} | {health.current_server}"
        return f"Happ Suite: {status_text}"

    def _build_menu(self):
        """Build the context menu."""
        import pystray

        def on_start_all(icon, item):
            threading.Thread(target=self._action_start_all, daemon=True).start()

        def on_stop_all(icon, item):
            threading.Thread(target=self._action_stop_all, daemon=True).start()

        def on_restart_vpn(icon, item):
            threading.Thread(target=self._action_restart_vpn, daemon=True).start()

        def on_open_dashboard(icon, item):
            self._action_open_dashboard()

        def on_exit(icon, item):
            self._action_exit()

        # Server submenu
        server_items = []
        for srv in self.config.servers:
            label = srv["label"]
            def make_handler(s=srv):
                def handler(icon, item):
                    threading.Thread(target=lambda: self._action_switch_server(s), daemon=True).start()
                return handler
            server_items.append(pystray.MenuItem(label, make_handler()))

        status = self.orchestrator.get_status()
        status_text = " | ".join(f"{k}: {v}" for k, v in status.items())

        menu = pystray.Menu(
            pystray.MenuItem(f"Happ Suite v2.0", None, enabled=False),
            pystray.MenuItem(status_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("▶️ Запустить всё", on_start_all),
            pystray.MenuItem("⏹️ Остановить всё", on_stop_all),
            pystray.MenuItem("🔄 Переподключить VPN", on_restart_vpn),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🌐 Сменить сервер", pystray.Menu(*server_items)),
            pystray.MenuItem("📊 Открыть дашборд", on_open_dashboard),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", on_exit),
        )
        return menu

    def _action_start_all(self):
        """Start all components with auto-server selection."""
        logger.info("User action: Start All")
        results = server_picker.ping_all_servers(self.config)
        best = server_picker.pick_best_server(results)
        self.orchestrator.start_all(target_server=best)

    def _action_stop_all(self):
        """Stop all components."""
        logger.info("User action: Stop All")
        self.orchestrator.stop_all()

    def _action_restart_vpn(self):
        """Restart VPN with auto-server selection."""
        logger.info("User action: Restart VPN")
        results = server_picker.ping_all_servers(self.config)
        best = server_picker.pick_best_server(results)
        self.orchestrator.restart_vpn(server=best)

    def _action_switch_server(self, server):
        """Switch to a specific server."""
        logger.info(f"User action: Switch to {server['label']}")
        self.orchestrator.happ.switch_server(server)

    def _action_open_dashboard(self):
        """Open CLI dashboard in a new console window."""
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dashboard_path = os.path.join(script_dir, "src", "dashboard.py")
        if os.path.exists(dashboard_path):
            subprocess.Popen(
                [sys.executable, dashboard_path],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )

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
            icon=_create_icon_image("yellow"),
            title="Happ Suite: Запуск...",
            menu=self._build_menu(),
        )

        # Start components in background
        def startup():
            time.sleep(1)
            self._action_start_all()
            self.health_monitor.start()

        startup_thread = threading.Thread(target=startup, daemon=True)
        startup_thread.start()

        # This blocks until icon.stop() is called
        self._icon.run()
