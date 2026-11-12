"""A stand-in sound player for the tests. It plays nothing and writes every request to a file.

It is used only when the tests ask for it (see testenv). Each request is one JSON line:
play (kind, size, a start of the sound's bytes), stop, end. A voice lasts as long as its bytes say with
`dur=<seconds>` (one second when they say nothing); a chime lasts 0.6 seconds. A sound whose bytes do not
start with FAKE is "damaged" and fails. The file `<log>.fail` holds an error code to make every play fail.
"""

import json
import re
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

CHIME_SECONDS = 0.6
DURATION_RE = re.compile(rb"dur=([0-9.]+)")


class RecordingPlayer(QObject):
    finished = Signal(object)

    def __init__(self, log_path, parent=None):
        super().__init__(parent)
        self._log = Path(log_path)
        self._fail = Path(str(log_path) + ".fail")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._end)
        self._error = None

    def _write(self, **row):
        with self._log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(row, time=time.time())) + "\n")

    def play(self, kind, sound):
        self._timer.stop()
        self._write(event="play", kind=kind, size=len(sound), head=sound[:60].decode("latin-1"))
        self._error = None
        if self._fail.exists():
            self._error = self._fail.read_text().strip()
        elif kind == "voice" and not sound.startswith(b"FAKE"):
            self._error = "failed"
        seconds = CHIME_SECONDS
        if kind == "voice":
            found = DURATION_RE.search(sound)
            seconds = float(found.group(1)) if found else 1.0
        self._timer.start(0 if self._error else int(seconds * 1000))

    def stop(self):
        if self._timer.isActive():
            self._timer.stop()
            self._write(event="stop")

    def _end(self):
        self._write(event="end", error=self._error)
        self.finished.emit(self._error)
