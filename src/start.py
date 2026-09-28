"""Windowed Happ Suite entry point. Closing the window keeps tray controls alive."""

import argparse
import ctypes
import logging
import threading
import tkinter as tk

try:
    from .app_config import Config
    from .core import Orchestrator
    from .dashboard import Dashboard
    from .health import HealthMonitor
    from .main import _single_instance_handle, _release_single_instance
    from .tray import TrayApp
except ImportError:
    from app_config import Config
    from core import Orchestrator
    from dashboard import Dashboard
    from health import HealthMonitor
    from main import _single_instance_handle, _release_single_instance
    from tray import TrayApp


SHOW_EVENT_NAME = r"Local\HappSuiteShowDashboard"
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background", action="store_true", help="start in the tray after Windows sign-in")
    args = parser.parse_args()
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
        root = tk.Tk()
        if args.background:
            root.withdraw()
        config = Config()
        orchestrator = Orchestrator(config)
        health = HealthMonitor(orchestrator, config)

        def show_window():
            root.after(0, lambda: (root.deiconify(), root.lift()))

        def exit_window():
            root.after(0, root.destroy)

        tray = TrayApp(orchestrator, config, health, on_open_dashboard=show_window,
                       on_exit=exit_window)
        dashboard = Dashboard(root, tray, config)

        def run_tray():
            try:
                tray.run()
            except Exception:
                logging.getLogger("happ_suite.start").exception("Tray failed")
                dashboard.set_message("Трей не запустился: см. журнал")

        threading.Thread(target=run_tray, name="HappSuiteTray", daemon=True).start()

        def check_show_request():
            if ctypes.windll.kernel32.WaitForSingleObject(ctypes.c_void_p(show_event), 0) == WAIT_OBJECT_0:
                root.deiconify()
                root.lift()
            root.after(250, check_show_request)

        root.after(250, check_show_request)
        root.mainloop()
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
