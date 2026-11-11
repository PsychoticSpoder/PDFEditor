"""A small built-in stylesheet for the GUI."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication


def apply_theme(app: QApplication) -> None:
    """Apply a compact, consistent look to the whole application."""
    app.setStyleSheet("""
        QGroupBox {
            font-weight: 600;
            margin-top: 12px;
            padding-top: 8px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
        }
        QPushButton {
            padding: 5px 12px;
            border-radius: 4px;
        }
        QPushButton:hover {
            background-color: #e8f0fe;
        }
        QStatusBar {
            color: #5f6368;
        }
        """)
