#!/usr/bin/env python3
"""
RemotePhone Linux Client
Mirror and control your Android phone from your Linux desktop.
"""

import sys
import logging

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
from PyQt6.QtGui import QFont
from remotephone.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RemotePhone")
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
