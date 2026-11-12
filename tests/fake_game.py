#!/usr/bin/env python3
"""A stand-in game window for the tests: one X11 window with a title. It reads commands from standard input:
hide, show, quit. It prints READY <window id> when its window is up.
"""

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget

app = QApplication([sys.argv[0], "-platform", "xcb"])
window = QWidget()
window.setWindowTitle(sys.argv[1] if len(sys.argv) > 1 else "City of Heroes")
window.resize(800, 600)
window.show()
print("READY", int(window.winId()), flush=True)


def poll():
    import select

    while select.select([sys.stdin], [], [], 0)[0]:
        line = sys.stdin.readline().strip()
        if not line or line == "quit":
            app.quit()
        elif line == "hide":
            window.hide()
        elif line == "show":
            window.show()


timer = QTimer()
timer.timeout.connect(poll)
timer.start(50)
raise SystemExit(app.exec())
