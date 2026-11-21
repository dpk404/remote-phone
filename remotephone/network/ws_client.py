"""
RemotePhone: WebSocket Client
Handles the network connection to the Android phone's WebSocket server.
Runs in a background thread, emits Qt signals for thread-safe UI updates.
Auto-reconnects on connection loss with exponential backoff.
"""

import hashlib
import hmac
import json
import secrets
import socket
import ssl
import struct
import threading
import logging

from PyQt6.QtCore import QObject, QSettings, pyqtSignal

import websocket

log = logging.getLogger("ws_client")

# Frame type markers in the 9-byte binary header (must match the Android side)
FRAME_VIDEO_CONFIG = 0x00
FRAME_VIDEO_KEY = 0x01
FRAME_VIDEO_DELTA = 0x02
FRAME_AUDIO_CONFIG = 0x10
FRAME_AUDIO_DATA = 0x11

# Video frame types that bypass Qt signals and go directly to the decoder
_VIDEO_TYPES = {FRAME_VIDEO_CONFIG, FRAME_VIDEO_KEY, FRAME_VIDEO_DELTA}
_TYPE_NAMES = {FRAME_VIDEO_CONFIG: "CONFIG", FRAME_VIDEO_KEY: "KEY", FRAME_VIDEO_DELTA: "DELTA",
               FRAME_AUDIO_CONFIG: "AUDIO_CFG", FRAME_AUDIO_DATA: "AUDIO"}

# The phone serves a self-signed certificate. The desktop makes no trust decision about it:
# the phone's owner decides who may watch, and the answer to the phone's challenge is bound to
# the certificate we connected to, so a relay presenting its own certificate is denied.
SSL_OPT = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}

# WebSocketApp has no connect timeout of its own: it applies this default to the TCP connect, TLS handshake
# and upgrade, so a stalled handshake fails into the reconnect loop. _on_open clears it for the session.
CONNECT_TIMEOUT = 10
websocket.setdefaulttimeout(CONNECT_TIMEOUT)


def _phone_key(fingerprint: str, item: str) -> str:
    return f"phones/{fingerprint}/{item}"


def client_id(fingerprint: str) -> str:
    """Our identity towards this phone, made once per phone; the owner's Allow attaches a secret to it."""
    settings = QSettings()
    cid = settings.value(_phone_key(fingerprint, "id"))
    if not cid:
        cid = secrets.token_hex(16)
        settings.setValue(_phone_key(fingerprint, "id"), cid)
    return cid


# Reconnect settings
_RECONNECT_BASE = 1.0     # initial retry delay (seconds)
_RECONNECT_MAX = 10.0     # max retry delay
_RECONNECT_FACTOR = 1.5   # backoff multiplier


class WebSocketClient(QObject):
    """Thread-safe WebSocket client that bridges the phone connection to the Qt event loop."""

    # Signals (emitted from background thread, received on main thread)
    connected = pyqtSignal()
    disconnected = pyqtSignal(bool, str)  # clean (no auto-reconnect), the phone's close reason
    reconnecting = pyqtSignal(int)   # attempt number
    frame_received = pyqtSignal(int, int, bytes)  # frame_type, timestamp, payload (audio only)
    info_received = pyqtSignal(dict)
    approval_changed = pyqtSignal(str)  # "pending" while the phone's owner decides, then "granted"
    clipboard_received = pyqtSignal(str)
    audio_state_changed = pyqtSignal(bool)  # the phone's audio switch, whichever side flipped it
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ws = None
        self._thread = None
        self._url = None
        self._lock = threading.Lock()
        self._frame_count = 0
        self._video_decoder = None
        self._stop_event = threading.Event()  # set once the user no longer wants to stay connected
        self._close_frame = (None, None)  # (code, reason) of a close frame that arrived through on_error
        self._fingerprint = ""  # SHA-256 of the certificate of the phone we are connected to

    def set_video_decoder(self, decoder):
        """Set direct decoder reference for low-latency video frame routing."""
        self._video_decoder = decoder
        decoder.request_keyframe = lambda: self.send_command({"type": "request_keyframe"})

    def connect_to(self, url: str):
        """Start a background thread that connects to the WebSocket server."""
        self.disconnect()
        # Wait for the old thread to fully exit before starting a new one,
        # so both threads never race over self._ws at the same time.
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._url = url
        self._frame_count = 0
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_with_reconnect, daemon=True, name="WSClient")
        self._thread.start()

    def _run_with_reconnect(self):
        """Connection loop with auto-reconnect on failure."""
        attempt = 0
        delay = _RECONNECT_BASE

        while not self._stop_event.is_set() and not self._stale():
            try:
                self._ws = websocket.WebSocketApp(
                    self._url,
                    on_open=self._on_open,
                    on_data=self._on_data,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws.run_forever(
                    sslopt=SSL_OPT,
                    ping_interval=5,
                    ping_timeout=3,
                    skip_utf8_validation=True,
                )
            except Exception as e:
                log.error(f"WebSocket run error: {e}")

            # If we get here, connection closed or failed
            if self._stop_event.is_set() or self._stale():
                break

            attempt += 1
            log.info(f"Reconnecting in {delay:.1f}s (attempt {attempt})...")
            self.reconnecting.emit(attempt)

            # Wait with interruptible sleep
            if self._stop_event.wait(timeout=delay):
                break  # stop was requested during sleep

            delay = min(delay * _RECONNECT_FACTOR, _RECONNECT_MAX)

    def _stale(self) -> bool:
        # an attempt cancelled mid-handshake cannot be interrupted, so it may return after a newer connect_to
        return self._thread is not None and threading.current_thread() is not self._thread

    def _on_open(self, ws):
        log.info(f"Connected to {self._url}")
        ws.sock.settimeout(None)  # an established stream may sit idle between pings
        self.connected.emit()
        self._fingerprint = hashlib.sha256(ws.sock.sock.getpeercert(binary_form=True)).hexdigest().upper()
        ws.send(json.dumps({
            "type": "hello",
            "version": 1,
            "client": f"RemotePhone on {socket.gethostname()}",  # shown in the phone's Allow prompt
            "id": client_id(self._fingerprint),
            "maxWidth": 1920,
            "maxHeight": 1920,
            "maxFps": 60,
            "videoBitrate": 20_000_000,  # a ceiling: the phone encodes at a variable rate below it
            "audioBitrate": 128_000,
        }))

    def _on_data(self, ws, data, data_type, continue_flag):
        """
        Called for every message with explicit data type.
        data_type: websocket.ABNF.OPCODE_TEXT (1) or OPCODE_BINARY (2)
        """
        if data_type == websocket.ABNF.OPCODE_BINARY:
            self._handle_binary(data)
        elif data_type == websocket.ABNF.OPCODE_TEXT:
            if isinstance(data, bytes):
                data = data.decode('utf-8', errors='replace')
            self._handle_text(data)

    def _handle_binary(self, data: bytes):
        """Parse the 9-byte header and route the frame."""
        if len(data) < 9:
            return

        frame_type = data[0]
        timestamp = struct.unpack('>I', data[1:5])[0]
        payload_size = struct.unpack('>I', data[5:9])[0]
        payload = data[9:9 + payload_size]

        self._frame_count += 1
        if self._frame_count <= 5 or self._frame_count % 300 == 0:
            log.info(f"Frame #{self._frame_count}: type={_TYPE_NAMES.get(frame_type, hex(frame_type))}, "
                     f"ts={timestamp}, payload={len(payload)}B")

        # Video frames: feed directly to decoder (no Qt signal hop)
        if frame_type in _VIDEO_TYPES and self._video_decoder is not None:
            self._video_decoder.feed_frame(frame_type, timestamp, payload)
        else:
            # Audio and other frames go through Qt signal
            self.frame_received.emit(frame_type, timestamp, payload)

    def _handle_text(self, message: str):
        """Parse JSON text messages (device info, errors, etc.)."""
        log.info(f"Text message: {message[:200]}")
        try:
            obj = json.loads(message)
            msg_type = obj.get("type", "")

            if msg_type == "info":
                self.info_received.emit(obj)
            elif msg_type == "approval":
                if obj.get("secret"):
                    # Handed over once, on the owner's Allow; it proves us on every later visit
                    QSettings().setValue(_phone_key(self._fingerprint, "secret"), obj["secret"])
                self.approval_changed.emit(obj.get("state", ""))
            elif msg_type == "challenge":
                secret = str(QSettings().value(_phone_key(self._fingerprint, "secret"), ""))
                mac = hmac.new(secret.encode(), f"{obj.get('nonce', '')}|{self._fingerprint}".encode(),
                               hashlib.sha256).hexdigest()
                self.send_command({"type": "auth", "mac": mac})
            elif msg_type == "clipboard":
                self.clipboard_received.emit(obj.get("content", ""))
            elif msg_type == "audio":
                self.audio_state_changed.emit(obj.get("enabled") is True)
            elif msg_type == "error":
                self.error_occurred.emit(obj.get("message", "Unknown error"))
        except json.JSONDecodeError:
            pass

    def _on_error(self, ws, error):
        if self._stale():
            return
        # websocket-client 1.9 hands the server's close frame to on_error and then calls on_close(None, None)
        if isinstance(error, websocket.ABNF) and error.opcode == websocket.ABNF.OPCODE_CLOSE:
            if len(error.data) >= 2:
                self._close_frame = (struct.unpack("!H", error.data[:2])[0],
                                     error.data[2:].decode("utf-8", errors="replace"))
            return
        err_str = str(error)
        # Don't spam "Connection refused" during reconnect attempts
        if "Connection refused" in err_str and not self._stop_event.is_set():
            log.debug(f"WebSocket error (will retry): {error}")
        else:
            log.error(f"WebSocket error: {error}")
            self.error_occurred.emit(err_str)

    def _on_close(self, ws, close_status_code, close_msg):
        if self._stale():
            return
        if close_status_code is None:
            close_status_code, close_msg = self._close_frame
        self._close_frame = (None, None)
        log.info(f"Disconnected (code={close_status_code}, msg={close_msg})")
        # Clean if the server stopped on purpose (1000, or 1001 from released 2.x apps on Stop mirroring),
        # the phone's owner rejected us (1008, policy violation), or we initiated the close:
        # none is worth a reconnect loop
        if close_status_code == 1001 and not close_msg:
            close_msg = "Mirroring stopped on the phone"
        rejected = close_status_code == 1008
        clean = close_status_code in (1000, 1001) or rejected or self._stop_event.is_set()
        if clean:
            self._stop_event.set()
        with self._lock:
            self._ws = None
        self.disconnected.emit(clean, close_msg or "")
        if rejected:
            self.error_occurred.emit(close_msg or "Rejected on the phone")

    def send_command(self, command: dict):
        """Send a JSON control command to the phone."""
        with self._lock:
            if self._ws and self._ws.sock and self._ws.sock.connected:
                try:
                    self._ws.send(json.dumps(command))
                except Exception as e:
                    log.warning(f"Send failed: {e}")

    def disconnect(self):
        """Cleanly close the WebSocket connection and stop reconnect loop."""
        self._stop_event.set()
        with self._lock:
            if self._ws:
                try:
                    self._ws.close()
                except Exception:
                    pass
                self._ws = None
