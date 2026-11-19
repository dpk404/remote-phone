"""
RemotePhone — Input Handler
Translates mouse and keyboard events from the Linux client into
the JSON control protocol for the Android phone.

When the phone supports live touch (Android 8+), a press, drag and release are sent
as they happen and the phone follows the pointer. Otherwise gestures are detected here:
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

_K = Qt.Key


class InputHandler:
    """Maps desktop input events to phone touch/key commands."""

    TAP_THRESHOLD = 10.0     # max pixel movement for a gesture to count as a tap
    TAP_TIME_MAX = 0.25      # max seconds for a tap
    LONG_PRESS_MIN = 0.5     # min seconds for a long press
    HSCROLL_BACK_THRESHOLD = 400.0  # cumulative horizontal scroll to trigger back (higher = less sensitive)
    VSCROLL_STEP = 60.0      # min accumulated vertical delta per scroll command (batches trackpad events)
    VSCROLL_GAIN = 2.5       # scroll speed: phone swipe distance per unit of wheel/trackpad travel

    SCROLL_COOLDOWN = 0.4    # seconds to ignore clicks after scroll (trackpad ghost taps)
    TRACKPAD_GAIN = 1.0      # live touch: 1.0 moves the picture exactly as far as the trackpad reports
    SCROLL_PRESS_TRAVEL = 48.0  # live touch: phone pixels a scroll must cover before the finger goes down

    # System keys → phone key actions
    KEY_ACTIONS = {
        _K.Key_Escape: "back",
        _K.Key_Home: "home",
        _K.Key_F2: "recents",
        _K.Key_F5: "notifications",
        _K.Key_F6: "quick_settings",
        _K.Key_F10: "power",
    }
    # Editing keys → commands
    KEY_COMMANDS = {
        _K.Key_Backspace: {"type": "backspace"},
        _K.Key_Delete: {"type": "delete"},
        _K.Key_Return: {"type": "text", "content": "\n"},
        _K.Key_Enter: {"type": "text", "content": "\n"},
    }
    # Ctrl+<key> → clipboard/selection commands
    CTRL_COMMANDS = {
        _K.Key_A: "select_all",
        _K.Key_C: "copy",
        _K.Key_V: "paste",
        _K.Key_X: "cut",
    }

    def __init__(self):
        self._press_x = 0.0
        self._press_y = 0.0
        self._press_time = 0.0
        self._last_x = 0.0
        self._last_y = 0.0
        self._moved = False
        self._hscroll_accum = 0.0   # accumulated horizontal scroll delta
        self._vscroll_accum = 0.0   # accumulated vertical scroll delta
        self._hscroll_time = 0.0    # last horizontal scroll timestamp
        self._hscroll_fired = False # back already sent for the current horizontal gesture
        self._last_scroll_time = 0.0  # tracks last scroll to suppress ghost taps
        self._suppressed = False      # whether current press was suppressed
        self.live = False             # phone takes touch down/move/up as they happen (its info says so)
        self._scroll_x = 0.0          # live touch: the finger a trackpad scroll is dragging,
        self._scroll_y = None         # None while it is up
        self._scroll_pending = 0.0    # scroll travel collected before the finger goes down

    def reset(self):
        """Clear all in-progress gesture and scroll state (call on disconnect)."""
        self.__init__()

    @staticmethod
    def _touch(action: str, x: float, y: float) -> dict:
        return {"type": "touch", "action": action, "x": round(x, 1), "y": round(y, 1)}

    def on_press(self, x: float, y: float) -> dict | None:
        """Record the start of a mouse press. Returns the touch-down command in live mode."""
        self._press_x = x
        self._press_y = y
        self._last_x = x
        self._last_y = y
        self._press_time = time.time()
        self._moved = False
        # Suppress ghost taps right after scrolling (trackpad artifact)
        self._suppressed = self._press_time - self._last_scroll_time < self.SCROLL_COOLDOWN
        if self.live and not self._suppressed:
            return self._touch("down", x, y)
        return None

    def on_move(self, x: float, y: float) -> dict | None:
        """Track mouse movement during a drag. Returns the touch-move command in live mode."""
        dx = abs(x - self._press_x)
        dy = abs(y - self._press_y)
        if dx > self.TAP_THRESHOLD or dy > self.TAP_THRESHOLD:
            self._moved = True
        self._last_x = x
        self._last_y = y
        if self.live and not self._suppressed:
            return self._touch("move", x, y)
        return None

    def on_release(self, x: float, y: float) -> dict | None:
        """
        Determine the gesture type from the press-move-release sequence.
        Returns a command dict or None.
        """
        # Drop release if the press was suppressed (ghost tap after scroll)
        if self._suppressed:
            self._suppressed = False
            return None

        if self.live:
            # The finger is already down on the phone, so it always comes back up
            if x < 0 or y < 0:
                x, y = self._last_x, self._last_y
            return self._touch("up", x, y)

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

    def _lift_scroll_finger(self) -> dict:
        cmd = self._touch("up", self._scroll_x, self._scroll_y)
        self._scroll_y = None
        self._scroll_pending = 0.0
        return cmd

    def on_scroll(self, x: float, y: float, dx: float, dy: float,
                  phase=Qt.ScrollPhase.NoScrollPhase, drag: float = 0.0,
                  height: float = 0.0) -> dict | list | None:
        """
        Convert scroll wheel into a scroll or back gesture.
        - Vertical scroll (dy) → normal page scroll
        - Horizontal scroll (dx from 2-finger trackpad swipe) → back gesture

        On a live-touch phone a trackpad scroll (one that reports phases) instead drags a finger:
        drag is how far the picture should move for this event, in phone pixels, and height is
        the phone screen height. The phone then scrolls with the fingers and flings on release.
        Starting that drag returns two commands (press, then move) as a list.
        """
        now = time.time()

        if self.live and height and phase != Qt.ScrollPhase.NoScrollPhase:
            down = self._scroll_y is not None
            if phase in (Qt.ScrollPhase.ScrollBegin, Qt.ScrollPhase.ScrollEnd):
                self._scroll_pending = 0.0
                if down:
                    return self._lift_scroll_finger()
            elif phase == Qt.ScrollPhase.ScrollMomentum and abs(dy) >= abs(dx):
                self._last_scroll_time = now
                return None  # the phone flings by itself once the finger is up
            elif down or (abs(dy) >= abs(dx) and abs(dy) >= 1):
                self._last_scroll_time = now
                if not down:
                    # Android reads a press that barely moves as a tap. So the finger goes down
                    # only once the scroll has travelled further than a tap can, and it moves
                    # that distance at once: a scroll too short for that does nothing at all.
                    self._scroll_pending += drag * self.TRACKPAD_GAIN
                    if abs(self._scroll_pending) < self.SCROLL_PRESS_TRAVEL:
                        return None
                    # Start away from the screen edges so the finger has room to travel
                    start = min(max(y, 0.25 * height), 0.75 * height)
                    self._scroll_x = x
                    self._scroll_y = min(max(start + self._scroll_pending, 0.05 * height), 0.95 * height)
                    self._scroll_pending = 0.0
                    return [self._touch("down", x, start), self._touch("move", x, self._scroll_y)]
                new_y = self._scroll_y + drag * self.TRACKPAD_GAIN
                if not 0.05 * height <= new_y <= 0.95 * height:
                    return self._lift_scroll_finger()  # out of screen: the next event presses again
                self._scroll_y = new_y
                return self._touch("move", self._scroll_x, new_y)

        # Trackpads report phases: fingers touching down starts a new gesture,
        # even mid-momentum-tail, so a second quick swipe is never swallowed
        # by the previous one's latch. Wheels report NoScrollPhase and rely on
        # the time-gap reset below instead.
        if phase == Qt.ScrollPhase.ScrollBegin:
            self._hscroll_accum = 0.0
            self._hscroll_fired = False
            self._vscroll_accum = 0.0

        # Horizontal scroll → accumulate for back gesture detection
        if abs(dx) > abs(dy) and abs(dx) >= 1:
            # Reset accumulator if too much time passed (new gesture)
            if now - self._hscroll_time > 0.5:
                self._hscroll_accum = 0.0
                self._hscroll_fired = False
            self._hscroll_time = now
            self._last_scroll_time = now

            # Fire back at most once per gesture: the swipe's remainder and the
            # trackpad's momentum events keep arriving after the threshold trips.
            if self._hscroll_fired:
                return None
            self._hscroll_accum += dx

            # Two-finger swipe right → back gesture (like browser back)
            if self._hscroll_accum > self.HSCROLL_BACK_THRESHOLD:
                self._hscroll_accum = 0.0
                self._hscroll_fired = True
                return {"type": "key", "action": "back"}
            return None

        # Vertical scroll → normal scroll (negate dy for traditional desktop direction).
        # Trackpads deliver a burst of tiny deltas; sent individually each becomes a
        # sub-20px swipe that Android reads as a tap, and every dispatch cancels the
        # previous gesture. Accumulate and emit one command per VSCROLL_STEP instead
        # (a mouse wheel notch is 120, so it still passes through immediately).
        if abs(dy) < 1:
            return None
        if now - self._last_scroll_time > 0.3:
            self._vscroll_accum = 0.0
        self._last_scroll_time = now
        self._vscroll_accum += dy
        if abs(self._vscroll_accum) < self.VSCROLL_STEP:
            return None
        step, self._vscroll_accum = self._vscroll_accum, 0.0
        return {
            "type": "scroll",
            "x": round(x, 1),
            "y": round(y, 1),
            "dx": round(dx, 1),
            "dy": round(-step * self.VSCROLL_GAIN, 1),
        }

    def on_key_press(self, event: QKeyEvent) -> dict | None:
        """
        Map keyboard keys to phone actions.
        System keys → key commands, printable characters → text input.
        """
        key = event.key()
        if key in self.KEY_ACTIONS:
            return {"type": "key", "action": self.KEY_ACTIONS[key]}
        if key in self.KEY_COMMANDS:
            return self.KEY_COMMANDS[key]
        if key in self.CTRL_COMMANDS and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return {"type": self.CTRL_COMMANDS[key]}

        # Printable text
        text = event.text()
        if text and text.isprintable():
            return {"type": "text", "content": text}

        return None
