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

assert h.on_scroll(5, 5, 0, -120) == {"type": "scroll", "x": 5, "y": 5, "dx": 0, "dy": 120}
h.on_press(5, 5)
assert h.on_release(5, 5) is None  # click right after a scroll is a trackpad ghost tap

assert h.on_key_press(key(Qt.Key.Key_Escape)) == {"type": "key", "action": "back"}
assert h.on_key_press(key(Qt.Key.Key_Return)) == {"type": "text", "content": "\n"}
assert h.on_key_press(key(Qt.Key.Key_V, "\x16", Qt.KeyboardModifier.ControlModifier)) == {"type": "paste"}
assert h.on_key_press(key(Qt.Key.Key_A, "a")) == {"type": "text", "content": "a"}
assert h.on_key_press(key(Qt.Key.Key_F11)) is None

print("ok")
