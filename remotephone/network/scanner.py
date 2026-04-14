"""
RemotePhone — Network Scanner
Scans the local subnet for devices with RemotePhone WebSocket server (port 8765).
Uses parallel TCP connect probes for fast discovery.
"""

import socket
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger("scanner")

DEFAULT_PORT = 8765
CONNECT_TIMEOUT = 0.3  # seconds per probe
MAX_WORKERS = 80       # parallel connection attempts


def get_local_subnets() -> list[str]:
    """Get the local IP prefixes (e.g., ['192.168.1.']) from all non-loopback interfaces."""
    prefixes = []
    try:
        # Get all IPs this machine is bound to
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127."):
                continue
            # Extract /24 prefix
            parts = ip.split(".")
            prefix = ".".join(parts[:3]) + "."
            if prefix not in prefixes:
                prefixes.append(prefix)
    except Exception:
        pass

    # Fallback: use the default route interface
    if not prefixes:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            parts = ip.split(".")
            prefixes.append(".".join(parts[:3]) + ".")
        except Exception:
            pass

    return prefixes


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


class NetworkScanner(QObject):
    """Scans local network for RemotePhone servers. Emits results via Qt signals."""

    scan_complete = pyqtSignal(list)  # list of IPs found
    scan_started = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._scanning = False

    def start_scan(self, port: int = DEFAULT_PORT):
        """Start a background subnet scan for open port."""
        if self._scanning:
            return
        self._scanning = True
        self._thread = threading.Thread(
            target=self._scan, args=(port,), daemon=True, name="NetScanner"
        )
        self._thread.start()
        self.scan_started.emit()

    def _scan(self, port: int):
        """Scan all /24 subnets this machine belongs to."""
        prefixes = get_local_subnets()
        if not prefixes:
            log.warning("Could not determine local subnet")
            self._scanning = False
            self.scan_complete.emit([])
            return

        log.info(f"Scanning subnets: {prefixes} for port {port}")
        found = []

        # Build list of all IPs to scan (skip .0 and .255)
        targets = []
        for prefix in prefixes:
            for i in range(1, 255):
                targets.append(f"{prefix}{i}")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(probe_host, ip, port): ip for ip in targets}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    log.info(f"Found RemotePhone server at {result}:{port}")
                    found.append(result)

        self._scanning = False
        self.scan_complete.emit(found)
