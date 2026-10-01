"""Windowed Relay Studio entry point. Closing the window keeps tray controls alive."""

import argparse
import ctypes
import logging
import os
from pathlib import Path
import sys
import threading

try:
    from .app_config import Config
    from .core import Orchestrator
    from .health import HealthMonitor
    from .main import _single_instance_handle, _release_single_instance
    from .tray import TrayApp
except ImportError:
    from app_config import Config
    from core import Orchestrator
    from health import HealthMonitor
    from main import _single_instance_handle, _release_single_instance
    from tray import TrayApp


SHOW_EVENT_NAME = r"Local\HappSuiteShowDashboard"
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0


def enable_high_dpi_awareness():
    """Opt into native per-monitor rendering before creating windows."""
    if os.name != "nt":
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.argtypes = (ctypes.c_void_p,)
        set_context.restype = ctypes.c_bool
        if set_context(ctypes.c_void_p(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            return
    except (AttributeError, OSError):
        pass
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _create_show_event():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateEventW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_wchar_p)
    kernel.CreateEventW.restype = ctypes.c_void_p
    handle = kernel.CreateEventW(None, False, False, SHOW_EVENT_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _show_existing():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenEventW.argtypes = (ctypes.c_uint, ctypes.c_bool, ctypes.c_wchar_p)
    kernel.OpenEventW.restype = ctypes.c_void_p
    handle = kernel.OpenEventW(EVENT_MODIFY_STATE, False, SHOW_EVENT_NAME)
    if handle:
        kernel.SetEvent(ctypes.c_void_p(handle))
        kernel.CloseHandle(ctypes.c_void_p(handle))


def main(argv=None):
    enable_high_dpi_awareness()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background", action="store_true", help="start in the tray after Windows sign-in")
    parser.add_argument("--self-check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--bootstrap-mihomo", action="store_true",
                        help="install or verify Mihomo without starting VPN/TUN")
    parser.add_argument("--elevated-vpn", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--elevated-vpn-stop", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--best-foreign", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--gemini-dns-helper", choices=("enable", "disable"), help=argparse.SUPPRESS)
    parser.add_argument("--dns-result", help=argparse.SUPPRESS)
    parser.add_argument("--ui-preview", action="store_true", help="show offline Qt UI without tray or network commands")
    parser.add_argument("--preview-page", default="vpn", choices=("vpn", "google", "ag", "settings"), help=argparse.SUPPRESS)
    parser.add_argument("--preview-screenshot", help=argparse.SUPPRESS)
    parser.add_argument("--preview-quit-after", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.ui_preview:
        from .ui_qt.preview import main as preview_main
        preview_args = ["--page", args.preview_page, "--quit-after", str(args.preview_quit_after)]
        if args.preview_screenshot:
            preview_args += ["--screenshot", args.preview_screenshot]
        return preview_main(preview_args)
    if args.self_check:
        if not getattr(sys, "frozen", False):
            parser.error("--self-check is available only in the packaged application")
        try:
            try:
                from .package_inspection import inspect_package_tree
            except ImportError:
                from package_inspection import inspect_package_tree
            inspect_package_tree(Path(sys.executable).resolve().parent)
        except (OSError, ValueError) as exc:
            logging.getLogger("happ_suite.start").error("Packaged application self-check failed: %s", exc)
            raise SystemExit(1) from exc
        return
    if args.bootstrap_mihomo:
        try:
            from .components.mihomo import bootstrap_mihomo
        except ImportError:
            from components.mihomo import bootstrap_mihomo
        raise SystemExit(bootstrap_mihomo())
    if args.gemini_dns_helper:
        if not args.dns_result:
            parser.error("--dns-result is required for the DNS helper")
        try:
            from .gemini_dns import helper_main
        except ImportError:
            from gemini_dns import helper_main
        raise SystemExit(helper_main(args.gemini_dns_helper, args.dns_result))
    mutex = _single_instance_handle()
    if mutex is None:
        if not args.background:
            _show_existing()
        return

    show_event = None
    root = None
    tray = None
    try:
        show_event = _create_show_event()
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from .ui_qt.controller import Controller
        from .ui_qt.main_window import MainWindow
        from .ui_qt.theme import apply_dark_theme

        app = QApplication.instance() or QApplication([sys.argv[0]])
        app.setApplicationName("Relay Studio")
        app.setQuitOnLastWindowClosed(False)
        apply_dark_theme(app)
        config = Config()
        orchestrator = Orchestrator(config)
        health = HealthMonitor(orchestrator, config)

        tray = TrayApp(orchestrator, config, health)
        controller = Controller(tray, config)
        root = MainWindow(controller)
        controller.show_requested.connect(root.show_window)
        controller.quit_requested.connect(app.quit)
        tray.on_open_dashboard = controller.show_requested.emit
        tray.on_exit = controller.quit_requested.emit
        tray.on_toggle_dns = controller.dns_requested.emit
        app.aboutToQuit.connect(controller.close)
        controller.start()
        if not args.background:
            root.show()
        if (args.elevated_vpn or args.elevated_vpn_stop) and config.vpn_backend == "mihomo":
            def elevated_action():
                with tray._action_lock:
                    ok = (orchestrator._start_happ_only() if args.elevated_vpn
                          else orchestrator._stop_happ_only())
                    if not ok:
                        from .vpn_backend import NetworkUnavailableError
                        raise NetworkUnavailableError(orchestrator.vpn.last_error or "Операция VPN не подтверждена")
                    health.start()
                if args.best_foreign and args.elevated_vpn:
                    controller.best_requested.emit()
            QTimer.singleShot(0, lambda: controller._run("elevated", elevated_action))
        elif args.best_foreign:
            QTimer.singleShot(500, controller.choose_best)

        def run_tray():
            try:
                tray.run()
            except Exception:
                logging.getLogger("happ_suite.start").exception("Tray failed")
                controller.message.emit("Трей не запустился: см. журнал")

        threading.Thread(target=run_tray, name="HappSuiteTray", daemon=True).start()

        def check_show_request():
            if ctypes.windll.kernel32.WaitForSingleObject(ctypes.c_void_p(show_event), 0) == WAIT_OBJECT_0:
                root.show_window()

        show_timer = QTimer(root)
        show_timer.timeout.connect(check_show_request)
        show_timer.start(250)
        app.exec()
    finally:
        if tray is not None:
            try:
                tray.on_exit = None
                tray._action_exit()
            except Exception:
                logging.getLogger("happ_suite.start").exception("Tray cleanup failed")
        if show_event:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(show_event))
        _release_single_instance(mutex)


if __name__ == "__main__":
    main()
