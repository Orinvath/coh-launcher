"""The Linux sound player: Qt can decode the MP3 that Fish Audio sends, and the player reports a missing sound device."""

import shutil
import subprocess
import unittest

import harness
from harness import fresh_dir, wait_until

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from launcher import watchcontroller
from launcher.linux.sound import SoundPlayer


def setUpModule():
    harness.app()


class TestPlayer(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg makes the sample MP3 for this test")
    def test_qt_decodes_mp3_so_no_decoder_is_bundled(self):
        mp3 = fresh_dir("mp3") / "sample.mp3"
        subprocess.run(["ffmpeg", "-v", "quiet", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        "-c:a", "libmp3lame", str(mp3)], check=True)
        player = QMediaPlayer()
        player.setAudioOutput(QAudioOutput())
        buffer = QBuffer()
        buffer.setData(QByteArray(mp3.read_bytes()))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        errors = []
        player.errorOccurred.connect(lambda code, text: errors.append(text))
        player.setSourceDevice(buffer, QUrl("voice.mp3"))
        self.assertTrue(wait_until(lambda: player.duration() > 0, 5))
        self.assertAlmostEqual(player.duration(), 1000, delta=100)
        self.assertEqual(errors, [])

    def test_no_sound_device_is_reported_for_chime_and_voice(self):
        from PySide6.QtMultimedia import QMediaDevices

        if QMediaDevices.audioOutputs():
            self.skipTest("this screen has a sound device")
        player = SoundPlayer()
        results = []
        player.finished.connect(results.append)
        player.play("chime", watchcontroller.CHIME_FILE.read_bytes())
        self.assertEqual(results, ["no-device"])

    def test_the_chime_is_a_short_wav_that_ships_with_the_launcher(self):
        import wave

        with wave.open(str(watchcontroller.CHIME_FILE)) as chime:
            seconds = chime.getnframes() / chime.getframerate()
        self.assertTrue(0.4 < seconds < 0.8)


if __name__ == "__main__":
    unittest.main()
