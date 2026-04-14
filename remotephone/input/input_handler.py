"""
RemotePhone — Input Handler
Translates mouse and keyboard events from the Linux client into
the JSON control protocol for the Android phone.

Gesture detection:
  - Click (< 10px movement, < 250ms) → tap
  - Click + drag → swipe
  - Long click (> 500ms, no movement) → long_press
  - Vertical scroll → scroll
  - Horizontal scroll (2-finger swipe on trackpad) → back gesture
  - Middle-click → back
  - Right-click → back (handled in VideoWidget)
  - Keyboard → key actions or text input
"""

import time
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent


class InputHandler:
    """Maps desktop input events to phone touch/key commands."""

    TAP_THRESHOLD = 10.0     # max pixel movement for a gesture to count as a tap
    TAP_TIME_MAX = 0.25      # max seconds for a tap
    LONG_PRESS_MIN = 0.5     # min seconds for a long press
    HSCROLL_BACK_THRESHOLD = 400.0  # cumulative horizontal scroll to trigger back (higher = less sensitive)

    SCROLL_COOLDOWN = 0.4    # seconds to ignore clicks after scroll (trackpad ghost taps)

    def __init__(self):
        self._press_x = 0.0
        self._press_y = 0.0
        self._press_time = 0.0
        self._last_x = 0.0
        self._last_y = 0.0
        self._moved = False
        self._hscroll_accum = 0.0   # accumulated horizontal scroll delta
        self._hscroll_time = 0.0    # last horizontal scroll timestamp
        self._last_scroll_time = 0.0  # tracks last scroll to suppress ghost taps
        self._suppressed = False      # whether current press was suppressed

    def on_press(self, x: float, y: float) -> dict | None:
        """Record the start of a mouse press."""
        self._press_x = x
        self._press_y = y
        self._last_x = x
        self._last_y = y
        self._press_time = time.time()
        self._moved = False

        # Suppress ghost taps right after scrolling (trackpad artifact)
        if self._press_time - self._last_scroll_time < self.SCROLL_COOLDOWN:
            self._suppressed = True
            return None

        self._suppressed = False
        return None

    def on_move(self, x: float, y: float):
        """Track mouse movement during a drag."""
        dx = abs(x - self._press_x)
        dy = abs(y - self._press_y)
        if dx > self.TAP_THRESHOLD or dy > self.TAP_THRESHOLD:
            self._moved = True
        self._last_x = x
        self._last_y = y

    def on_release(self, x: float, y: float) -> dict | None:
        """
        Determine the gesture type from the press-move-release sequence.
        Returns a command dict or None.
        """
        # Drop release if the press was suppressed (ghost tap after scroll)
        if self._suppressed:
            self._suppressed = False
            return None

        # Also suppress if a scroll happened between press and release
        now = time.time()
        if now - self._last_scroll_time < self.SCROLL_COOLDOWN:
            return None

        elapsed = now - self._press_time

        # If release happened outside the display, use last known position
        if x < 0 or y < 0:
            x = self._last_x
            y = self._last_y

        if self._moved:
            # Swipe: drag from press point to release point
            duration = max(int(elapsed * 1000), 100)
            return {
                "type": "swipe",
                "x1": round(self._press_x, 1),
                "y1": round(self._press_y, 1),
                "x2": round(x, 1),
                "y2": round(y, 1),
                "duration": min(duration, 2000),
            }
        elif elapsed >= self.LONG_PRESS_MIN:
            # Long press: held in place
            return {
                "type": "long_press",
                "x": round(self._press_x, 1),
                "y": round(self._press_y, 1),
                "duration": int(elapsed * 1000),
            }
        else:
            # Tap: quick click
            return {
                "type": "tap",
                "x": round(self._press_x, 1),
                "y": round(self._press_y, 1),
            }

    def on_scroll(self, x: float, y: float, dx: float, dy: float) -> dict | None:
        """
        Convert scroll wheel into a scroll or back gesture.
        - Vertical scroll (dy) → normal page scroll
        - Horizontal scroll (dx from 2-finger trackpad swipe) → back gesture
        """
        now = time.time()

        # Horizontal scroll → accumulate for back gesture detection
        if abs(dx) > abs(dy) and abs(dx) >= 1:
            # Reset accumulator if too much time passed (new gesture)
            if now - self._hscroll_time > 0.5:
                self._hscroll_accum = 0.0
            self._hscroll_time = now
            self._last_scroll_time = now
            self._hscroll_accum += dx

            # Two-finger swipe right → back gesture (like browser back)
            if self._hscroll_accum > self.HSCROLL_BACK_THRESHOLD:
                self._hscroll_accum = 0.0
                return {"type": "key", "action": "back"}
            return None

        # Vertical scroll → normal scroll (negate dy for traditional desktop direction)
        if abs(dy) < 1:
            return None
        self._last_scroll_time = now
        return {
            "type": "scroll",
            "x": round(x, 1),
            "y": round(y, 1),
            "dx": round(dx, 1),
            "dy": round(-dy, 1),
        }

    def on_key_press(self, event: QKeyEvent) -> dict | None:
        """
        Map keyboard keys to phone actions.
        System keys → key commands, printable characters → text input.
        """
        key = event.key()

        # System key mappings
        key_map = {
            Qt.Key.Key_Escape: "back",
            Qt.Key.Key_Home: "home",
            Qt.Key.Key_F2: "recents",
            Qt.Key.Key_F5: "notifications",
            Qt.Key.Key_F6: "quick_settings",
            Qt.Key.Key_F10: "power",
        }

        if key in key_map:
            return {"type": "key", "action": key_map[key]}

        # Backspace → delete last character in focused text field
        if key == Qt.Key.Key_Backspace:
            return {"type": "backspace"}

        # Delete key
        if key == Qt.Key.Key_Delete:
            return {"type": "delete"}

        # Enter / Return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return {"type": "text", "content": "\n"}

        # Select all (Ctrl+A)
        if key == Qt.Key.Key_A and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return {"type": "select_all"}

        # Copy (Ctrl+C)
        if key == Qt.Key.Key_C and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return {"type": "copy"}

        # Paste (Ctrl+V)
        if key == Qt.Key.Key_V and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return {"type": "paste"}

        # Cut (Ctrl+X)
        if key == Qt.Key.Key_X and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return {"type": "cut"}

        # Printable text
        text = event.text()
        if text and text.isprintable():
            return {"type": "text", "content": text}

        return None
