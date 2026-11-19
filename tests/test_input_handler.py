"""Smallest check that gesture classification and key mapping hold.
Run from the repo root:  python -m tests.test_input_handler
"""

from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QKeyEvent

from remotephone.input.input_handler import InputHandler


def key(k, text="", mods=Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(QEvent.Type.KeyPress, k, mods, text)


h = InputHandler()

h.on_press(10, 20)
assert h.on_release(12, 21)["type"] == "tap"

h.on_press(10, 20)
h.on_move(200, 20)
assert h.on_release(200, 20)["type"] == "swipe"

h.on_press(10, 20)
h._press_time -= 1.0  # pretend the button was held for a second
assert h.on_release(10, 20)["type"] == "long_press"

assert h.on_scroll(5, 5, 0, -120) == {"type": "scroll", "x": 5, "y": 5, "dx": 0, "dy": 120 * h.VSCROLL_GAIN}

# trackpad burst: many tiny deltas batch into few large commands, never tap-sized ones
burst = [h.on_scroll(5, 5, 0, -6) for _ in range(30)]
sent = [c for c in burst if c]
assert 2 <= len(sent) <= 4 and all(abs(c["dy"]) >= h.VSCROLL_STEP * h.VSCROLL_GAIN for c in sent), sent
h.on_press(5, 5)
assert h.on_release(5, 5) is None  # click right after a scroll is a trackpad ghost tap

# one long right swipe with momentum tail fires back exactly once
backs = [h.on_scroll(5, 5, 60, 0) for _ in range(20)]
assert [c for c in backs if c] == [{"type": "key", "action": "back"}]
h._hscroll_time -= 1.0  # gesture gap: a fresh swipe may fire again
assert [c for c in (h.on_scroll(5, 5, 60, 0) for _ in range(8)) if c] == [{"type": "key", "action": "back"}]

# a second quick swipe mid-momentum: ScrollBegin resets the latch immediately
h.on_scroll(5, 5, 0, 0, Qt.ScrollPhase.ScrollBegin)
assert [c for c in (h.on_scroll(5, 5, 60, 0, Qt.ScrollPhase.ScrollUpdate) for _ in range(8)) if c] \
    == [{"type": "key", "action": "back"}]

assert h.on_key_press(key(Qt.Key.Key_Escape)) == {"type": "key", "action": "back"}
assert h.on_key_press(key(Qt.Key.Key_Return)) == {"type": "text", "content": "\n"}
assert h.on_key_press(key(Qt.Key.Key_V, "\x16", Qt.KeyboardModifier.ControlModifier)) == {"type": "paste"}
assert h.on_key_press(key(Qt.Key.Key_A, "a")) == {"type": "text", "content": "a"}
assert h.on_key_press(key(Qt.Key.Key_F11)) is None

# live touch: the phone gets the press, each move and the release as they happen
live = InputHandler()
live.live = True
assert live.on_press(10, 20) == {"type": "touch", "action": "down", "x": 10, "y": 20}
assert live.on_move(15, 30) == {"type": "touch", "action": "move", "x": 15, "y": 30}
assert live.on_release(-1, -1) == {"type": "touch", "action": "up", "x": 15, "y": 30}  # released outside the picture
live.on_scroll(5, 5, 0, -120)
assert live.on_press(5, 5) is None and live.on_move(6, 6) is None and live.on_release(6, 6) is None  # ghost tap

# live touch + trackpad: a two-finger scroll drags a finger that follows it and lifts at the end
P = Qt.ScrollPhase
pad = InputHandler()
pad.live = True
assert pad.on_scroll(500, 1200, 0, 0, P.ScrollBegin, 0, 2400) is None
# a scroll too short to be told from a tap sends nothing: no press, so no click on the phone
assert pad.on_scroll(500, 1200, 0, 8, P.ScrollUpdate, 20, 2400) is None
assert pad.on_scroll(500, 1200, 0, 0, P.ScrollEnd, 0, 2400) is None
# once it has travelled far enough the finger goes down and moves that distance in one go
assert pad.on_scroll(500, 1200, 0, 8, P.ScrollUpdate, 30, 2400) is None
assert pad.on_scroll(500, 1200, 0, 8, P.ScrollUpdate, 30, 2400) == [
    {"type": "touch", "action": "down", "x": 500, "y": 1200},
    {"type": "touch", "action": "move", "x": 500, "y": 1260},
]
assert pad.on_scroll(500, 1200, 0, 8, P.ScrollUpdate, 30, 2400) == {"type": "touch", "action": "move", "x": 500, "y": 1290}
assert pad.on_scroll(500, 1200, 3, 8, P.ScrollUpdate, -80, 2400)["y"] == 1210
assert pad.on_scroll(500, 1200, 0, 0, P.ScrollEnd, 0, 2400) == {"type": "touch", "action": "up", "x": 500, "y": 1210}
assert pad.on_scroll(500, 1200, 0, 5, P.ScrollMomentum, 20, 2400) is None  # the phone flings by itself
# running out of screen lifts the finger; pressing again waits for enough travel, away from the edge
assert pad.on_scroll(500, 2300, 0, 8, P.ScrollUpdate, 60, 2400)[1]["y"] == 1860
assert pad.on_scroll(500, 2300, 0, 8, P.ScrollUpdate, 600, 2400)["action"] == "up"
assert pad.on_scroll(500, 2300, 0, 8, P.ScrollUpdate, 30, 2400) is None
assert pad.on_scroll(500, 2300, 0, 8, P.ScrollUpdate, 30, 2400)[0] == {"type": "touch", "action": "down", "x": 500, "y": 1800}
pad.on_scroll(500, 2300, 0, 0, P.ScrollEnd, 0, 2400)
# a mouse wheel reports no phases and keeps the stepped scroll; so does a phone without live touch
assert pad.on_scroll(5, 5, 0, -120, P.NoScrollPhase, -300, 2400)["type"] == "scroll"
old_phone = InputHandler()
assert old_phone.on_scroll(5, 5, 0, -120, P.ScrollUpdate, -300, 2400)["type"] == "scroll"

live.reset()
assert not live.live  # a new connection must be told again

print("ok")
