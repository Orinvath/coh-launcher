"""The watch: owns the helper, follows the game and its chat log, shows alerts, and plays their sounds.

It decides everything about the watch and tells the helper only what to show. It has no settings file of
its own: the bridge tells it the saved settings and hears from it when the watch could not open.
"""

import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from . import alerts, gametime, messages, testenv, watchface
from .chatlog import ChatLogFollower
from .fishaudio import SpeechClient
from .soundline import FROM_PLAYER, SoundLine, UnavailablePlayer, VoiceTicket
from .soundrecorder import RecordingPlayer
from .voicecache import VoiceCache

TICK_MS = 200
SCAN_SECONDS = 1.0
GAME_GONE_SECONDS = 3.0
CHIME_FILE = Path(__file__).resolve().parent.parent / "ui" / "sounds" / "chime.wav"


def make_player(pieces, parent):
    log = testenv.sound_log_path()
    if log:
        return RecordingPlayer(log, parent)
    if pieces.sound_player is not None:
        return pieces.sound_player(parent)
    return UnavailablePlayer(parent)


class WatchController(QObject):
    changed = Signal()  # a message came or went
    failed = Signal(str)  # the watch could not open; the reason
    moved = Signal(int, int)  # the watch was dragged, or put back at its starting spot

    def __init__(self, pieces, store, cache_dir, parent=None):
        super().__init__(parent)
        self._pieces = pieces
        self._store = store
        self._speech = SpeechClient(self)
        self._cache = VoiceCache(cache_dir)
        player = make_player(pieces, self)
        self._line = SoundLine(player, CHIME_FILE.read_bytes(), self)
        self._line.voice_started.connect(lambda: self._set_message("speak", None))
        self._line.voice_failed.connect(self._voice_failed)
        self._line.chime_played.connect(lambda: self._set_message("chime", None))
        self._line.chime_failed.connect(self._chime_failed)
        self._settings = {"game_root": "", "chime": True, "speak": False, "voice_id": None, "spot": None}
        self._enabled = False
        self._helper = None
        self._quick_stops = 0
        self._running = False
        self._last_seen = 0.0
        self._last_scan = 0.0
        self._pids = []
        self._follower = None
        self._repeat = alerts.RepeatFilter()
        self._card = None  # {"text", "start", "end"}
        self._sent_view = None
        self._messages = {"chime": None, "speak": None, "chatlog": False}
        timer = QTimer(self)
        timer.timeout.connect(self._tick)
        timer.start(TICK_MS)

    # ----- what the bridge tells it ------------------------------------------

    def configure(self, game_root, chime, speak, voice_id, spot):
        """The saved settings. A switch turned off cuts off its own kind of sound at once."""
        old = self._settings
        self._settings = {"game_root": game_root, "chime": chime, "speak": speak, "voice_id": voice_id, "spot": spot}
        if old["chime"] and not chime:
            self._line.stop_chimes()
            self._set_message("chime", None)
        if old["speak"] and not speak:
            self.key_removed()

    def key_removed(self):
        """Nothing more is spoken: the voice playing now is cut off and waiting ones are dropped."""
        self._line.stop_voices()
        self._set_message("speak", None)

    def messages(self):
        return dict(self._messages)

    def game_running(self):
        return self._running

    def helper_ready(self):
        """True while the watch helper is up and answering."""
        return self._helper is not None and self._helper.is_ready()

    def set_enabled(self, enabled):
        if enabled == self._enabled:
            return
        if not enabled:
            self._turn_off()
            return
        reason = self._start_reason()
        if reason is not None:
            self.failed.emit(reason)
            return
        self._enabled = True
        self._quick_stops = 0
        self._start_helper()

    def shutdown(self):
        self._turn_off()

    # ----- the helper ----------------------------------------------------------

    def _start_reason(self):
        helper = self._pieces.watch_helper
        return messages.WATCH_NOT_AVAILABLE if helper is None else helper.check()

    def _start_helper(self):
        self._helper = self._pieces.watch_helper.WatchHelper(self)
        self._helper.ready.connect(self._helper_ready)
        self._helper.moved.connect(self.moved)
        self._helper.start_failed.connect(lambda: self._fail(messages.HELPER_NO_START))
        self._helper.stopped.connect(self._helper_stopped)
        self._helper.start()

    def _helper_ready(self):
        self._helper.send(cmd="config", spot=self._settings["spot"])
        self._helper.send(cmd="game", pids=self._pids)
        self._sent_view = None
        self._send_view()

    def _helper_stopped(self, seconds_run):
        self._helper.deleteLater()
        if seconds_run < testenv.helper_minute():
            self._quick_stops += 1
        else:
            self._quick_stops = 0
        if self._quick_stops >= 2:
            self._fail(messages.HELPER_TWICE)
            return
        self._start_helper()

    def _fail(self, reason):
        self._turn_off()
        self.failed.emit(reason)

    def _turn_off(self):
        self._enabled = False
        if self._helper is not None:
            helper, self._helper = self._helper, None
            helper.stop()
            helper.deleteLater()
        self._game_closed()

    def _send(self, **command):
        if self._helper is not None:
            self._helper.send(**command)

    # ----- the game ----------------------------------------------------------

    def _scan(self, now):
        root = self._settings["game_root"]
        pids = self._pieces.find_game(root) if root and Path(root).is_dir() and self._pieces.find_game else []
        if pids:
            self._last_seen = now
            if not self._running:
                self._running = True
                self._follower = ChatLogFollower(root)
        elif self._running and now - self._last_seen >= GAME_GONE_SECONDS:
            self._game_closed()
            return
        if pids and sorted(pids) != self._pids:
            self._pids = sorted(pids)
            self._send(cmd="game", pids=self._pids)

    def _game_closed(self):
        """Everything that depends on the game running stops or goes away."""
        was_running = self._running
        self._running = False
        self._follower = None
        self._pids = []
        self._repeat = alerts.RepeatFilter()
        self._card = None
        self._line.stop_all()
        self._messages = {"chime": None, "speak": None, "chatlog": False}
        if was_running:
            self._send(cmd="game", pids=[])
        self.changed.emit()

    # ----- the clock, the chat log and the card --------------------------------

    def _tick(self):
        if not self._enabled:
            return
        now = time.monotonic()
        if now - self._last_scan >= SCAN_SECONDS:
            self._last_scan = now
            self._scan(now)
        if self._follower is not None:
            self._read_log()
        if self._card is not None and now >= self._card["end"]:
            self._card = None
        self._send_view()

    def _read_log(self):
        lines, found = self._follower.poll()
        self._set_message("chatlog", not found)
        for log_time, message in lines:
            self._repeat.forget_old(log_time)
            text = alerts.alert_text(message)
            if text is not None and self._repeat.is_new(text, log_time):
                self._new_alert(text)

    def _current_view_data(self):
        clock = gametime.game_clock(testenv.utc_now())
        if self._card is None:
            return ("face", clock.digits, clock.ampm, clock.is_day)
        return ("card", clock.text, self._card["text"])

    def _send_view(self):
        data = self._current_view_data()
        if data == self._sent_view:
            return
        self._sent_view = data
        if data[0] == "face":
            self._send(cmd="view", kind="face", digits=data[1], ampm=data[2], day=data[3])
        else:
            age = time.monotonic() - self._card["start"]
            self._send(cmd="view", kind="card", time=data[1], text=data[2], age=age)

    def _new_alert(self, text):
        now = time.monotonic()
        duration = alerts.card_seconds(watchface.card_layout(text).hidden)
        self._card = {"text": text, "start": now, "end": now + duration}
        self._send_view()
        self._send(cmd="ripples")
        settings = self._settings
        ticket = None
        if settings["speak"] and settings["voice_id"]:
            ticket = self._make_ticket(text, settings["voice_id"])
        if settings["chime"] or ticket is not None:
            self._line.add(settings["chime"], ticket)

    # ----- speaking ------------------------------------------------------------

    def _make_ticket(self, text, voice_id):
        """Read the key fresh for this alert, then use the saved sound or ask Fish Audio. The key is not kept."""
        ticket = VoiceTicket(self)
        if self._store is None:
            ticket.skip()
            return ticket

        def key_read(key, error):
            if ticket.state != "waiting":
                return
            if error:
                ticket.fail(messages.capital(messages.STORE_REASONS[error]))
            elif not key:
                ticket.skip()
            else:
                self._fetch(ticket, key, text, voice_id)

        self._store.read(key_read)
        return ticket

    def _fetch(self, ticket, key, text, voice_id):
        saved = self._cache.get(voice_id, text)
        if saved is not None:
            ticket.retry = lambda: self._retry(text, voice_id)
            ticket.ready(saved, cache_hit=True)
            return
        call = self._speech.speak(key, text, voice_id)
        ticket.cancelled.connect(call.cancel)

        def done(sound, reason):
            call.deleteLater()
            if sound is not None:
                self._cache.put(voice_id, text, sound)
                ticket.ready(sound)
            else:
                ticket.fail(reason)

        call.done.connect(done)

    def _retry(self, text, voice_id):
        """A saved sound would not play: delete it and ask Fish Audio again, once."""
        self._cache.discard(voice_id, text)
        return self._make_ticket(text, voice_id)

    def _voice_failed(self, source, reason):
        if source == FROM_PLAYER:
            reason = messages.capital(messages.SOUND_REASONS[reason])
        self._set_message("speak", f"{messages.SPEAK_FAILED} {reason}")

    def _chime_failed(self, code):
        self._set_message("chime", f"{messages.CHIME_FAILED} {messages.SOUND_REASONS[code]}")

    def _set_message(self, name, value):
        if self._messages[name] != value:
            self._messages[name] = value
            self.changed.emit()
