"""
RemotePhone — H.264 Video Decoder
Decodes H.264 NAL units from the phone into QImage frames using PyAV (FFmpeg).
Runs a decode loop in a background thread with aggressive frame dropping for
minimal latency real-time display.
"""

import logging
import threading
from collections import deque

import av
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage

log = logging.getLogger("video_decoder")

# Frame type constants
FRAME_VIDEO_CONFIG = 0x00
FRAME_VIDEO_KEY = 0x01
FRAME_VIDEO_DELTA = 0x02

# Performance: drop frames when queue exceeds this depth
_DROP_THRESHOLD = 3


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

    def start(self):
        """Start the decoder thread."""
        if self._running:
            return
        self._running = True
        self._config_data = None
        self._decoded_count = 0
        self._frame_queue.clear()
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
        log.info("Decoder thread stopped")

    def feed_frame(self, frame_type: int, timestamp: int, data: bytes):
        """Feed a raw H.264 frame into the decode queue (thread-safe, called from WS thread)."""
        if frame_type == FRAME_VIDEO_CONFIG:
            self._config_data = data
            log.info(f"Stored SPS/PPS config: {len(data)} bytes")

        with self._condition:
            self._frame_queue.append((frame_type, timestamp, data))
            self._condition.notify()

    def _decode_loop(self):
        """Background thread: dequeue frames, decode H.264, emit QImages."""
        codec = av.CodecContext.create('h264', 'r')
        codec.thread_type = 'SLICE'  # slice-level threading for lower latency than frame-level
        codec.thread_count = 2
        codec.options = {
            'flags': '+low_delay+output_corrupt',
            'flags2': '+fast',
            'tune': 'zerolatency',
        }

        try:
            codec.open()
        except Exception:
            pass

        log.info("Decoder loop running")
        got_first_keyframe = False

        while self._running:
            frame_data = None

            with self._condition:
                if not self._frame_queue:
                    self._condition.wait(timeout=0.002)  # ~500Hz poll when idle
                    continue

                # Aggressive frame dropping for real-time responsiveness
                if len(self._frame_queue) > _DROP_THRESHOLD:
                    dropped = self._drop_to_latest_key()
                    if dropped > 0:
                        log.warning(f"Dropped {dropped} frames (queue was {dropped + len(self._frame_queue)})")

                frame_data = self._frame_queue.popleft()

            if frame_data is None:
                continue

            frame_type, timestamp, data = frame_data

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

                packet = av.Packet(feed_data)
                frames = codec.decode(packet)

                for video_frame in frames:
                    self._decoded_count += 1

                    # Convert to RGB24 — single allocation via to_ndarray
                    arr = video_frame.to_ndarray(format='rgb24')
                    h, w, ch = arr.shape
                    bytes_per_line = ch * w

                    if self._decoded_count <= 3:
                        log.info(f"Decoded frame #{self._decoded_count}: {w}x{h}")

                    # Single copy: tobytes() produces a bytes object that QImage
                    # can reference, then .copy() makes a Qt-owned copy for thread safety.
                    raw = arr.tobytes()
                    qimg = QImage(raw, w, h, bytes_per_line,
                                  QImage.Format.Format_RGB888).copy()

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
        # No keyframe in queue — don't drop delta frames, they need
        # sequential decoding. Just let the decoder catch up.
        return 0
