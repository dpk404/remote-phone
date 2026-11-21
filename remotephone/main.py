#!/usr/bin/env python3
"""
RemotePhone Linux Client
Mirror and control your Android phone from your Linux desktop.
"""

import sys
import logging

# Default to WARNING: only show errors and important messages
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
from remotephone.ui.main_window import ICONS, MainWindow

# Not this file's folder: in the macOS bundle the entry script sits outside the package
ASSETS = ICONS.parent


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
    app.setOrganizationName("RemotePhone")  # QSettings: client id and trusted phones
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")

    font = app.font()
    font.setPointSize(10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
