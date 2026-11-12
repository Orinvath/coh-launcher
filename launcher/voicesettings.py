"""The Fish Audio key box and Voice pulldown while Settings is open (Feature 9, steps 4 to 17).

It holds only what belongs to one opening of Settings: the key lines, the voice list, and whether a key is
stored. The key itself is read from the password store, used to load the list, and not kept.
"""

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QNetworkAccessManager

from . import messages
from .fishaudio import VoiceListLoader


class VoiceSettings(QObject):
    changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self._store = store
        self._loader = VoiceListLoader(QNetworkAccessManager(self), self)
        self._loader.loaded.connect(self._loaded)
        self._loader.failed.connect(self._load_failed)
        self._reset()

    def _reset(self):
        self.key_state = "closed"  # closed, reading, none, stored, unreadable, unavailable
        self.line = None  # the one key and voice message line: {"kind", "text"} or None
        self.groups = []
        self._opening = 0

    def view(self):
        return {"keyState": self.key_state, "line": self.line, "groups": self.groups}

    def open(self):
        """Settings opened (or opened again by itself): read the key and load the list afresh."""
        self._loader.cancel()
        waiting_save_error = self.line if self.line and self.line["kind"] == "saveError" else None
        self._reset()
        self.line = waiting_save_error
        if self._store is None:
            self.key_state = "unavailable"
            self.line = _line("readError", f"{messages.KEY_READ_FAILED} {messages.STORE_UNAVAILABLE}")
            self.changed.emit()
            return
        self.key_state = "reading"
        self._opening += 1
        opening = self._opening
        self._store.read(lambda key, error: self._key_read(opening, key, error))
        self.changed.emit()

    def _key_read(self, opening, key, error):
        if opening != self._opening or self.key_state != "reading":
            return
        if error:
            self.key_state = "unreadable"
            self.line = _line("readError", f"{messages.KEY_READ_FAILED} {messages.STORE_REASONS[error]}")
        elif key:
            self.key_state = "stored"
            self._start_load(key)
        else:
            self.key_state = "none"
            self.line = _line("prompt", messages.KEY_PROMPT)
        self.changed.emit()

    def typed(self, key):
        """A key was typed (or the box was emptied). An empty key empties the pulldown."""
        self._loader.cancel()
        self.groups = []
        if key:
            self.key_state = "stored"
            self._start_load(key)
        else:
            self.key_state = "none"
            self.line = _line("prompt", messages.KEY_PROMPT)
        self.changed.emit()

    def save_failed(self, error):
        self.line = _line("saveError", f"{messages.KEY_SAVE_FAILED} {messages.STORE_REASONS[error]}")
        self.changed.emit()

    def close(self):
        self._loader.cancel()
        self._reset()
        self.changed.emit()

    def _start_load(self, key):
        self.line = _line("loading", messages.KEY_LOADING)
        self._loader.load(key)

    def _loaded(self, groups):
        self.groups = groups
        self.line = None
        self.changed.emit()

    def _load_failed(self, reason):
        self.line = _line("loadError", f"{messages.VOICES_FAILED} {reason}")
        self.changed.emit()


def _line(kind, text):
    return {"kind": kind, "text": text}
