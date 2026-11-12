"""Linux piece, launcher side: starts, talks to and closes the watch helper (Feature 4, steps 9 to 11)."""

import json
import os
import sys
import time

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from ..config import PROJECT_ROOT
from . import x11

NO_DISPLAY = "No X11 display was found."
STOP_WAIT_MS = 2000


def check():
    """None when a helper can be started here, or the reason it cannot."""
    return None if x11.display_reachable() else NO_DISPLAY


class WatchHelper(QObject):
    """One helper process. ready, moved(x, y), start_failed, or stopped(seconds it ran) when it ended by itself."""

    ready = Signal()
    moved = Signal(int, int)
    start_failed = Signal()
    stopped = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.setWorkingDirectory(str(PROJECT_ROOT))
        self._process.readyReadStandardOutput.connect(self._read)
        self._process.finished.connect(self._ended)
        self._process.errorOccurred.connect(self._error)
        self._text = ""
        self._ready = False
        self._started_at = 0.0
        self._closing = False

    def is_ready(self):
        return self._ready and self._process.state() == QProcess.ProcessState.Running

    def start(self):
        environment = QProcessEnvironment()
        for key, value in os.environ.items():
            if key != "WAYLAND_DISPLAY":
                environment.insert(key, value)
        environment.insert("QT_QPA_PLATFORM", "xcb")
        self._process.setProcessEnvironment(environment)
        self._started_at = time.monotonic()
        self._process.start(sys.executable, ["-m", "launcher.linux.watchhelper"])

    def send(self, **command):
        if self._process.state() == QProcess.ProcessState.Running:
            self._process.write((json.dumps(command) + "\n").encode())

    def stop(self):
        """Close the helper now and wait for it. Nothing is left running."""
        self._closing = True
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self.send(cmd="quit")
            self._process.closeWriteChannel()
            if not self._process.waitForFinished(STOP_WAIT_MS):
                self._process.kill()
                self._process.waitForFinished(STOP_WAIT_MS)

    def _read(self):
        if self._closing:
            return
        self._text += bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        *lines, self._text = self._text.split("\n")
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("event") == "ready":
                self._ready = True
                self.ready.emit()
            elif event.get("event") == "moved":
                self.moved.emit(event["x"], event["y"])

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart and not self._closing:
            self.start_failed.emit()

    def _ended(self, _code, _status):
        if self._closing:
            return
        if not self._ready:
            self.start_failed.emit()
        else:
            self.stopped.emit(time.monotonic() - self._started_at)
