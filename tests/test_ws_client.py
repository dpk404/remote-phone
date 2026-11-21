"""Smallest checks for how the client reads a closed connection, fed what websocket-client passes its callbacks.
Run from the repo root:  python -m tests.test_ws_client
"""

import struct
import threading

import websocket

from remotephone.network.ws_client import WebSocketClient

c = WebSocketClient()
closes, errors = [], []
c.disconnected.connect(lambda clean, reason: closes.append((clean, reason)))
c.error_occurred.connect(errors.append)


def close_frame(code, reason):
    return websocket.ABNF(1, 0, 0, 0, websocket.ABNF.OPCODE_CLOSE, 0, struct.pack("!H", code) + reason.encode())


# connect_to starts with disconnect(); a later drop of the new connection must still reconnect
c.disconnect()
c._stop_event.clear()
c._on_close(None, None, None)
assert closes.pop() == (False, "") and not c._stop_event.is_set()

# websocket-client 1.9: the close frame comes through on_error, then on_close(None, None)
c._on_error(None, close_frame(1008, "Rejected on the phone"))
c._on_close(None, None, None)
assert closes.pop() == (True, "Rejected on the phone") and errors == ["Rejected on the phone"]

# older releases pass the code and reason to on_close
c._stop_event.clear()
errors.clear()
c._on_close(None, 1000, "Disconnected on the phone")
assert closes.pop() == (True, "Disconnected on the phone") and errors == []

# released 2.x apps close with 1001 and no reason on Stop mirroring
c._stop_event.clear()
c._on_error(None, close_frame(1001, ""))
c._on_close(None, None, None)
assert closes.pop() == (True, "Mirroring stopped on the phone") and errors == []
c._stop_event.clear()
c._on_close(None, 1001, "")
assert closes.pop() == (True, "Mirroring stopped on the phone") and errors == []

# run_forever takes its connect timeout from the library default; without it a stalled handshake never retries
assert websocket.getdefaulttimeout() == 10

# an attempt that outlives a newer connect_to stays silent
c._thread = threading.Thread()
c._on_close(None, 1000, "")
assert closes == []
# and never dials again once it wakes from its retry sleep
c._stop_event.clear()
c._run_with_reconnect()
assert c._ws is None

# the phone's audio switch reaches the UI; only a real true turns it on
audio = []
c.audio_state_changed.connect(audio.append)
c._handle_text('{"type":"audio","enabled":true}')
c._handle_text('{"type":"audio","enabled":"false"}')
assert audio == [True, False]

# so does whether the phone can apply input, read just as strictly
control = []
c.control_state_changed.connect(control.append)
c._handle_text('{"type":"control","enabled":false}')
c._handle_text('{"type":"control","enabled":true}')
c._handle_text('{"type":"control","enabled":"true"}')
assert control == [False, True, False]

print("ws_client OK")
