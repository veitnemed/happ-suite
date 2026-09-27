"""
Happ Suite — main entry point.
Launches the orchestrator, health monitor, and system tray UI.
"""
import ctypes
import logging
import os
import sys

# Setup logging first
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "happ_suite.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("happ_suite")


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
    logger.info("=" * 60)
    logger.info("Happ Suite v2.0 starting...")
    logger.info(f"Admin: {is_admin()}")
    logger.info(f"Python: {sys.version}")
    logger.info(f"CWD: {os.getcwd()}")
    logger.info("=" * 60)

    # Import after logging is configured
    from .config import Config
    from .core import Orchestrator
    from .health import HealthMonitor
    from .tray import TrayApp

    # Load config
    config = Config()
    logger.info(f"Config: {config}")

    # Create orchestrator
    orchestrator = Orchestrator(config)

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
        logger.info("Happ Suite stopped")


if __name__ == "__main__":
    main()
