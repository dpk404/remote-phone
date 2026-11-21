"""Smallest checks for the main window: the launch switches, the page flow, scan results
and the update version comparison.
Run from the repo root:  python -m tests.test_main_window
"""

import os
import tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings, Qt
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
# a lost connection shows Reconnecting and drops the fps until frames flow again
w.resize(480, 820)
w._set_device("asus ASUS_X01BDA", "192.168.1.6 · 8765")
w.connected, w._resolution, w.frame_count = True, "720 × 1520", 400
w._on_disconnected(False)
assert "Reconnecting" in w.live_pill.text()
assert w.stats_label.text() == "720 × 1520" and w.frame_count == 0
w._on_disconnected(True, "Disconnected on the phone")
assert w.stack.currentWidget() is w.connect_page and not w.connected
assert "Disconnected on the phone" in w.status_label.text()

# scan results: several fill the list, exactly one also connects
w._on_scan_complete([("192.168.1.4", "Pixel 8 Pro"), ("192.168.1.9", "Galaxy S23")])
assert w.phone_list.count() == 2 and connects == []
w._on_scan_complete([("192.168.1.4", "Pixel 8 Pro")])
app.processEvents()  # let the old cards go
assert w.phone_list.count() == 1 and connects == ["wss://192.168.1.4:8765"]
assert w.wait_name.text() == "Pixel 8 Pro"

# names from the network never render as markup
w._on_scan_complete([("192.168.1.4", "<b>x</b>")])
app.processEvents()
card_name = w.phone_list.itemAt(0).widget().findChild(main_window.QLabel, "cardName")
assert card_name.textFormat() == Qt.TextFormat.PlainText and w.wait_name.textFormat() == Qt.TextFormat.PlainText

# a bad port typed past the validator falls back to the default instead of aborting the app
w.port_input.setText("87x5")
w.scan_btn.click()
assert scans[-1] == 8765
# and the card and the auto-connect use that same port
w.port_input.setText("99999")
w.scan_btn.click()
w._on_scan_complete([("192.168.1.4", "Pixel 8 Pro")])
app.processEvents()
assert scans[-1] == 8765 and connects[-1] == "wss://192.168.1.4:8765"
assert "port 8765" in w.phone_list.itemAt(0).widget().findChild(main_window.QLabel, "mono").text()

# a malformed info from the network is shown, not raised
w._on_info_received({"type": "info", "device": 5, "screenWidth": "x", "androidVersion": None})
assert w.wait_name.text() == "5"

# the phone's own audio switch moves the speaker button without echoing a toggle back
sent, playing = [], []
w.ws_client.send_command = sent.append
w.audio_player.start = lambda: playing.append(True)
w.audio_player.stop = lambda: playing.append(False)
w.connected = True
w._on_audio_state(True)
assert w.audio_btn.isChecked() and playing == [True] and sent == []
w._on_audio_state(True)
assert playing == [True]
w._on_audio_state(False)
assert not w.audio_btn.isChecked() and playing == [True, False] and sent == []
w._on_audio_state(True)
w._on_disconnected(False)  # a lost connection forgets it until the phone says so again
assert not w.audio_btn.isChecked() and sent == []

assert main_window.is_newer("2.10.0", "2.9.1")
assert not main_window.is_newer("2.1.0", "2.1.0") and not main_window.is_newer("2.0.9", "2.1.0")

print("ok")
