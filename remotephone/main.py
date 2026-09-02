#!/usr/bin/env python3
"""
RemotePhone Linux Client
Mirror and control your Android phone from your Linux desktop.
"""

import sys
import logging
from pathlib import Path

# Default to WARNING — only show errors and important messages
# Use --verbose or -v flag for debug output
_verbose = '--verbose' in sys.argv or '-v' in sys.argv
if _verbose:
    sys.argv = [a for a in sys.argv if a not in ('--verbose', '-v')]

logging.basicConfig(
    level=logging.DEBUG if _verbose else logging.WARNING,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QIcon
from remotephone.ui.main_window import MainWindow

ASSETS = Path(__file__).resolve().parent / "assets"


def app_icon() -> QIcon:
    """Multi-resolution window/taskbar icon built from the bundled PNGs."""
    icon = QIcon()
    for size in (64, 128, 256, 512):
        png = ASSETS / f"icon-{size}.png"
        if png.exists():
            icon.addFile(str(png))
    return icon


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RemotePhone")
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")

    # Use a clean sans-serif font
    font = QFont("Inter", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
