"""Import and launcher syntax smoke tests; never start UI or touch system state."""

import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_core_modules_import_without_starting_the_application():
    modules = (
        "src.start",
        "src.dashboard",
        "src.mihomo_backend",
        "src.mihomo_subscription",
        "src.mihomo_installer",
        "src.official_installers",
        "src.components",
        "src.components.vscode",
        "src.components.antigravity",
        "src.components.windows_file_version",
        "src.gemini_availability",
        "src.ui_model",
    )
    for name in modules:
        importlib.import_module(name)


def test_python_launchers_are_valid_without_executing_them():
    for name in ("start.py", "start.pyw"):
        path = ROOT / name
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
