"""Smallest check that a burst drops the late backlog, not the newest audio. Needs no sound device.
Run from the repo root:  python -m tests.test_audio_player
"""

from remotephone.decoder.audio_player import AudioPlayer, _MAX_QUEUED

p = AudioPlayer()
p._stream = object()  # feed only queues while a stream is open
for i in range(_MAX_QUEUED + 1):
    p.feed(bytes([i]))
assert list(p._queue.queue) == [bytes([_MAX_QUEUED])]

print("audio_player OK")
