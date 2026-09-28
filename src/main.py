"""
Relay Studio — main entry point.
Launches the orchestrator, health monitor, and system tray UI.
"""
import ctypes
import logging
import os
import sys

# Configure UTF-8 for console output on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# The onedir package may be installed in a read-only application directory.
LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "HappSuite", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log_handlers = [logging.FileHandler(os.path.join(LOG_DIR, "happ_suite.log"), encoding="utf-8")]
if sys.stdout is not None:
    log_handlers.append(logging.StreamHandler(sys.stdout))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=log_handlers,
)
logger = logging.getLogger("happ_suite")


def _single_instance_handle():
    """Keep one tray/hotkey owner per Windows user session."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, True, r"Local\HappSuiteTray")
    if not handle:
        raise OSError("Cannot create Relay Studio instance mutex")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        if "--elevated-vpn" in sys.argv or "--elevated-vpn-stop" in sys.argv:
            # The unelevated tray exits after UAC succeeds. Wait for its mutex
            # to be released before the elevated tray takes ownership.
            kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            kernel32.WaitForSingleObject.restype = ctypes.c_uint32
            wait_result = kernel32.WaitForSingleObject(handle, 30000)
            if wait_result in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
                return handle
        kernel32.CloseHandle(handle)
        return None
    return handle


def _release_single_instance(handle):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.ReleaseMutex(handle)
    kernel32.CloseHandle(handle)


def is_admin() -> bool:
    """Check if running with admin privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def request_admin_restart():
    """Re-launch the current script with admin privileges."""
    if is_admin():
        return
    logger.info("Requesting admin privileges...")
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, " ".join(sys.argv), None, 1
        )
        sys.exit(0)
    except Exception as e:
        logger.error(f"Failed to elevate: {e}")


def main():
    """Main entry point."""
    mutex = _single_instance_handle()
    if mutex is None:
        logger.info("Relay Studio is already running in this Windows session")
        return
    logger.info("=" * 60)
    logger.info("Relay Studio v2.0 starting...")
    logger.info(f"Admin: {is_admin()}")
    logger.info(f"Python: {sys.version}")
    logger.info(f"CWD: {os.getcwd()}")
    logger.info("=" * 60)

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception

    # Ensure current directory is in sys.path
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)

    # Import after logging is configured
    try:
        from .app_config import Config
        from .core import Orchestrator
        from .health import HealthMonitor
        from .tray import TrayApp
    except (ImportError, ValueError):
        from app_config import Config
        from core import Orchestrator
        from health import HealthMonitor
        from tray import TrayApp

    # Load config
    config = Config()
    logger.info(f"Config: {config}")

    # Create orchestrator
    orchestrator = Orchestrator(config)

    if "--elevated-vpn" in sys.argv and config.vpn_backend == "mihomo":
        logger.info("Elevated restart requested the Mihomo connection")
        if not orchestrator._start_happ_only():
            logger.error("Mihomo did not reach a verified connected state")
    elif "--elevated-vpn-stop" in sys.argv and config.vpn_backend == "mihomo":
        logger.info("Elevated restart requested the Mihomo disconnection")
        if not orchestrator._stop_happ_only():
            logger.error("Mihomo stop or route recovery was not verified")

    # Create health monitor
    health_monitor = HealthMonitor(orchestrator, config)

    # Create and run tray app (blocks until exit)
    tray = TrayApp(orchestrator, config, health_monitor)
    try:
        tray.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        health_monitor.stop()
        logger.info("Relay Studio stopped")
        _release_single_instance(mutex)


if __name__ == "__main__":
    main()
