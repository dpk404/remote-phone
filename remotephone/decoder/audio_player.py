"""
RemotePhone — Audio Player
Plays raw PCM audio received from the phone using sounddevice.
Audio format: 16-bit signed LE, 44100 Hz, stereo.
"""

import numpy as np

# sounddevice is optional — audio will be silently disabled if not available
try:
    import sounddevice as sd
    AUDIO_AVAILABLE = True
except (ImportError, OSError):
    AUDIO_AVAILABLE = False


class AudioPlayer:
    """Simple PCM audio player using sounddevice."""

    SAMPLE_RATE = 44100
    CHANNELS = 2

    def __init__(self):
        self._stream = None
        self._running = False

    def start(self, sample_rate: int = 44100, channels: int = 2):
        """Open an audio output stream."""
        if not AUDIO_AVAILABLE:
            return

        if self._running:
            self.stop()

        try:
            self._stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype='int16',
                blocksize=4096,
                latency='low',
            )
            self._stream.start()
            self._running = True
        except Exception:
            self._running = False

    def feed(self, data: bytes):
        """
        Feed raw PCM audio data for playback.
        Expected format: signed 16-bit little-endian, interleaved stereo.
        """
        if not self._running or not self._stream:
            return

        try:
            # Convert bytes to numpy array of int16 samples
            samples = np.frombuffer(data, dtype=np.int16)

            # Reshape to (frames, channels) for stereo
            if len(samples) % self.CHANNELS == 0:
                samples = samples.reshape(-1, self.CHANNELS)
            else:
                # Truncate to complete frames
                n = len(samples) - (len(samples) % self.CHANNELS)
                if n == 0:
                    return
                samples = samples[:n].reshape(-1, self.CHANNELS)

            self._stream.write(samples)
        except Exception:
            pass  # Don't crash on audio glitches

    def stop(self):
        """Stop and close the audio stream."""
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    @staticmethod
    def is_available() -> bool:
        """Check if audio playback is available on this system."""
        return AUDIO_AVAILABLE
