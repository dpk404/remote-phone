"""
RemotePhone — Audio Player
Plays raw PCM audio received from the phone using sounddevice.
Audio format: 16-bit signed LE, 44100 Hz, stereo.
"""

import queue
import threading

# sounddevice is optional — audio is silently disabled if it (or PortAudio) is missing
try:
    import sounddevice as sd
except (ImportError, OSError):
    sd = None

_FRAME_BYTES = 4  # 2 channels × int16
_MAX_QUEUED = 4   # chunks (~93 ms each) held before new audio is dropped rather than played late


class AudioPlayer:
    """Simple PCM audio player using sounddevice."""

    def __init__(self):
        self._stream = None
        self._thread = None
        self._queue = queue.Queue(maxsize=_MAX_QUEUED)

    def start(self):
        """Open an audio output stream."""
        if sd is None:
            return
        self.stop()
        try:
            self._stream = sd.RawOutputStream(
                samplerate=44100,
                channels=2,
                dtype='int16',
                blocksize=4096,
                latency='low',
            )
            self._stream.start()
        except Exception:
            self._stream = None
            return
        self._thread = threading.Thread(
            target=self._write_loop, args=(self._stream,), daemon=True, name="AudioWriter"
        )
        self._thread.start()

    def feed(self, data: bytes):
        """Queue raw PCM (signed 16-bit LE, interleaved stereo) for playback. Never blocks."""
        if self._stream is None:
            return
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            pass

    def _write_loop(self, stream):
        # write() blocks for as long as the chunk plays, so it must stay off the GUI thread
        while True:
            data = self._queue.get()
            if data is None:
                return
            try:
                # Only whole stereo frames; a trailing partial sample is dropped
                stream.write(data[:len(data) // _FRAME_BYTES * _FRAME_BYTES])
            except Exception:
                pass  # Don't crash on audio glitches

    def stop(self):
        """Stop and close the audio stream."""
        if self._stream is None:
            return
        stream, self._stream = self._stream, None
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        self._queue.put(None)
        self._thread.join(timeout=1)
        try:
            stream.abort()  # stop() would block until the buffered audio has played out
            stream.close()
        except Exception:
            pass
