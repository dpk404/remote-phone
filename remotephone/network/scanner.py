"""
RemotePhone — Network Scanner
Scans the local subnet for devices running a RemotePhone WebSocket server (port 8765).
Discovery is two-stage: a fast parallel TCP connect probe narrows the subnet to hosts
with the port open, then each candidate is verified with the RemotePhone WebSocket
hello/info handshake so unrelated services (or networks that ACK every address) are
not reported as devices.
"""

import json
import re
import socket
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import websocket
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger("scanner")

DEFAULT_PORT = 8765
CONNECT_TIMEOUT = 0.3  # seconds per TCP probe
VERIFY_TIMEOUT = 1.0   # seconds for the WebSocket handshake + info reply
MAX_WORKERS = 50       # parallel connection attempts (higher drops SYNs/handshakes to one phone)
VERIFY_RETRIES = 1     # a burst can drop the verify handshake even on the real server


_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _is_private(ip: str) -> bool:
    """RFC 1918 ranges only: the phone is always on one of these."""
    try:
        a, b = (int(p) for p in ip.split(".")[:2])
    except ValueError:
        return False
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def get_local_subnets() -> list[str]:
    """
    All local /24 prefixes (e.g. ['192.168.1.']) to scan.

    Uses the default-route interface plus every private address the machine
    holds, so a VPN owning the default route does not hide the LAN the phone
    is on. ponytail: parses `ip`/`ifconfig`/`ipconfig` output, the stdlib has
    no cross-platform interface-address API; switch to a lib only if a format
    stops matching.
    """
    prefixes = set()

    # Default-route interface (no shelling out; also covers odd setups)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # UDP connect sends nothing; it just picks the outbound interface
            ip = s.getsockname()[0]
            if _is_private(ip):
                prefixes.add(ip.rsplit(".", 1)[0] + ".")
    except OSError:
        pass

    # Every other interface (the LAN the VPN's default route masks)
    for cmd in (["ip", "-4", "-o", "addr"], ["ifconfig"], ["ipconfig"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.SubprocessError):
            continue  # command not on this OS; try the next
        prefixes.update(
            ip.rsplit(".", 1)[0] + "."
            for ip in _IPV4.findall(out)
            if _is_private(ip) and not ip.endswith(".0")
        )
        break  # first command that ran is authoritative

    return sorted(prefixes)


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
    for _ in range(1 + VERIFY_RETRIES):
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
        """Scan every local /24 subnet."""
        prefixes = get_local_subnets()
        if not prefixes:
            log.warning("Could not determine local subnet")
            self._scanning = False
            self.scan_complete.emit([])
            return

        log.info(f"Scanning {', '.join(p + '0/24' for p in prefixes)} for port {port}")

        def sweep() -> list[str]:
            hits = []
            # One subnet at a time: blasting every /24 at once overloads the phone's
            # radio and drops SYNs, so a scan across several interfaces misses it.
            for prefix in prefixes:
                targets = [f"{prefix}{i}" for i in range(1, 255)]  # skip .0 and .255
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                    for future in as_completed([pool.submit(probe_and_verify, ip, port) for ip in targets]):
                        result = future.result()
                        if result:
                            log.info(f"Found RemotePhone server at {result}:{port}")
                            hits.append(result)
            return hits

        # A lost SYN on WiFi can hide the phone; an empty result is worth one retry.
        found = sweep() or sweep()

        self._scanning = False
        self.scan_complete.emit(found)


if __name__ == "__main__":
    # ponytail check: classification and that a VPN default route no longer hides the LAN
    assert _is_private("192.168.1.6") and _is_private("10.8.0.33") and _is_private("172.16.5.4")
    assert not _is_private("8.8.8.8") and not _is_private("172.32.0.1") and not _is_private("169.254.1.1")
    subs = get_local_subnets()
    print("subnets to scan:", subs)
    assert all(s.endswith(".") and _is_private(s + "1") for s in subs), subs
    print("ok")
