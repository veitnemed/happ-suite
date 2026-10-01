"""Offline UI preview. Does not instantiate Config, runtime, tray or hotkeys."""
import argparse
import sys

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QApplication

from ..core import ComponentState
from ..components import ComponentInstallation, ComponentStatus
from .main_window import MainWindow
from .theme import apply_dark_theme


class PreviewController(QObject):
    message = Signal(str)
    changed = Signal(object)
    busy_changed = Signal(str, bool)
    subscription_loaded = Signal(str)

    def __init__(self, scenario="default"):
        super().__init__()
        self.snapshot = {
            "vpn_state": ComponentState.ERROR if scenario == "error" else ComponentState.STARTING if scenario == "loading" else ComponentState.STOPPED,
            "vpn_error": "Провайдер недоступен. Проверьте подключение и повторите запрос." if scenario == "error" else None,
            "ag_state": ComponentState.DEGRADED, "ownership": "unknown",
            "nodes": [] if scenario == "empty" else [
                {"name": "Netherlands · Amsterdam", "type": "vless"},
                {"name": "Germany · Frankfurt", "type": "vless"},
                {"name": "Finland · Helsinki — a deliberately long server name for layout verification", "type": "trojan"},
                {"name": "United Kingdom · London", "type": "vless"}],
            "preferred_node": "Netherlands · Amsterdam", "dns": {"managed": False, "message": "Используется DNS текущей сети."},
            "hotkeys": {"happ": "Ctrl+Alt+H", "dns": "Ctrl+Alt+D", "gemini": "Ctrl+Alt+G"},
            "installations": {key: ComponentInstallation(key, name, False, None, True, False, ComponentStatus.NOT_INSTALLED)
                              for key, name in (("mihomo", "Mihomo"), ("ag_unlocker", "AG Unlocker"),
                                                ("vscode", "Visual Studio Code"), ("google_antigravity", "Google Antigravity"))},
            "busy": {"nodes"} if scenario == "loading" else set(),
        }

    def _noop(self, *args):
        self.message.emit("Предпросмотр: системные команды отключены.")

    toggle_vpn = toggle_ag = toggle_dns = save_subscription = refresh_nodes = apply_node = _noop
    choose_best = check_gemini = component_action = capture_hotkey = set_autostart = open_log = set_hotkey = load_subscription = _noop


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", choices=("vpn", "google", "ag", "settings"), default="vpn")
    parser.add_argument("--scenario", choices=("default", "empty", "error", "loading"), default="default")
    parser.add_argument("--screenshot")
    parser.add_argument("--quit-after", type=int, default=0, help="milliseconds")
    parser.add_argument("--width", type=int, default=1160)
    parser.add_argument("--height", type=int, default=800)
    args = parser.parse_args(argv)
    app = QApplication([sys.argv[0]])
    apply_dark_theme(app)
    controller = PreviewController(args.scenario)
    window = MainWindow(controller, preview=True)
    available = window.screen().availableGeometry()
    window.resize(min(args.width, available.width()), min(args.height, available.height() - 40))
    window.render(controller.snapshot)
    window.show_page(args.page)
    window.show()
    if args.screenshot:
        QTimer.singleShot(700, lambda: window.grab().save(args.screenshot))
    if args.quit_after:
        QTimer.singleShot(args.quit_after, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
