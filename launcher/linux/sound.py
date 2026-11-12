"""Linux piece: the sound player. Plays a sound through the PC's normal sound output with Qt Multimedia.

play(kind, sound) starts one sound (WAV or MP3 bytes), stop() cuts it off, and `finished(error)` says when it ended.
error is None when it played, or NO_DEVICE, FAILED.
"""

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

NO_DEVICE, FAILED = "no-device", "failed"
HINTS = {"chime": "chime.wav", "voice": "voice.mp3"}


class SoundPlayer(QObject):
    finished = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._player = None
        self._buffer = None

    def play(self, kind, sound):
        self.stop()
        if not QMediaDevices.audioOutputs():
            self.finished.emit(NO_DEVICE)
            return
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(QAudioOutput(self._player))
        self._buffer = QBuffer(self._player)
        self._buffer.setData(QByteArray(sound))
        self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self._player.mediaStatusChanged.connect(self._status)
        self._player.errorOccurred.connect(self._error)
        self._player.setSourceDevice(self._buffer, QUrl(HINTS[kind]))
        self._player.play()

    def stop(self):
        if self._player is not None:
            player, self._player = self._player, None
            player.disconnect(self)
            player.stop()
            player.deleteLater()

    def _status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._end(None)

    def _error(self, _error, _text):
        self._end(FAILED)

    def _end(self, error):
        self.stop()
        self.finished.emit(error)
