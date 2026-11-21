"""
RemotePhone: H.264 Video Decoder
Decodes H.264 NAL units from the phone into QImage frames using PyAV (FFmpeg).
Runs a decode loop in a background thread with aggressive frame dropping for
minimal latency real-time display.
"""

import logging
import threading
import time
from collections import deque

import av
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage

from remotephone.network.ws_client import FRAME_VIDEO_CONFIG, FRAME_VIDEO_KEY, FRAME_VIDEO_DELTA

log = logging.getLogger("video_decoder")

# Performance: drop frames when queue exceeds this depth
_DROP_THRESHOLD = 3
# Keyframes are rare (the phone sends one about every 10 s), so a decoder this far behind
# asks for one rather than wait; the drop above then skips straight to it
_KEYFRAME_REQUEST_THRESHOLD = 30

# Frames are shown at the phone's capture spacing, not at their network arrival spacing.
# This is the most a frame is held to absorb jitter, so it is also the latency it can add;
# raise it on a jittery link, set it to 0 to paint on arrival.
_JITTER_BUFFER = 0.015
# Per-frame upward creep of the fastest-delivery estimate, so clock skew between phone and
# desktop or one unusually fast frame cannot pin it forever.
_LEAD_DRIFT = 0.0002


class VideoDecoder(QObject):
    """
    Receives H.264 encoded frames, decodes them via FFmpeg,
    and emits QImage frames for display.
    """

    frame_ready = pyqtSignal(QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._running = False
        self._frame_queue = deque()
        self._condition = threading.Condition()
        self._config_data = None  # SPS/PPS bytes
        self._decoded_count = 0
        self._lead = None  # smallest (arrival time - phone timestamp) seen: the fastest delivery
        self.request_keyframe = None  # set by the connection: asks the phone for a keyframe
        self._keyframe_asked_at = 0.0

    def start(self):
        """Start the decoder thread."""
        if self._running:
            return
        self._running = True
        self._decoded_count = 0
        self._lead = None
        # Do not clear _config_data or the queue here: the server pushes the cached
        # SPS/PPS the instant the socket opens, which can beat the queued Qt
        # 'connected' signal that triggers this start(). stop() does the clearing.
        self._thread = threading.Thread(
            target=self._decode_loop, daemon=True, name="VideoDecoder"
        )
        self._thread.start()
        log.info("Decoder thread started")

    def stop(self):
        """Stop the decoder thread."""
        self._running = False
        with self._condition:
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        self._config_data = None
        with self._condition:
            self._frame_queue.clear()
        log.info("Decoder thread stopped")

    def feed_frame(self, frame_type: int, timestamp: int, data: bytes):
        """Feed a raw H.264 frame into the decode queue (thread-safe, called from WS thread)."""
        if frame_type == FRAME_VIDEO_CONFIG:
            self._config_data = data
            log.info(f"Stored SPS/PPS config: {len(data)} bytes")

        with self._condition:
            self._frame_queue.append((frame_type, timestamp, data))
            self._condition.notify()

    def _hold(self, timestamp_ms: int, now: float) -> float:
        """Seconds to hold a decoded frame so it leaves at the phone's capture spacing."""
        lead = now - timestamp_ms / 1000
        if self._lead is None or abs(lead - self._lead) > 1:
            self._lead = lead  # new stream, a stall, or the 32-bit timestamp wrapped
        self._lead = min(self._lead + _LEAD_DRIFT, lead)
        return max(0.0, self._lead + _JITTER_BUFFER - lead)

    def _decode_loop(self):
        """Background thread: dequeue frames, decode H.264, emit QImages."""
        codec = av.CodecContext.create('h264', 'r')
        codec.thread_type = 'SLICE'  # slice-level threading for lower latency than frame-level
        codec.thread_count = 2
        codec.options = {
            'flags': '+low_delay+output_corrupt',
            'flags2': '+fast',
        }

        try:
            codec.open()
        except Exception:
            pass

        log.info("Decoder loop running")
        got_first_keyframe = False

        while self._running:
            with self._condition:
                if not self._frame_queue:
                    self._condition.wait(timeout=0.002)  # ~500Hz poll when idle
                    continue

                # Aggressive frame dropping for real-time responsiveness
                if len(self._frame_queue) > _DROP_THRESHOLD:
                    dropped = self._drop_to_latest_key()
                    if dropped > 0:
                        log.warning(f"Dropped {dropped} frames (queue was {dropped + len(self._frame_queue)})")

                behind = len(self._frame_queue) > _KEYFRAME_REQUEST_THRESHOLD
                frame_type, timestamp, data = self._frame_queue.popleft()

            if behind and self.request_keyframe and time.monotonic() - self._keyframe_asked_at > 1:
                self._keyframe_asked_at = time.monotonic()
                self.request_keyframe()

            try:
                # Config frames: just store, don't decode separately
                if frame_type == FRAME_VIDEO_CONFIG:
                    continue

                # Wait for first keyframe before decoding deltas
                if frame_type == FRAME_VIDEO_DELTA and not got_first_keyframe:
                    continue

                # Build data to feed decoder
                if frame_type == FRAME_VIDEO_KEY:
                    got_first_keyframe = True
                    # Prepend SPS/PPS to keyframes
                    if self._config_data:
                        feed_data = self._config_data + data
                    else:
                        feed_data = data

                    if self._decoded_count < 3:
                        log.info(f"Decoding keyframe: {len(feed_data)} bytes")
                else:
                    feed_data = data

                for video_frame in codec.decode(av.Packet(feed_data)):
                    self._decoded_count += 1
                    w, h = video_frame.width, video_frame.height

                    if self._decoded_count <= 3:
                        log.info(f"Decoded frame #{self._decoded_count}: {w}x{h}")

                    # Repack to RGB24 and hand the single packed plane to QImage;
                    # .copy() gives Qt its own buffer so the frame can be freed.
                    plane = video_frame.reformat(format='rgb24').planes[0]
                    qimg = QImage(bytes(plane), w, h, plane.line_size,
                                  QImage.Format.Format_RGB888).copy()

                    hold = self._hold(timestamp, time.monotonic())
                    # A backlog means we are already behind: catch up instead of pacing
                    if hold and not self._frame_queue:
                        time.sleep(hold)
                    self.frame_ready.emit(qimg)

            except av.error.InvalidDataError as e:
                if self._decoded_count < 5:
                    log.warning(f"Invalid data (frame #{self._decoded_count}): {e}")
            except Exception as e:
                log.error(f"Decode error: {e}", exc_info=True)
                if not self._running:
                    break

        log.info(f"Decoder loop exited. Total decoded: {self._decoded_count}")

    def _drop_to_latest_key(self) -> int:
        """Drop queued frames up to the latest keyframe. Returns number dropped. Must hold _condition lock."""
        last_key_idx = -1
        for i in range(len(self._frame_queue) - 1, -1, -1):
            if self._frame_queue[i][0] in (FRAME_VIDEO_CONFIG, FRAME_VIDEO_KEY):
                last_key_idx = i
                break

        if last_key_idx > 0:
            # Drop everything before the last keyframe
            dropped = last_key_idx
            for _ in range(last_key_idx):
                self._frame_queue.popleft()
            return dropped
        # No keyframe in queue: don't drop delta frames, they need
        # sequential decoding. Just let the decoder catch up.
        return 0
