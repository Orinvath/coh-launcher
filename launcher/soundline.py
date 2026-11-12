"""The one line of sounds (Feature 7, step 4): chime 1, voice 1, chime 2, voice 2, one at a time. The same on every system.

The line never plays a sound itself. It hands one sound at a time to the sound player and waits for it.
The player has play(kind, sound), stop() and a `finished(error)` signal; error is None when the sound played.
"""

from PySide6.QtCore import QObject, QTimer, Signal

CHIME, VOICE = "chime", "voice"
FROM_ALERT, FROM_PLAYER = "alert", "player"


class VoiceTicket(QObject):
    """A spoken alert on its way: waiting, ready (with its sound), failed (with a reason), skipped or cancelled."""

    resolved = Signal()
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "waiting"
        self.sound = None
        self.reason = None
        self.cache_hit = False
        self.retry = None  # () -> VoiceTicket: asks Fish Audio again for a saved sound that would not play

    def ready(self, sound, cache_hit=False):
        self._resolve("ready", sound=sound, cache_hit=cache_hit)

    def fail(self, reason):
        self._resolve("failed", reason=reason)

    def skip(self):
        self._resolve("skipped")

    def cancel(self):
        if self.state == "waiting":
            self.state = "cancelled"
            self.cancelled.emit()

    def _resolve(self, state, sound=None, reason=None, cache_hit=False):
        if self.state == "waiting":
            self.state, self.sound, self.reason, self.cache_hit = state, sound, reason, cache_hit
            self.resolved.emit()


class _Job:
    def __init__(self, chime, ticket):
        self.chime = chime
        self.ticket = ticket


class SoundLine(QObject):
    voice_started = Signal()
    voice_failed = Signal(str, str)  # source (FROM_ALERT or FROM_PLAYER), reason
    chime_played = Signal()
    chime_failed = Signal(str)

    def __init__(self, player, chime_sound, parent=None):
        super().__init__(parent)
        self._player = player
        self._chime_sound = chime_sound
        self._jobs = []
        self._playing = None  # (kind, ticket) of the sound the player is playing now
        player.finished.connect(self._sound_finished)

    def add(self, chime, ticket):
        """A new alert joins the line: its chime when wanted, and its voice (a ticket) or None."""
        if ticket is not None:
            ticket.resolved.connect(self._advance)
        self._jobs.append(_Job(chime, ticket))
        self._advance()

    def stop_all(self):
        for job in self._jobs:
            if job.ticket is not None:
                job.ticket.cancel()
        self._jobs.clear()
        if self._playing is not None:
            self._stop_playing()

    def stop_chimes(self):
        for job in self._jobs:
            job.chime = False
        if self._playing and self._playing[0] == CHIME:
            self._stop_playing()
        self._advance()

    def stop_voices(self):
        for job in self._jobs:
            if job.ticket is not None:
                job.ticket.cancel()
                job.ticket = None
        if self._playing and self._playing[0] == VOICE:
            self._stop_playing()
        self._advance()

    def _stop_playing(self):
        self._playing = None
        self._player.stop()

    def _advance(self):
        while self._playing is None and self._jobs:
            job = self._jobs[0]
            if job.chime:
                job.chime = False
                self._playing = (CHIME, None)
                self._player.play(CHIME, self._chime_sound)
                return
            ticket = job.ticket
            if ticket is not None and ticket.state == "waiting":
                return
            self._jobs.pop(0)
            if ticket is not None:
                self._take(ticket)

    def _take(self, ticket):
        if ticket.state == "ready":
            self._playing = (VOICE, ticket)
            self.voice_started.emit()
            self._player.play(VOICE, ticket.sound)
        elif ticket.state == "failed":
            self.voice_failed.emit(FROM_ALERT, ticket.reason)

    def _sound_finished(self, error):
        if self._playing is None:
            return
        kind, ticket = self._playing
        self._playing = None
        if kind == CHIME:
            self.chime_failed.emit(error) if error else self.chime_played.emit()
        elif error and ticket.cache_hit:
            retry = _Job(False, ticket.retry())
            retry.ticket.resolved.connect(self._advance)
            self._jobs.insert(0, retry)
        elif error:
            self.voice_failed.emit(FROM_PLAYER, error)
        self._advance()


class UnavailablePlayer(QObject):
    """The player of a system with no sound player piece yet: every request fails at once."""

    finished = Signal(object)

    def play(self, _kind, _sound):
        QTimer.singleShot(0, lambda: self.finished.emit("unavailable"))

    def stop(self):
        return None
