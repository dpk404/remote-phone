"""
RemotePhone: Main Window
Displays the mirrored phone screen and captures input for remote control.
"""

import html
import json
import threading
import urllib.request
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFrame,
    QLabel, QLineEdit, QPushButton, QToolButton, QStackedWidget, QScrollArea,
    QSizePolicy, QCheckBox, QMessageBox
)
from PyQt6.QtCore import Qt, QRectF, QTimer, QSettings, QSize, QUrl, pyqtSignal
from PyQt6.QtGui import (QImage, QPainter, QColor, QFont, QFontDatabase, QIntValidator, QKeyEvent, QIcon,
                         QDesktopServices)

from remotephone.network.ws_client import WebSocketClient, FRAME_AUDIO_DATA
from remotephone.network.scanner import NetworkScanner
from remotephone.decoder.video_decoder import VideoDecoder
from remotephone.decoder.audio_player import AudioPlayer
from remotephone.input.input_handler import InputHandler

_BACK = {"type": "key", "action": "back"}
_IDLE_HINT = "Waiting for the picture"
ICONS = Path(__file__).resolve().parent.parent / "assets" / "icons"
RELEASES_URL = "https://github.com/dpk404/remote-phone/releases"
PYPI_URL = "https://pypi.org/pypi/remote-phone/json"
GREEN, AMBER, RED = "#10B981", "#F59E0B", "#EF4444"
try:
    VERSION = version("remote-phone")
except PackageNotFoundError:
    VERSION = ""


def is_newer(latest: str, current: str) -> bool:
    return tuple(map(int, latest.split("."))) > tuple(map(int, current.split(".")))


def _icon(name: str) -> QIcon:
    return QIcon(str(ICONS / f"{name}.svg"))


def _label(text: str = "", name: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    return label


def _rule() -> QFrame:
    rule = QFrame()
    rule.setObjectName("rule")
    rule.setFixedHeight(1)
    return rule


def _frame(name: str, layout_cls=QVBoxLayout, margins=(0, 0, 0, 0), spacing=0):
    frame = QFrame()
    frame.setObjectName(name)
    layout = layout_cls(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return frame, layout


def _tool(icon: str, tip: str = "", text: str = "") -> QToolButton:
    btn = QToolButton()
    btn.setIcon(_icon(icon))
    btn.setIconSize(QSize(18, 18))
    btn.setToolTip(tip)
    btn.setText(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keys keep going to the phone
    if text:
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        btn.setFixedHeight(50)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    else:
        btn.setFixedSize(36, 36)
    return btn


def _button(text: str, name: str, height: int = 0) -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName(name)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if height:
        btn.setFixedHeight(height)
    return btn


def _scroll_page(content: QWidget, max_width: int = 480) -> QScrollArea:
    """Centres content at most max_width wide; scrolls instead of setting a window minimum height."""
    content.setMaximumWidth(max_width)
    page, row = _frame("page", QHBoxLayout)
    row.addWidget(content)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(page)
    return scroll


# (key, action) rows for the help dialog and the ? button tooltip. Mirrors the
# gesture and key handling in input_handler.py.
SHORTCUTS = [
    ("Left click", "Tap"),
    ("Click + drag", "Swipe"),
    ("Hold click (>0.5s)", "Long press"),
    ("Scroll wheel / two fingers", "Scroll"),
    ("Two-finger swipe right", "Back"),
    ("Right / middle click", "Back"),
    ("Esc", "Back"),
    ("Home", "Home"),
    ("F2", "Recent apps"),
    ("F5", "Notifications"),
    ("F6", "Quick Settings"),
    ("F10", "Lock screen"),
    ("F11", "Fullscreen toggle"),
    ("Type", "Text into the focused field"),
    ("Backspace", "Delete (or Back if no field)"),
    ("Delete", "Delete forward"),
    ("Enter", "Confirm / submit"),
    ("Ctrl+A / C / X / V", "Select all / Copy / Cut / Paste"),
]
SHORTCUTS_TOOLTIP = "\n".join(f"{k}\u2003{a}" for k, a in SHORTCUTS)


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
        self.hint = _IDLE_HINT  # shown in the black area until the first frame arrives
        self.setMinimumSize(360, 360)  # square floor so a landscape window can be short
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pressing = False
        # Skip automatic background erase, we paint the full widget ourselves
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def update_frame(self, image: QImage):
        self.image = image
        self.update()  # coalesced repaint, avoids blocking the decoder thread

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(0, 0, 0))

        if self.image:
            # Draw QImage directly: avoids expensive QPixmap.fromImage() conversion per frame
            target = QRectF(*self._get_display_rect())
            source = QRectF(0, 0, self.image.width(), self.image.height())
            painter.drawImage(target, self.image, source)
        else:
            # Placeholder
            painter.setPen(QColor(107, 114, 128))
            font = QFont()
            font.setPointSize(14)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.hint)

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

        # The frame is the phone's screen size, so this follows rotation with no protocol hop
        phone_x = (px / dw) * self.image.width()
        phone_y = (py / dh) * self.image.height()
        return phone_x, phone_y

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            px, py = self._map_to_phone(event.position())
            if px is not None:
                self._pressing = True
                cmd = self.input.on_press(px, py)
                if cmd:
                    self.send_command(cmd)
        elif event.button() == Qt.MouseButton.MiddleButton:
            # Middle-click (scroll wheel click) = Back gesture
            self.send_command(_BACK)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._pressing:
            self._pressing = False
            px, py = self._map_to_phone(event.position())
            if px is None:
                px, py = -1, -1  # released outside, handler uses last known position
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
                cmd = self.input.on_move(px, py)
                if cmd:
                    self.send_command(cmd)
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        px, py = self._map_to_phone(event.position())
        if px is not None:
            delta = event.angleDelta()
            # How far the picture should move, in phone pixels: trackpads report it in points
            scale = self.image.height() / self._get_display_rect()[3]
            drag = (event.pixelDelta().y() or delta.y() / 2) * scale
            cmd = self.input.on_scroll(px, py, float(delta.x()), float(delta.y()),
                                       event.phase(), drag, self.image.height())
            for c in cmd if isinstance(cmd, list) else [cmd] if cmd else []:
                self.send_command(c)
        super().wheelEvent(event)




class MainWindow(QMainWindow):
    """Connect, Waiting and Mirroring pages on one stack."""

    update_found = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RemotePhone")
        self.resize(480, 820)

        # Components
        self.ws_client = WebSocketClient()
        self.decoder = VideoDecoder()
        self.audio_player = AudioPlayer()
        self.input_handler = InputHandler()
        self.scanner = NetworkScanner()

        # State
        self.connected = False
        self._connecting = False  # from a connect click until the connection is given up
        self.frame_count = 0  # decoded frames since the last FPS tick
        self._resolution = ""
        self._landscape = False  # shape the window was last fitted to; starts portrait

        self._setup_ui()
        self._setup_connections()
        self._apply_stylesheet()

        self.fps_timer = QTimer(self)
        self.fps_timer.timeout.connect(self._update_fps)
        self.fps_timer.start(1000)

        if self.scan_switch.isChecked():
            self.scanner.start_scan()
        if self.updates_switch.isChecked():
            threading.Thread(target=self._check_update, daemon=True, name="UpdateCheck").start()

    def _setup_ui(self):
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.connect_page = self._build_connect()
        self.wait_page = self._build_wait()
        self.mirror_page = self._build_mirror()
        for page in (self.connect_page, self.wait_page, self.mirror_page):
            self.stack.addWidget(page)

    def _build_connect(self) -> QWidget:
        content, col = _frame("content")

        brand, row = _frame("", QHBoxLayout, (28, 28, 28, 0), 14)
        logo = QLabel()
        logo.setPixmap(_icon("logo").pixmap(40, 40))
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(_label("RemotePhone", "brand"))
        titles.addWidget(_label("Mirror and control your Android phone", "muted"))
        row.addWidget(logo)
        row.addLayout(titles, 1)
        col.addWidget(brand)

        phones, box = _frame("", QVBoxLayout, (28, 30, 28, 0), 10)
        head = QHBoxLayout()
        head.addWidget(_label("PHONES ON YOUR NETWORK", "section"))
        head.addStretch()
        self.scan_btn = _button("Scan again", "ghost", 30)
        self.scan_btn.setIcon(_icon("refresh"))
        self.scan_btn.setIconSize(QSize(14, 14))
        self.scan_btn.setToolTip("Scan the local network for phones")
        head.addWidget(self.scan_btn)
        box.addLayout(head)
        self.phone_list = QVBoxLayout()
        self.phone_list.setSpacing(10)
        box.addLayout(self.phone_list)
        box.addWidget(_label("Phones appear here while they are mirroring.", "small"))
        col.addWidget(phones)

        divider, row = _frame("", QHBoxLayout, (28, 22, 28, 0), 12)
        row.addWidget(_rule(), 1)
        row.addWidget(_label("or enter an address", "small"))
        row.addWidget(_rule(), 1)
        col.addWidget(divider)

        form, box = _frame("", QVBoxLayout, (28, 18, 28, 0), 12)
        fields = QHBoxLayout()
        fields.setSpacing(10)
        self.ip_input = QLineEdit(QSettings().value("last_ip", "", type=str))
        self.ip_input.setPlaceholderText("192.168.1.4")
        self.port_input = QLineEdit("8765")
        self.port_input.setPlaceholderText("8765")
        self.port_input.setFixedWidth(92)
        for caption, edit in (("IP address", self.ip_input), ("Port", self.port_input)):
            field = QVBoxLayout()
            field.setSpacing(6)
            field.addWidget(_label(caption, "fieldLabel"))
            edit.setFixedHeight(40)
            field.addWidget(edit)
            fields.addLayout(field, 1 if edit is self.ip_input else 0)
        box.addLayout(fields)
        self.connect_btn = _button("Connect to address", "secondary", 40)
        box.addWidget(self.connect_btn)
        col.addWidget(form)

        how, box = _frame("", QVBoxLayout, (28, 26, 28, 0), 10)
        box.addWidget(_label("HOW IT WORKS", "section"))
        steps = QHBoxLayout()
        steps.setSpacing(10)
        for n, text in enumerate(("Start mirroring on the phone", "Pick it here or type its address",
                                  "Tap Allow on the phone"), 1):
            step, s = _frame("step", QVBoxLayout, (12, 12, 12, 12), 8)
            num = _label(str(n), "stepNum")
            num.setFixedSize(22, 22)
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption = _label(text, "stepText")
            caption.setWordWrap(True)
            s.addWidget(num)
            s.addWidget(caption)
            s.addStretch()
            steps.addWidget(step, 1)
        box.addLayout(steps)
        col.addWidget(how)
        col.addStretch()

        prefs, box = _frame("prefs", QVBoxLayout, (16, 4, 16, 4), 0)
        switches = []
        for title, sub, key in (("Scan for phones at launch", "Turn off if you always type the address", "auto_scan"),
                                ("Check for updates at launch", "Asks PyPI once for a newer version", "check_updates")):
            row = QHBoxLayout()
            row.setContentsMargins(0, 10, 0, 10)
            text = QVBoxLayout()
            text.setSpacing(3)
            text.addWidget(_label(title, "prefTitle"))
            text.addWidget(_label(sub, "small"))
            switch = QCheckBox()
            switch.setObjectName("switch")
            switch.setCursor(Qt.CursorShape.PointingHandCursor)
            switch.setToolTip(title)
            switch.setChecked(QSettings().value(key, True, type=bool))
            switch.toggled.connect(lambda on, k=key: QSettings().setValue(k, on))
            row.addLayout(text, 1)
            row.addWidget(switch)
            box.addLayout(row)
            switches.append(switch)
        self.scan_switch, self.updates_switch = switches
        wrap = QHBoxLayout()
        wrap.setContentsMargins(28, 22, 28, 0)
        wrap.addWidget(prefs)
        col.addLayout(wrap)

        footer, row = _frame("footer", QHBoxLayout, (28, 12, 28, 12), 12)
        self.status_label = _label("", "small")
        self._set_status("Ready")
        self.update_btn = _button("Update available", "pill", 24)
        self.update_btn.setIcon(_icon("download"))
        self.update_btn.setIconSize(QSize(12, 12))
        self.update_btn.setToolTip(RELEASES_URL)
        self.update_btn.hide()
        self.shortcuts_btn = _button("Shortcuts", "link")
        self.shortcuts_btn.setToolTip(SHORTCUTS_TOOLTIP)  # hover for the list, click for the dialog
        row.addWidget(self.status_label, 1)
        row.addWidget(self.update_btn)
        row.addWidget(_label(f"v{VERSION}" if VERSION else "", "monoSmall"))
        row.addWidget(self.shortcuts_btn)
        col.addSpacing(16)
        page, outer = _frame("page")
        outer.addWidget(_scroll_page(content), 1)
        outer.addWidget(footer)  # outside the scroll so the status stays in view
        return page

    def _device_header(self, lead: QWidget, pill: QLabel, name_attr: str, addr_attr: str) -> QFrame:
        bar, row = _frame("bar", QHBoxLayout, (16, 10, 12, 10), 10)
        names = QVBoxLayout()
        names.setSpacing(1)
        for attr, obj in ((name_attr, "name"), (addr_attr, "mono")):
            label = _label("", obj)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # long names clip, not widen
            setattr(self, attr, label)
            names.addWidget(label)
        row.addWidget(lead)
        row.addLayout(names, 1)
        row.addWidget(pill)
        return bar

    def _build_wait(self) -> QWidget:
        content, col = _frame("content")
        back = _tool("back", "Back to connect")
        back.clicked.connect(self._disconnect)
        self.wait_pill = _label(f"<span style='color:{AMBER}'>●</span>&nbsp;Waiting", "waitPill")
        self.wait_pill.setFixedHeight(28)
        col.addWidget(self._device_header(back, self.wait_pill, "wait_name", "wait_addr"))

        middle, box = _frame("", QVBoxLayout, (40, 24, 40, 24), 28)
        box.addStretch()
        glow, g = _frame("glow")
        glow.setFixedSize(168, 268)
        phone, p = _frame("phoneFrame", QVBoxLayout, (14, 14, 14, 14))
        phone.setFixedSize(132, 248)
        prompt, q = _frame("prompt", QVBoxLayout, (10, 12, 10, 12), 8)
        tile = QLabel()
        tile.setObjectName("tileSmall")
        tile.setFixedSize(26, 26)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setPixmap(_icon("monitor").pixmap(14, 14))
        q.addWidget(tile, 0, Qt.AlignmentFlag.AlignHCenter)
        ask = _label("This computer wants to connect", "promptText")
        ask.setWordWrap(True)
        ask.setAlignment(Qt.AlignmentFlag.AlignCenter)
        q.addWidget(ask)
        buttons = QHBoxLayout()
        buttons.setSpacing(5)
        for name in ("fakeDeny", "fakeAllow"):
            fake = QFrame()
            fake.setObjectName(name)
            fake.setFixedHeight(18)
            buttons.addWidget(fake)
        q.addLayout(buttons)
        p.addWidget(prompt, 0, Qt.AlignmentFlag.AlignVCenter)
        g.addWidget(phone, 0, Qt.AlignmentFlag.AlignCenter)
        box.addWidget(glow, 0, Qt.AlignmentFlag.AlignHCenter)

        copy = QVBoxLayout()
        copy.setContentsMargins(40, 0, 40, 0)  # 320 wide text at the 480 design width
        copy.setSpacing(10)
        title = _label("Tap Allow on your phone", "title")
        self.wait_body = _label("", "body")
        for label in (title, self.wait_body):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            copy.addWidget(label)
        box.addLayout(copy)

        lock = QHBoxLayout()
        lock.setSpacing(8)
        lock.addStretch()
        icon = QLabel()
        icon.setPixmap(_icon("lock-muted").pixmap(14, 14))
        lock.addWidget(icon)
        lock.addWidget(_label("Encrypted connection · Allow lasts for this mirroring session", "small"))
        lock.addStretch()
        box.addLayout(lock)
        box.addStretch()
        col.addWidget(middle, 1)

        bottom, box = _frame("", QVBoxLayout, (28, 0, 28, 24), 10)
        self.cancel_btn = _button("Cancel", "secondary", 40)
        self.cancel_btn.clicked.connect(self._disconnect)
        note = _label("The request closes by itself after a minute without an answer.", "small")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setWordWrap(True)
        box.addWidget(self.cancel_btn)
        box.addWidget(note)
        col.addWidget(bottom)
        return _scroll_page(content)

    def _build_mirror(self) -> QWidget:
        page, col = _frame("page")
        logo = QLabel()
        logo.setPixmap(_icon("logo").pixmap(22, 22))
        self.live_pill = _label("", "livePill")
        self.live_pill.setFixedHeight(26)
        bar = self._device_header(logo, self.live_pill, "mirror_name", "mirror_addr")
        row = bar.layout()
        sep = _rule()
        sep.setFixedSize(1, 22)
        row.addWidget(sep)
        self.audio_btn = _tool("volume", "Stream phone audio (requires Android 10+)")
        icon = self.audio_btn.icon()
        icon.addFile(str(ICONS / "volume-on.svg"), QSize(), QIcon.Mode.Normal, QIcon.State.On)
        self.audio_btn.setIcon(icon)
        self.audio_btn.setCheckable(True)
        self.audio_btn.setEnabled(False)
        self.fullscreen_btn = _tool("fullscreen", "Fullscreen (F11)")
        self.disconnect_btn = _tool("close", "Disconnect")
        for btn in (self.audio_btn, self.fullscreen_btn, self.disconnect_btn):
            row.addWidget(btn)
        col.addWidget(bar)

        self.video_widget = VideoWidget(self.input_handler, self._send_command)
        col.addWidget(self.video_widget, 1)

        nav, row = _frame("navRow", QHBoxLayout, (12, 6, 12, 6), 4)
        for icon, text, action in (("back", "Back", "back"), ("home", "Home", "home"),
                                   ("recents", "Recents", "recents"), ("alerts", "Alerts", "notifications"),
                                   ("lock", "Lock", "power")):
            btn = _tool(icon, text=text)
            btn.clicked.connect(lambda _, a=action: self._send_command({"type": "key", "action": a}))
            row.addWidget(btn)
        col.addWidget(nav)

        strip, row = _frame("strip", QHBoxLayout, (16, 0, 16, 0))
        strip.setFixedHeight(30)
        self.stats_label = _label("", "monoSmall")
        self.keys_btn = _button("Keyboard shortcuts", "linkSmall")
        self.keys_btn.setToolTip(SHORTCUTS_TOOLTIP)
        row.addWidget(self.stats_label, 1)
        row.addWidget(self.keys_btn)
        col.addWidget(strip)
        return page

    def _setup_connections(self):
        # Connection
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.ip_input.returnPressed.connect(self._on_connect_clicked)
        self.scan_btn.clicked.connect(self._on_scan_clicked)
        self.shortcuts_btn.clicked.connect(self._show_shortcuts)
        self.keys_btn.clicked.connect(self._show_shortcuts)
        self.disconnect_btn.clicked.connect(self._disconnect)
        self.fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        self.update_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RELEASES_URL)))
        self.update_found.connect(self.update_btn.show)

        # Scanner signals
        self.scanner.scan_started.connect(self._on_scan_started)
        self.scanner.scan_complete.connect(self._on_scan_complete)

        # WebSocket signals
        self.ws_client.connected.connect(self._on_connected)
        self.ws_client.disconnected.connect(self._on_disconnected)
        self.ws_client.reconnecting.connect(self._on_reconnecting)
        self.ws_client.frame_received.connect(self._on_frame_received)
        self.ws_client.info_received.connect(self._on_info_received)
        self.ws_client.approval_changed.connect(self._on_approval)
        self.ws_client.clipboard_received.connect(self._on_clipboard_received)
        self.ws_client.error_occurred.connect(self._on_error)

        # Give decoder a direct reference so WS thread can feed frames
        # without a Qt signal hop through the main thread
        self.ws_client.set_video_decoder(self.decoder)

        # Decoder → display
        self.decoder.frame_ready.connect(self._on_decoded_frame)

        # Audio toggle
        self.audio_btn.toggled.connect(self._on_audio_toggled)

    def _apply_stylesheet(self):
        on, off = (str(ICONS / f"switch-{s}.svg").replace("\\", "/") for s in ("on", "off"))
        # a stylesheet's generic monospace resolves to a proportional font on macOS
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
        self.setStyleSheet("""
            QMainWindow, #page, #content { background-color: #0D0D0F; }
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { background: transparent; width: 8px; }
            QScrollBar::handle:vertical { background-color: #2D2D44; border-radius: 4px; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            QLabel { color: #F3F4F6; font-size: 13px; }
            #brand { font-size: 18px; font-weight: 800; }
            #muted { color: #9CA3AF; }
            #small { color: #9CA3AF; font-size: 12px; }
            #section { color: #9CA3AF; font-size: 11px; font-weight: 700; }
            #name { font-size: 14px; font-weight: 700; }
            #cardName { font-size: 15px; font-weight: 700; }
            #mono, #monoSmall { font-family: '%s'; font-size: 12px; color: #9CA3AF; }
            #monoSmall { font-size: 11px; }
            #fieldLabel { font-size: 12px; font-weight: 600; color: #D1D5DB; }
            #title { font-size: 22px; font-weight: 800; }
            #body { font-size: 14px; color: #9CA3AF; }
            #rule { background-color: #2D2D44; }
            #card { background-color: #1A1A2E; border: 1px solid #2D2D44; border-radius: 12px; }
            #tile, #tileSmall { background-color: rgba(124, 58, 237, 46); border-radius: 10px; }
            #tileSmall { border-radius: 7px; }
            #step { background-color: #121219; border: 1px solid #1F1F30; border-radius: 10px; }
            #stepNum { background-color: rgba(124, 58, 237, 46); color: #B8A6F7; border-radius: 11px;
                       font-size: 12px; font-weight: 800; }
            #stepText { font-size: 12px; color: #D1D5DB; }
            #prefs { background-color: #121219; border: 1px solid #1F1F30; border-radius: 12px; }
            #prefTitle { font-size: 14px; font-weight: 600; }
            #footer { border-top: 1px solid #1F1F30; }
            #bar { border-bottom: 1px solid #1F1F30; }
            #navRow, #strip { border-top: 1px solid #1F1F30; }
            #waitPill { background-color: rgba(245, 158, 11, 41); color: #FCD34D; border-radius: 14px;
                        padding: 0 12px; font-size: 12px; font-weight: 700; }
            #livePill { background-color: rgba(16, 185, 129, 41); color: #6EE7B7; border-radius: 13px;
                        padding: 0 10px; font-size: 12px; font-weight: 700; }
            #livePill[reconnecting="true"] { background-color: rgba(245, 158, 11, 41); color: #FCD34D; }
            #glow { background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,
                    stop: 0 rgba(124, 58, 237, 130), stop: 1 rgba(124, 58, 237, 0)); }
            #phoneFrame { background-color: #121219; border: 3px solid #2D2D44; border-radius: 22px; }
            #prompt { background-color: #1A1A2E; border: 2px solid #7C3AED; border-radius: 12px; }
            #promptText { font-size: 9px; font-weight: 700; }
            #fakeDeny { background-color: #2D2D44; border-radius: 5px; }
            #fakeAllow { background-color: #7C3AED; border-radius: 5px; }
            QLineEdit {
                background-color: #121219;
                border: 1px solid #2D2D44;
                border-radius: 8px;
                padding: 0 12px;
                color: #F3F4F6;
                font-family: '%s';
                font-size: 14px;
                selection-background-color: #7C3AED;
            }
            QLineEdit:focus { border-color: #7C3AED; }
            QPushButton { font-size: 13px; font-weight: 700; color: #F3F4F6; }
            #primary { background-color: #7C3AED; border: none; border-radius: 8px; padding: 0 16px; color: #FFFFFF; }
            #primary:hover { background-color: #6D28D9; }
            #primary:pressed { background-color: #5B21B6; }
            #secondary { background-color: #1A1A2E; border: 1px solid #2D2D44; border-radius: 8px; }
            #secondary:hover { background-color: #23233A; }
            #ghost { background: transparent; border: 1px solid #2D2D44; border-radius: 8px; padding: 0 10px;
                     color: #D1D5DB; font-size: 12px; font-weight: 600; }
            #ghost:hover { background-color: #1A1A2E; }
            #ghost:disabled { color: #6B7280; }
            #link, #linkSmall { background: transparent; border: none; padding: 0; color: #A78BFA;
                                font-size: 12px; font-weight: 600; }
            #linkSmall { font-size: 11px; }
            #link:hover, #linkSmall:hover { color: #C4B5FD; }
            #pill { background-color: rgba(124, 58, 237, 46); border: none; border-radius: 12px;
                    padding: 0 10px 0 8px; color: #B8A6F7; font-size: 11px; font-weight: 700; }
            QToolButton { background: transparent; border: none; border-radius: 8px; color: #D1D5DB;
                          font-size: 11px; font-weight: 600; }
            QToolButton:hover { background-color: #1A1A2E; }
            QToolButton:checked { background-color: rgba(124, 58, 237, 46); }
            #switch::indicator { width: 44px; height: 26px; image: url(%s); }
            #switch::indicator:checked { image: url(%s); }
            QToolTip { background-color: #1A1A2E; color: #FFFFFF; border: 1px solid #3D3D5C; }
        """ % (mono, mono, off, on))

    def _set_status(self, text: str, color: str = GREEN):
        self.status_label.setText(f"<span style='color:{color}'>●</span>&nbsp; {html.escape(text)}")

    def _set_pill(self, text: str, reconnecting: bool):
        self.live_pill.setText(text)
        self.live_pill.setProperty("reconnecting", reconnecting)
        self.live_pill.style().unpolish(self.live_pill)  # QSS property selectors only re-match on polish
        self.live_pill.style().polish(self.live_pill)

    def _set_device(self, name: str, addr: str = ""):
        self.wait_name.setText(name)
        self.mirror_name.setText(name)
        self.wait_body.setText(f"A request is showing on {name}. Nothing is streamed until you allow it there.")
        if addr:
            self.wait_addr.setText(addr)
            self.mirror_addr.setText(addr)

    # ── Connection handlers ──

    def _on_connect_clicked(self):
        if self._connecting:
            self._disconnect()
            return
        ip = self.ip_input.text().strip()
        if not ip:
            self._set_status("Enter the phone's IP address", RED)
            return
        self._connect(ip)

    def _connect(self, ip: str, name: str = ""):
        port = self.port_input.text().strip() or "8765"
        self.ip_input.setText(ip)
        self._set_device(name or ip, f"{ip} · {port}")
        self._connecting = True
        self.connect_btn.setText("Cancel")
        self._set_status(f"Connecting to {ip}:{port}...", AMBER)
        self.ws_client.connect_to(f"wss://{ip}:{port}")

    def _disconnect(self):
        self.ws_client.disconnect()
        self._on_disconnected(True)  # between retries the client has no socket to report the close

    def _on_connected(self):
        self.connected = True
        QSettings().setValue("last_ip", self.ip_input.text().strip())
        self._set_status("Connected, waiting for the phone...", AMBER)
        self.decoder.start()

    def _on_disconnected(self, clean: bool = False):
        was_connected = self.connected
        self.connected = False
        self.decoder.stop()
        self.audio_player.stop()
        self.input_handler.reset()
        self.video_widget._pressing = False
        if not clean:  # the client keeps retrying
            if was_connected:
                self._set_status("Connection lost, reconnecting...", AMBER)
                self._set_pill(f"<span style='color:{AMBER}'>●</span>&nbsp;Reconnecting", True)
            return
        self._connecting = False
        self.connect_btn.setText("Connect to address")
        self._set_status("Disconnected", "#6B7280")
        self.stack.setCurrentWidget(self.connect_page)
        self._resolution = ""
        self.stats_label.setText("")
        self.audio_btn.setChecked(False)
        self.audio_btn.setEnabled(False)
        self.video_widget.image = None
        self.video_widget.update()
        self.setWindowTitle("RemotePhone")

    def _on_reconnecting(self, attempt: int):
        self._set_status(f"Reconnecting... (attempt {attempt})", AMBER)

    def _on_info_received(self, info: dict):
        device = info.get("device", "Unknown")
        sw = info.get("screenWidth", 0)
        sh = info.get("screenHeight", 0)
        android_ver = info.get("androidVersion", "?")

        self._set_device(device)
        self._resolution = f"{sw} × {sh}"
        self.setWindowTitle(f"RemotePhone · {device} (Android {android_ver})")

        if info.get("audioAvailable", False):
            self.audio_btn.setEnabled(True)
        self.input_handler.live = bool(info.get("liveTouch"))

    def _on_approval(self, state: str):
        if state == "pending":
            self._set_status("Waiting for approval on the phone...", AMBER)
            self.stack.setCurrentWidget(self.wait_page)
        elif state == "granted":
            self._show_mirror()

    def _show_mirror(self):
        self._set_status("Connected")
        self._set_pill(f"<span style='color:{GREEN}'>●</span>&nbsp;Live", False)
        self.stack.setCurrentWidget(self.mirror_page)
        self.video_widget.setFocus()

    def _on_clipboard_received(self, content: str):
        QApplication.clipboard().setText(content)
        self.stats_label.setText(f"Copied to clipboard ({len(content)} chars)")

    def _on_error(self, error: str):
        self._set_status(error, RED)

    # ── Network scan ──

    def _on_scan_clicked(self):
        port = int(self.port_input.text().strip() or "8765")
        self.scanner.start_scan(port)

    def _on_scan_started(self):
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("Scanning...")
        if not self.connected:
            self._set_status("Scanning local network...", AMBER)

    def _on_scan_complete(self, found: list):
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Scan again")
        while self.phone_list.count():
            self.phone_list.takeAt(0).widget().deleteLater()
        port = self.port_input.text().strip() or "8765"
        for ip, name in found:
            card, row = _frame("card", QHBoxLayout, (16, 14, 16, 14), 14)
            tile = QLabel()
            tile.setObjectName("tile")
            tile.setFixedSize(40, 40)
            tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tile.setPixmap(_icon("phone").pixmap(20, 20))
            text = QVBoxLayout()
            text.setSpacing(3)
            text.addWidget(_label(name, "cardName"))
            text.addWidget(_label(f"{ip} · port {port}", "mono"))
            btn = _button("Connect", "primary", 36)
            btn.clicked.connect(lambda _, i=ip, n=name: self._connect(i, n))
            row.addWidget(tile)
            row.addLayout(text, 1)
            row.addWidget(btn)
            self.phone_list.addWidget(card)
        if found:
            self.ip_input.setText(found[0][0])
        if self.connected:
            return

        if len(found) == 1:
            # Exactly one server found, auto-connect
            self._set_status(f"Found {found[0][1]} at {found[0][0]}")
            self._connect(*found[0])
        elif found:
            self._set_status(f"Found {len(found)} phones")
        else:
            self._set_status("No phone found, enter the address below", "#6B7280")

    # ── Update check ──

    def _check_update(self):
        try:
            with urllib.request.urlopen(PYPI_URL, timeout=3) as reply:
                if is_newer(json.load(reply)["info"]["version"], VERSION):
                    self.update_found.emit()
        except Exception:
            pass  # offline, no PyPI, or a version that is not plain dotted ints

    # ── Frame routing ──

    def _on_frame_received(self, frame_type: int, timestamp: int, data: bytes):
        # Video goes straight from the WS thread to the decoder; only audio arrives here
        if frame_type == FRAME_AUDIO_DATA:
            self.audio_player.feed(data)

    def _on_decoded_frame(self, image: QImage):
        self.video_widget.update_frame(image)
        self.frame_count += 1
        landscape = image.width() > image.height()
        if landscape != self._landscape:
            self._landscape = landscape
            self._fit_window(image.width(), image.height())

    def _fit_window(self, w: int, h: int):
        """Turn the window to match a rotated phone, keeping the video's long side."""
        if self.isFullScreen() or self.isMaximized():
            return
        vw = self.video_widget
        long_side = max(vw.width(), vw.height())
        new_w, new_h = (long_side, long_side * h // w) if w > h else (long_side * w // h, long_side)
        self.resize(self.width() - vw.width() + new_w, self.height() - vw.height() + new_h)

    def _update_fps(self):
        if self.connected:
            self.stats_label.setText(f"{self._resolution} · {self.frame_count} fps")
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
            self._toggle_fullscreen()
        else:
            super().keyPressEvent(event)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _send_command(self, cmd: dict):
        if self.connected:
            self.ws_client.send_command(cmd)

    def _show_shortcuts(self):
        rows = "".join(
            f"<tr><td style='padding-right:16px'><b>{k}</b></td><td>{a}</td></tr>"
            for k, a in SHORTCUTS
        )
        box = QMessageBox(self)
        box.setWindowTitle("Keyboard & mouse shortcuts")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"<table cellspacing='4'>{rows}</table>")
        box.exec()

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
