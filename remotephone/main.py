#!/usr/bin/env python3
"""
RemotePhone Linux Client
Mirror and control your Android phone from your Linux desktop.
"""

import sys
import logging

# Enable logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor, QFont
from PyQt6.QtCore import Qt
from remotephone.ui.main_window import MainWindow


def create_dark_palette() -> QPalette:
    """Create a sleek dark color palette."""
    palette = QPalette()

    # Window
    palette.setColor(QPalette.ColorRole.Window, QColor(13, 13, 15))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))

    # Base (input fields, lists)
    palette.setColor(QPalette.ColorRole.Base, QColor(22, 22, 42))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(30, 30, 50))

    # Text
    palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(107, 114, 128))

    # Tooltips
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(26, 26, 46))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))

    # Buttons
    palette.setColor(QPalette.ColorRole.Button, QColor(26, 26, 46))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(255, 255, 255))

    # Highlights
    palette.setColor(QPalette.ColorRole.Highlight, QColor(124, 58, 237))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))

    # Links
    palette.setColor(QPalette.ColorRole.Link, QColor(124, 58, 237))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))

    return palette


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RemotePhone")
    app.setStyle("Fusion")
    app.setPalette(create_dark_palette())

    # Use a clean sans-serif font
    font = QFont("Inter", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
