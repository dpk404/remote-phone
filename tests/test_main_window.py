"""Smallest checks for the main window: the launch switches, the page flow, scan results
and the update version comparison.
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

scans, connects, update_checks = [], [], []
main_window.NetworkScanner.start_scan = lambda self, port=8765: scans.append(port)
main_window.WebSocketClient.connect_to = lambda self, url: connects.append(url)
main_window.MainWindow._check_update = lambda self: update_checks.append(1)

w = main_window.MainWindow()
assert w.scan_switch.isChecked() and scans == [8765]  # default: scan on startup
assert w.updates_switch.isChecked() and update_checks == [1]

w.scan_switch.setChecked(False)
w.updates_switch.setChecked(False)
w.ip_input.setText("192.168.1.42")
w._on_connected()  # a successful connection remembers the IP

w2 = main_window.MainWindow()
assert not w2.scan_switch.isChecked() and scans == [8765]  # off: no second scan
assert not w2.updates_switch.isChecked() and update_checks == [1]
assert w2.ip_input.text() == "192.168.1.42"

# page flow
w._on_approval("pending")
assert w.stack.currentWidget() is w.wait_page
w._on_approval("granted")
assert w.stack.currentWidget() is w.mirror_page
w._on_disconnected(True)
assert w.stack.currentWidget() is w.connect_page and not w.connected

# scan results: several fill the list, exactly one also connects
w._on_scan_complete([("192.168.1.4", "Pixel 8 Pro"), ("192.168.1.9", "Galaxy S23")])
assert w.phone_list.count() == 2 and connects == []
w._on_scan_complete([("192.168.1.4", "Pixel 8 Pro")])
app.processEvents()  # let the old cards go
assert w.phone_list.count() == 1 and connects == ["wss://192.168.1.4:8765"]
assert w.wait_name.text() == "Pixel 8 Pro"

assert main_window.is_newer("2.10.0", "2.9.1")
assert not main_window.is_newer("2.1.0", "2.1.0") and not main_window.is_newer("2.0.9", "2.1.0")

print("ok")
