"""Smallest check that the Auto-scan toggle is honoured on startup and remembered.
Run from the repo root:  python -m tests.test_main_window
"""

import os
import tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from remotephone.ui import main_window

# keep the real user settings out of it
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())

app = QApplication([])
app.setOrganizationName("RemotePhone")
app.setApplicationName("RemotePhone")

scans = []
main_window.NetworkScanner.start_scan = lambda self, port=8765: scans.append(port)

w = main_window.MainWindow()
assert w.autoscan_checkbox.isChecked() and scans == [8765]  # default: scan on startup

w.autoscan_checkbox.setChecked(False)
w.ip_input.setText("192.168.1.42")
w._on_connected()  # a successful connection remembers the IP

w2 = main_window.MainWindow()
assert not w2.autoscan_checkbox.isChecked() and scans == [8765]  # off: no second scan
assert w2.ip_input.text() == "192.168.1.42"

print("ok")
