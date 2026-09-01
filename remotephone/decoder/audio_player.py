"""
RemotePhone — Audio Player
Plays raw PCM audio received from the phone using sounddevice.
Audio format: 16-bit signed LE, 44100 Hz, stereo.
"""

# sounddevice is optional — audio is silently disabled if it (or PortAudio) is missing
try:
    import sounddevice as sd
except (ImportError, OSError):
    sd = None

_FRAME_BYTES = 4  # 2 channels × int16


class AudioPlayer:
    """Simple PCM audio player using sounddevice."""

    def __init__(self):
        self._stream = None

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

    def feed(self, data: bytes):
        """Feed raw PCM (signed 16-bit LE, interleaved stereo) for playback."""
        if self._stream is None:
            return
        try:
            # Only whole stereo frames; a trailing partial sample is dropped
            self._stream.write(data[:len(data) // _FRAME_BYTES * _FRAME_BYTES])
        except Exception:
            pass  # Don't crash on audio glitches

    def stop(self):
        """Stop and close the audio stream."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
