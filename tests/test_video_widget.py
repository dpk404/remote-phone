"""Smallest check that clicks map to phone pixels in both orientations.
Run from the repo root:  python -m tests.test_video_widget
"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from remotephone.input.input_handler import InputHandler
from remotephone.ui.main_window import VideoWidget

app = QApplication([])
w = VideoWidget(InputHandler(), lambda cmd: None)
w.resize(800, 600)

# landscape phone: 2400x1080 letterboxed to 800x360, bars 120px top and bottom
w.image = QImage(2400, 1080, QImage.Format.Format_RGB888)
assert w._get_display_rect() == (0, 120, 800, 360)
assert w._map_to_phone(QPointF(400, 300)) == (1200.0, 540.0)
assert w._map_to_phone(QPointF(400, 50)) == (None, None)  # in the bar

# portrait phone: 1080x2400 pillarboxed to 270x600, bars 265px each side
w.image = QImage(1080, 2400, QImage.Format.Format_RGB888)
assert w._get_display_rect() == (265, 0, 270, 600)
assert w._map_to_phone(QPointF(400, 300)) == (540.0, 1200.0)

print("ok")
