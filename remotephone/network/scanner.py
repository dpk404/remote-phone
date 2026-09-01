"""
RemotePhone — Network Scanner
Scans the local subnet for devices running a RemotePhone WebSocket server (port 8765).
Discovery is two-stage: a fast parallel TCP connect probe narrows the subnet to hosts
with the port open, then each candidate is verified with the RemotePhone WebSocket
hello/info handshake so unrelated services (or networks that ACK every address) are
not reported as devices.
"""

import json
import socket
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import websocket
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger("scanner")

DEFAULT_PORT = 8765
CONNECT_TIMEOUT = 0.3  # seconds per TCP probe
VERIFY_TIMEOUT = 1.0   # seconds for the WebSocket handshake + info reply
MAX_WORKERS = 80       # parallel connection attempts


def get_local_subnet() -> str | None:
    """Get the /24 prefix (e.g. '192.168.1.') of the interface holding the default route."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # UDP connect sends nothing; it just picks the outbound interface
            return s.getsockname()[0].rsplit(".", 1)[0] + "."
    except OSError:
        return None


def probe_host(ip: str, port: int) -> str | None:
    """Try to TCP connect to ip:port. Returns ip if open, None otherwise."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(CONNECT_TIMEOUT)
        result = sock.connect_ex((ip, port))
        sock.close()
        if result == 0:
            return ip
    except Exception:
        pass
    return None


def is_remotephone_server(ip: str, port: int) -> bool:
    """
    Confirm the host is an actual RemotePhone server (not just any open port).

    Performs the WebSocket handshake, sends a `hello`, and waits for the server's
    `info` reply. A bare open port — or a network/firewall that ACKs every address —
    will fail the handshake or never send a valid `info`, so it is rejected.
    """
    ws = None
    try:
        ws = websocket.create_connection(f"ws://{ip}:{port}", timeout=VERIFY_TIMEOUT)
        ws.send(json.dumps({"type": "hello", "version": 1, "client": "RemotePhone-Scan"}))
        # On connect the server may first push a binary video-config frame; the
        # text `info` reply follows the hello. Skip a few non-text frames to find it.
        for _ in range(4):
            msg = ws.recv()
            if isinstance(msg, str) and msg:
                try:
                    obj = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "info":
                    return True
        return False
    except Exception:
        return False
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def probe_and_verify(ip: str, port: int) -> str | None:
    """Fast TCP probe, then verify the host actually speaks the RemotePhone protocol."""
    if probe_host(ip, port) is None:
        return None
    if is_remotephone_server(ip, port):
        return ip
    return None


class NetworkScanner(QObject):
    """Scans local network for RemotePhone servers. Emits results via Qt signals."""

    scan_complete = pyqtSignal(list)  # list of IPs found
    scan_started = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scanning = False

    def start_scan(self, port: int = DEFAULT_PORT):
        """Start a background subnet scan for open port."""
        if self._scanning:
            return
        self._scanning = True
        threading.Thread(target=self._scan, args=(port,), daemon=True, name="NetScanner").start()
        self.scan_started.emit()

    def _scan(self, port: int):
        """Scan the local /24 subnet."""
        prefix = get_local_subnet()
        if not prefix:
            log.warning("Could not determine local subnet")
            self._scanning = False
            self.scan_complete.emit([])
            return

        log.info(f"Scanning {prefix}0/24 for port {port}")
        found = []
        targets = [f"{prefix}{i}" for i in range(1, 255)]  # skip .0 and .255

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for future in as_completed([pool.submit(probe_and_verify, ip, port) for ip in targets]):
                result = future.result()
                if result:
                    log.info(f"Found RemotePhone server at {result}:{port}")
                    found.append(result)

        self._scanning = False
        self.scan_complete.emit(found)
