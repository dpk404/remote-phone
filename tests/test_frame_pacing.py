"""Smallest check that decoded frames leave at the phone's capture spacing despite network jitter.
Run from the repo root:  python -m tests.test_frame_pacing
"""

from remotephone.decoder import video_decoder
from remotephone.decoder.video_decoder import VideoDecoder

d = VideoDecoder()
J = video_decoder._JITTER_BUFFER

# 30 fps capture; every frame arrives 0..12 ms late, in an uneven pattern
jitter = [0.0, 0.012, 0.003, 0.009, 0.0, 0.011, 0.006, 0.001] * 4
shown = []
for i, late in enumerate(jitter):
    arrival = 100.0 + i / 30 + late
    shown.append(arrival + d._hold(round(i * 1000 / 30), arrival))
gaps = [b - a for a, b in zip(shown, shown[1:])]
assert all(abs(g - 1 / 30) < 0.002 for g in gaps), gaps  # jitter absorbed: even 33 ms spacing
assert all(s - (100.0 + i / 30) <= J + 0.01 for i, s in enumerate(shown))  # bounded added latency

# a frame later than the buffer can absorb is shown at once, never held further
assert d._hold(round(len(jitter) * 1000 / 30), 100.0 + len(jitter) / 30 + 0.2) == 0.0

# the phone timestamp wrapping (or a new stream) re-anchors instead of freezing the pacing
assert abs(d._hold(5, 500.0) - J) < 1e-9

# a decoder far behind asks the phone for a keyframe, once, not on every frame
import time
from remotephone.network.ws_client import FRAME_VIDEO_DELTA

asked = []
behind = VideoDecoder()
behind.request_keyframe = lambda: asked.append(1)
for i in range(video_decoder._KEYFRAME_REQUEST_THRESHOLD + 10):
    behind.feed_frame(FRAME_VIDEO_DELTA, i, b"x")
behind.start()
time.sleep(0.2)
behind.stop()
assert asked == [1], asked

asked.clear()
fine = VideoDecoder()
fine.request_keyframe = lambda: asked.append(1)
for i in range(5):
    fine.feed_frame(FRAME_VIDEO_DELTA, i, b"x")
fine.start()
time.sleep(0.1)
fine.stop()
assert asked == [], asked

print("ok")
