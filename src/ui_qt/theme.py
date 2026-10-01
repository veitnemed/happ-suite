"""Shared dark visual system for the PySide6 interface."""

import os
from pathlib import Path

from PySide6.QtGui import QColor, QPalette, QFontDatabase


COLORS = {
    "background": "#0B1016",
    "sidebar": "#101720",
    "surface": "#151E29",
    "surface_alt": "#192431",
    "field": "#0D141C",
    "text": "#EFF4F8",
    "muted": "#9AA9B8",
    "subtle": "#6F8090",
    "border": "#293746",
    "accent": "#67D8B8",
    "accent_hover": "#81E7C9",
    "accent_text": "#08231B",
    "success": "#67D8B8",
    "warning": "#EFC36F",
    "error": "#F07F84",
    "blue": "#84B4FF",
}


def apply_dark_theme(app):
    """Apply one dark palette and stylesheet to the complete application."""
    app.setStyle("Fusion")
    if os.name == "nt" and "Segoe UI" not in QFontDatabase.families():
        # The offscreen Windows plugin does not enumerate the system font store.
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "segoeuib.ttf"):
            QFontDatabase.addApplicationFont(str(fonts / filename))
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["background"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["field"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["surface"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLORS["surface_alt"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["surface"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(COLORS["error"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(COLORS["accent_text"]))
    palette.setColor(QPalette.ColorRole.Link, QColor(COLORS["blue"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLORS["subtle"]))
    app.setPalette(palette)
    app.setStyleSheet(f"""
        QWidget {{
            color: {COLORS['text']};
            font-family: "Segoe UI";
            font-size: 10pt;
        }}
        QMainWindow, QWidget#appRoot {{ background: {COLORS['background']}; }}
        QWidget#sidebar {{ background: {COLORS['sidebar']}; }}
        QFrame#surface, QFrame.surface {{
            background: {COLORS['surface']};
            border: 1px solid {COLORS['border']};
            border-radius: 12px;
        }}
        QLabel.muted {{ color: {COLORS['muted']}; }}
        QLabel.subtle {{ color: {COLORS['subtle']}; }}
        QLabel.eyebrow {{ color: {COLORS['muted']}; font-size: 9pt; font-weight: 600; letter-spacing: 1px; }}
        QLabel.pageTitle {{ font-size: 21pt; font-weight: 650; }}
        QLabel.cardTitle {{ font-size: 12pt; font-weight: 650; }}
        QLabel.statusSuccess {{ color: {COLORS['success']}; }}
        QLabel.statusWarning {{ color: {COLORS['warning']}; }}
        QLabel.statusError {{ color: {COLORS['error']}; }}
        QPushButton {{
            min-height: 38px;
            padding: 0 15px;
            border: 1px solid {COLORS['border']};
            border-radius: 8px;
            background: {COLORS['surface_alt']};
            color: {COLORS['text']};
            font-weight: 600;
        }}
        QPushButton:hover {{ background: #202E3D; border-color: #3B5064; }}
        QPushButton:pressed {{ background: #111A23; }}
        QPushButton:disabled {{ color: {COLORS['subtle']}; background: #111820; border-color: #202B36; }}
        QPushButton.primaryButton {{ background: {COLORS['accent']}; color: {COLORS['accent_text']}; border-color: {COLORS['accent']}; }}
        QPushButton.primaryButton:hover {{ background: {COLORS['accent_hover']}; border-color: {COLORS['accent_hover']}; }}
        QPushButton.dangerButton {{ color: {COLORS['error']}; }}
        QPushButton.navButton {{ text-align: left; border: 0; background: transparent; min-height: 44px; padding: 0 12px; color: {COLORS['muted']}; }}
        QPushButton.navButton:hover {{ background: {COLORS['surface_alt']}; color: {COLORS['text']}; }}
        QPushButton.navButton[active="true"] {{ background: #1C2A36; color: {COLORS['accent']}; }}
        QLineEdit, QComboBox, QKeySequenceEdit {{
            min-height: 38px; padding: 0 11px; border: 1px solid {COLORS['border']};
            border-radius: 8px; background: {COLORS['field']}; selection-background-color: {COLORS['accent']};
        }}
        QLineEdit:focus, QComboBox:focus, QKeySequenceEdit:focus {{ border-color: {COLORS['accent']}; }}
        QListView {{ background: transparent; border: 0; outline: 0; }}
        QListView::item {{ min-height: 54px; border-bottom: 1px solid {COLORS['border']}; padding: 4px 9px; }}
        QListView::item:hover {{ background: {COLORS['surface_alt']}; }}
        QListView::item:selected {{ background: #20323C; color: {COLORS['text']}; border-left: 3px solid {COLORS['accent']}; }}
        QScrollArea {{ border: 0; background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: #354555; min-height: 28px; border-radius: 4px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QCheckBox {{ spacing: 9px; }}
        QCheckBox::indicator {{ width: 17px; height: 17px; }}
        QCheckBox::indicator:unchecked {{ border: 1px solid #526171; border-radius: 4px; background: {COLORS['field']}; }}
        QCheckBox::indicator:checked {{ border: 1px solid {COLORS['accent']}; border-radius: 4px; background: {COLORS['accent']}; }}
        QToolTip {{ background: {COLORS['surface_alt']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; padding: 5px; }}
    """)
