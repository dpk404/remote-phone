"""
RemotePhone — Main Window
Displays the mirrored phone screen and captures input for remote control.
"""

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QStatusBar,
    QSizePolicy, QCheckBox
)
from PyQt6.QtCore import Qt, QRectF, QTimer
from PyQt6.QtGui import QImage, QPainter, QColor, QFont, QKeyEvent

from remotephone.network.ws_client import WebSocketClient, FRAME_AUDIO_DATA
from remotephone.network.scanner import NetworkScanner
from remotephone.decoder.video_decoder import VideoDecoder
from remotephone.decoder.audio_player import AudioPlayer
from remotephone.input.input_handler import InputHandler

_BACK = {"type": "key", "action": "back"}


class VideoWidget(QWidget):
    """
    Renders the phone screen and turns mouse events into phone-coordinate
    commands via the InputHandler, sending them through the given callback.
    """

    def __init__(self, input_handler: InputHandler, send_command, parent=None):
        super().__init__(parent)
        self.input = input_handler
        self.send_command = send_command
        self.image = None
        self.phone_width = 1080
        self.phone_height = 2400
        self.setMinimumSize(360, 640)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pressing = False
        # Skip automatic background erase — we paint the full widget ourselves
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def set_phone_dimensions(self, width: int, height: int):
        self.phone_width = width
        self.phone_height = height

    def update_frame(self, image: QImage):
        self.image = image
        self.update()  # coalesced repaint — avoids blocking the decoder thread

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(0, 0, 0))

        if self.image:
            # Draw QImage directly — avoids expensive QPixmap.fromImage() conversion per frame
            target = QRectF(*self._get_display_rect())
            source = QRectF(0, 0, self.image.width(), self.image.height())
            painter.drawImage(target, self.image, source)
        else:
            # Placeholder
            painter.setPen(QColor(107, 114, 128))
            painter.setFont(QFont("Inter", 14))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter,
                "Connect to your phone\nto start mirroring"
            )

        painter.end()

    def _get_display_rect(self):
        """Calculate the aspect-fit image rectangle (x, y, w, h) within the widget."""
        if not self.image:
            return 0, 0, self.width(), self.height()

        img_ratio = self.image.width() / self.image.height()
        widget_ratio = self.width() / self.height()

        if widget_ratio > img_ratio:
            h = self.height()
            w = int(h * img_ratio)
        else:
            w = self.width()
            h = int(w / img_ratio)

        x = (self.width() - w) // 2
        y = (self.height() - h) // 2
        return x, y, w, h

    def _map_to_phone(self, pos):
        """Map widget pixel coordinates to phone screen coordinates."""
        if not self.image:
            return None, None

        x_off, y_off, dw, dh = self._get_display_rect()

        px = pos.x() - x_off
        py = pos.y() - y_off

        if px < 0 or px >= dw or py < 0 or py >= dh:
            return None, None

        phone_x = (px / dw) * self.phone_width
        phone_y = (py / dh) * self.phone_height
        return phone_x, phone_y

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            px, py = self._map_to_phone(event.position())
            if px is not None:
                self._pressing = True
                self.input.on_press(px, py)
        elif event.button() == Qt.MouseButton.MiddleButton:
            # Middle-click (scroll wheel click) = Back gesture
            self.send_command(_BACK)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._pressing:
            self._pressing = False
            px, py = self._map_to_phone(event.position())
            if px is None:
                px, py = -1, -1  # released outside — handler uses last known position
            cmd = self.input.on_release(px, py)
            if cmd:
                self.send_command(cmd)
        elif event.button() == Qt.MouseButton.RightButton:
            # Right-click = Back button
            self.send_command(_BACK)
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self._pressing:
            px, py = self._map_to_phone(event.position())
            if px is not None:
                self.input.on_move(px, py)
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        px, py = self._map_to_phone(event.position())
        if px is not None:
            delta = event.angleDelta()
            cmd = self.input.on_scroll(px, py, float(delta.x()), float(delta.y()),
                                       event.phase())
            if cmd:
                self.send_command(cmd)
        super().wheelEvent(event)


class MainWindow(QMainWindow):
    """Main application window with connection controls, video display, and status bar."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RemotePhone")
        self.resize(420, 860)

        # Components
        self.ws_client = WebSocketClient()
        self.decoder = VideoDecoder()
        self.audio_player = AudioPlayer()
        self.input_handler = InputHandler()
        self.scanner = NetworkScanner()

        # State
        self.connected = False
        self.frame_count = 0  # decoded frames since the last FPS tick

        self._setup_ui()
        self._setup_connections()
        self._apply_stylesheet()

        self.fps_timer = QTimer(self)
        self.fps_timer.timeout.connect(self._update_fps)
        self.fps_timer.start(1000)

        # Auto-scan for phone on startup
        self.scanner.start_scan()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Connection Bar ──
        conn_bar = QWidget()
        conn_bar.setObjectName("connectionBar")
        conn_layout = QHBoxLayout(conn_bar)
        conn_layout.setContentsMargins(12, 8, 12, 8)
        conn_layout.setSpacing(8)

        ip_label = QLabel("IP:")
        ip_label.setFixedWidth(18)
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("Scanning...")
        self.ip_input.setFixedWidth(160)

        self.scan_btn = QPushButton("Scan")
        self.scan_btn.setObjectName("scanBtn")
        self.scan_btn.setFixedWidth(50)
        self.scan_btn.setToolTip("Scan local network for RemotePhone server")

        port_label = QLabel("Port:")
        port_label.setFixedWidth(30)
        self.port_input = QLineEdit()
        self.port_input.setPlaceholderText("Port")
        self.port_input.setText("8765")
        self.port_input.setFixedWidth(60)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("connectBtn")
        self.connect_btn.setFixedWidth(100)

        self.audio_checkbox = QCheckBox("Audio")
        self.audio_checkbox.setEnabled(False)
        self.audio_checkbox.setToolTip("Stream phone audio (requires Android 10+)")

        conn_layout.addWidget(ip_label)
        conn_layout.addWidget(self.ip_input)
        conn_layout.addWidget(self.scan_btn)
        conn_layout.addWidget(port_label)
        conn_layout.addWidget(self.port_input)
        conn_layout.addWidget(self.connect_btn)
        conn_layout.addStretch()
        conn_layout.addWidget(self.audio_checkbox)

        layout.addWidget(conn_bar)

        # ── Video Display ──
        self.video_widget = VideoWidget(self.input_handler, self._send_command)
        layout.addWidget(self.video_widget, 1)

        # ── Status Bar ──
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.status_label = QLabel("Disconnected")
        self.device_label = QLabel("")
        self.fps_label = QLabel("")
        self.resolution_label = QLabel("")

        self.status_bar.addWidget(self.status_label, 1)
        self.status_bar.addPermanentWidget(self.resolution_label)
        self.status_bar.addPermanentWidget(self.device_label)
        self.status_bar.addPermanentWidget(self.fps_label)

    def _setup_connections(self):
        # Connection
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.ip_input.returnPressed.connect(self._on_connect_clicked)
        self.scan_btn.clicked.connect(self._on_scan_clicked)

        # Scanner signals
        self.scanner.scan_started.connect(self._on_scan_started)
        self.scanner.scan_complete.connect(self._on_scan_complete)

        # WebSocket signals
        self.ws_client.connected.connect(self._on_connected)
        self.ws_client.disconnected.connect(self._on_disconnected)
        self.ws_client.reconnecting.connect(self._on_reconnecting)
        self.ws_client.frame_received.connect(self._on_frame_received)
        self.ws_client.info_received.connect(self._on_info_received)
        self.ws_client.clipboard_received.connect(self._on_clipboard_received)
        self.ws_client.error_occurred.connect(self._on_error)

        # Give decoder a direct reference so WS thread can feed frames
        # without a Qt signal hop through the main thread
        self.ws_client.set_video_decoder(self.decoder)

        # Decoder → display
        self.decoder.frame_ready.connect(self._on_decoded_frame)

        # Audio toggle
        self.audio_checkbox.toggled.connect(self._on_audio_toggled)

    def _apply_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0D0D0F;
            }
            #connectionBar {
                background-color: #1A1A2E;
                border-bottom: 1px solid #2D2D44;
            }
            QLabel {
                color: #D1D5DB;
                font-size: 13px;
            }
            QLineEdit {
                background-color: #16162A;
                border: 1px solid #3D3D5C;
                border-radius: 6px;
                padding: 6px 10px;
                color: #FFFFFF;
                font-family: 'JetBrains Mono', 'Fira Code', monospace;
                font-size: 13px;
                selection-background-color: #7C3AED;
            }
            QLineEdit:focus {
                border-color: #7C3AED;
            }
            #scanBtn {
                background-color: #2D2D44;
                border: 1px solid #3D3D5C;
                border-radius: 6px;
                padding: 7px 8px;
                color: #D1D5DB;
                font-size: 12px;
            }
            #scanBtn:hover {
                background-color: #3D3D5C;
            }
            #scanBtn:disabled {
                color: #6B7280;
            }
            #connectBtn {
                background-color: #7C3AED;
                border: none;
                border-radius: 6px;
                padding: 7px 16px;
                color: #FFFFFF;
                font-weight: bold;
                font-size: 13px;
            }
            #connectBtn:hover {
                background-color: #6D28D9;
            }
            #connectBtn:pressed {
                background-color: #5B21B6;
            }
            QCheckBox {
                color: #D1D5DB;
                font-size: 13px;
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 3px;
                border: 1px solid #3D3D5C;
                background-color: #16162A;
            }
            QCheckBox::indicator:checked {
                background-color: #7C3AED;
                border-color: #7C3AED;
            }
            QStatusBar {
                background-color: #111122;
                border-top: 1px solid #2D2D44;
                color: #9CA3AF;
                font-size: 12px;
                padding: 2px 8px;
            }
            QStatusBar QLabel {
                font-size: 12px;
                padding: 0 8px;
            }
            QToolTip {
                background-color: #1A1A2E;
                color: #FFFFFF;
                border: 1px solid #3D3D5C;
            }
        """)

    # ── Connection handlers ──

    def _on_connect_clicked(self):
        if not self.connected:
            ip = self.ip_input.text().strip()
            port = self.port_input.text().strip() or "8765"
            if not ip:
                self.status_label.setText("⚠ Enter the phone's IP address")
                return
            url = f"ws://{ip}:{port}"
            self.status_label.setText(f"Connecting to {url}...")
            self.connect_btn.setText("Cancel")
            self.ws_client.connect_to(url)
        else:
            self.ws_client.disconnect()

    def _on_connected(self):
        self.connected = True
        self.connect_btn.setText("Disconnect")
        self.status_label.setText("● Connected")
        self.decoder.start()

    def _on_disconnected(self, clean: bool = False):
        was_connected = self.connected
        self.connected = False
        self.fps_label.setText("")
        self.decoder.stop()
        self.audio_player.stop()
        self.input_handler.reset()
        self.video_widget._pressing = False
        if was_connected and not clean:
            self.status_label.setText("Connection lost — reconnecting...")
            self.connect_btn.setText("Cancel")
        else:
            self.connect_btn.setText("Connect")
            self.status_label.setText("Disconnected")
            self.device_label.setText("")
            self.resolution_label.setText("")
            self.audio_checkbox.setEnabled(False)
            self.audio_checkbox.setChecked(False)
            self.video_widget.image = None
            self.video_widget.update()
            self.setWindowTitle("RemotePhone")

    def _on_reconnecting(self, attempt: int):
        self.status_label.setText(f"Reconnecting... (attempt {attempt})")

    def _on_info_received(self, info: dict):
        device = info.get("device", "Unknown")
        sw = info.get("screenWidth", 0)
        sh = info.get("screenHeight", 0)
        android_ver = info.get("androidVersion", "?")

        self.device_label.setText(f"{device}")
        self.resolution_label.setText(f"{sw}×{sh}")
        self.video_widget.set_phone_dimensions(sw, sh)
        self.setWindowTitle(f"RemotePhone — {device} (Android {android_ver})")

        if info.get("audioAvailable", False):
            self.audio_checkbox.setEnabled(True)

    def _on_clipboard_received(self, content: str):
        QApplication.clipboard().setText(content)
        self.status_label.setText(f"Copied to clipboard ({len(content)} chars)")

    def _on_error(self, error: str):
        self.status_label.setText(f"⚠ {error}")
        if not self.connected:
            self.connect_btn.setText("Connect")

    # ── Network scan ──

    def _on_scan_clicked(self):
        port = int(self.port_input.text().strip() or "8765")
        self.scanner.start_scan(port)

    def _on_scan_started(self):
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("...")
        self.ip_input.setPlaceholderText("Scanning...")
        if not self.connected:
            self.status_label.setText("Scanning local network...")

    def _on_scan_complete(self, found: list):
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Scan")
        self.ip_input.setPlaceholderText("Phone IP address")
        if found:
            self.ip_input.setText(found[0])
        if self.connected:
            return

        if len(found) == 1:
            # Exactly one server found — auto-connect
            self.status_label.setText(f"Found phone at {found[0]}")
            self._on_connect_clicked()
        elif found:
            # Multiple found — first is filled in, let user pick. Cap the listed IPs so a long
            # result set can't stretch the status bar (and the window) arbitrarily wide.
            shown = ", ".join(found[:3])
            if len(found) > 3:
                shown += f", +{len(found) - 3} more"
            self.status_label.setText(f"Found {len(found)} devices: {shown}")
        else:
            self.status_label.setText("No phone found — enter IP manually")

    # ── Frame routing ──

    def _on_frame_received(self, frame_type: int, timestamp: int, data: bytes):
        # Video goes straight from the WS thread to the decoder; only audio arrives here
        if frame_type == FRAME_AUDIO_DATA:
            self.audio_player.feed(data)

    def _on_decoded_frame(self, image: QImage):
        self.video_widget.update_frame(image)
        self.frame_count += 1

    def _update_fps(self):
        if self.connected:
            self.fps_label.setText(f"{self.frame_count} FPS")
            self.frame_count = 0

    # ── Input handlers ──

    def keyPressEvent(self, event: QKeyEvent):
        cmd = self.input_handler.on_key_press(event)
        # Holding a key repeats text/backspace/delete/enter, but never system keys (back, home, ...)
        if event.isAutoRepeat() and not (cmd and cmd["type"] in ("text", "backspace", "delete")):
            return
        # Ctrl+V types the desktop clipboard into the phone; the phone's own
        # clipboard paste stays as fallback when the desktop clipboard is empty
        if cmd and cmd["type"] == "paste":
            text = QApplication.clipboard().text()
            if text:
                cmd = {"type": "text", "content": text}
        if cmd:
            self._send_command(cmd)
        elif event.key() == Qt.Key.Key_F11:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
        else:
            super().keyPressEvent(event)

    def _send_command(self, cmd: dict):
        if self.connected:
            self.ws_client.send_command(cmd)

    # ── Audio toggle ──

    def _on_audio_toggled(self, checked: bool):
        if not self.connected:
            return
        self._send_command({"type": "toggle_audio", "enabled": checked})
        if checked:
            self.audio_player.start()
        else:
            self.audio_player.stop()

    # ── Cleanup ──

    def closeEvent(self, event):
        self.ws_client.disconnect()
        self.decoder.stop()
        self.audio_player.stop()
        super().closeEvent(event)
