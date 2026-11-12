"""Features 7 and 10: the chime, the line of sounds, speaking alerts, and their messages. Uses the recording
stand-in sound player, a stand-in Fish Audio and a stand-in password store on a private bus."""

import hashlib
import os
import time
import unittest

import harness
from harness import WatchRig, pump, sound_events, wait_until

from launcher import voicecache


def setUpModule():
    harness.app()


def run_to_end(rig, count, seconds=8):
    """Wait until the recording player has played `count` sounds to their end."""
    return wait_until(lambda: len([e for e in sound_events(rig.sound_log) if e["event"] == "end"]) >= count, seconds)


class RigCase(unittest.TestCase):
    def tearDown(self):
        self.rig.stop()

    def text(self):
        return self.rig.control.messages()


class TestChime(RigCase):
    def test_one_chime_per_alert_and_two_alerts_play_one_after_the_other(self):
        self.rig = WatchRig()
        self.rig.alert("WARNING: one.")
        self.rig.alert("WARNING: two.")
        self.assertTrue(run_to_end(self.rig, 2))
        rows = sound_events(self.rig.sound_log)
        self.assertEqual([r["event"] for r in rows], ["play", "end", "play", "end"])
        self.assertEqual(self.rig.plays(), ["chime", "chime"])

    def test_repeated_copy_gets_no_second_chime(self):
        self.rig = WatchRig()
        self.rig.alert("WARNING: one.")
        self.rig.alert("WARNING: one.")
        self.assertTrue(run_to_end(self.rig, 1))
        pump(1.2)
        self.assertEqual(self.rig.plays(), ["chime"])

    def test_chime_off_means_alert_without_chime(self):
        self.rig = WatchRig(chime=False)
        self.rig.alert("WARNING: one.")
        self.assertTrue(wait_until(lambda: any(c.get("kind") == "card" for c in self.rig.sent), 3))
        pump(1.0)
        self.assertEqual(self.rig.plays(), [])

    def test_chime_plays_with_the_watch_hidden(self):
        self.rig = WatchRig()
        harness.set_active_window(0)
        self.rig.alert("WARNING: one.")
        self.assertTrue(run_to_end(self.rig, 1))

    def test_stop_when_the_watch_is_turned_off(self):
        self.rig = WatchRig()
        self.rig.alert("WARNING: one.")
        self.assertTrue(wait_until(lambda: self.rig.plays(), 3))
        self.rig.control.set_enabled(False)
        self.assertTrue(any(e["event"] == "stop" for e in sound_events(self.rig.sound_log)))

    def test_chime_off_cuts_the_chime_and_drops_waiting_chimes(self):
        self.rig = WatchRig()
        self.rig.alert("WARNING: one.")
        self.rig.alert("WARNING: two.")
        self.assertTrue(wait_until(lambda: self.rig.plays(), 3))
        self.rig.configure(chime=False)
        pump(1.5)
        self.assertEqual(self.rig.plays(), ["chime"])
        self.assertEqual([e["event"] for e in sound_events(self.rig.sound_log)], ["play", "stop"])


class TestChimeError(RigCase):
    def make(self):
        self.rig = WatchRig()
        (self.rig.base / "sound.log.fail").write_text("no-device")

    def test_no_device_message_and_the_alert_still_shows(self):
        self.make()
        self.rig.alert("WARNING: one.")
        self.assertTrue(wait_until(lambda: self.text()["chime"], 3))
        self.assertEqual(self.text()["chime"], "Alert chime could not play: no sound device was found.")
        self.assertTrue(any(c.get("kind") == "card" for c in self.rig.sent))

    def test_message_goes_when_a_chime_plays_again(self):
        self.make()
        self.rig.alert("WARNING: one.")
        self.assertTrue(wait_until(lambda: self.text()["chime"], 3))
        (self.rig.base / "sound.log.fail").unlink()
        self.rig.alert("WARNING: two.", "2026-11-11 09:01:00")
        self.assertTrue(wait_until(lambda: not self.text()["chime"], 3))

    def test_message_goes_with_the_game_and_when_the_chime_is_turned_off(self):
        self.make()
        self.rig.alert("WARNING: one.")
        self.assertTrue(wait_until(lambda: self.text()["chime"], 3))
        self.rig.configure(chime=False)
        self.assertIsNone(self.text()["chime"])
        self.rig.configure(chime=True)
        self.rig.alert("WARNING: three.", "2026-11-11 09:02:00")
        self.assertTrue(wait_until(lambda: self.text()["chime"], 3))
        self.rig.game.stop()
        self.assertTrue(wait_until(lambda: not self.text()["chime"], 6))


class TestSpeaking(RigCase):
    def make(self, **kw):
        self.rig = WatchRig(speak=True, **kw)
        self.rig.fish.tts_body = b"FAKE-MP3 dur=0.3"

    def test_chime_then_voice_in_the_text_sent(self):
        self.make()
        self.rig.alert("[Vanguard] WARNING: Zombie attack in 'Boomtown/East'.")
        self.assertTrue(run_to_end(self.rig, 2))
        self.assertEqual(self.rig.plays(), ["chime", "voice"])
        call = self.rig.fish.tts_calls()[0]
        self.assertEqual(call["body"]["text"], "WARNING: Zombie attack in 'Boomtown/East'.")
        self.assertEqual(call["body"]["reference_id"], "voice-1")
        self.assertEqual(call["body"]["format"], "mp3")
        self.assertEqual(call["auth"], "Bearer the-key")
        self.assertEqual(call["model"], "s2.1-pro-free")

    def test_order_chime1_voice1_chime2_voice2_and_second_call_is_sent_at_once(self):
        self.make()
        self.rig.fish.tts_body = b"FAKE-MP3 dur=1.5"
        self.rig.alert("WARNING: one.")
        self.rig.alert("WARNING: two.", "2026-11-11 09:01:00")
        self.assertTrue(wait_until(lambda: len(self.rig.fish.tts_calls()) == 2, 3))
        first_voice = [e for e in sound_events(self.rig.sound_log) if e["event"] == "play" and e["kind"] == "voice"]
        self.assertTrue(len(first_voice) <= 1)
        self.assertTrue(run_to_end(self.rig, 4, 12))
        self.assertEqual(self.rig.plays(), ["chime", "voice", "chime", "voice"])
        rows = sound_events(self.rig.sound_log)
        self.assertEqual([r["event"] for r in rows], ["play", "end"] * 4)

    def test_three_alerts_are_asked_for_at_once_and_played_in_order(self):
        self.make()
        for number, stamp in enumerate(("09:00:00", "09:01:00", "09:02:00")):
            self.rig.alert(f"WARNING: n{number}.", f"2026-11-11 {stamp}")
        self.assertTrue(wait_until(lambda: len(self.rig.fish.tts_calls()) == 3, 3))
        self.assertTrue(run_to_end(self.rig, 6, 12))
        self.assertEqual(self.rig.plays(), ["chime", "voice"] * 3)

    def test_closing_the_game_cuts_off_the_voice_and_drops_the_rest(self):
        self.make()
        self.rig.fish.tts_body = b"FAKE-MP3 dur=10"
        self.rig.alert("WARNING: one.")
        self.rig.alert("WARNING: two.", "2026-11-11 09:01:00")
        self.assertTrue(wait_until(lambda: self.rig.plays() == ["chime", "voice"], 4))
        self.rig.game.stop()
        self.assertTrue(wait_until(lambda: any(e["event"] == "stop" for e in sound_events(self.rig.sound_log)), 6))
        pump(1.0)
        self.assertEqual(self.rig.plays(), ["chime", "voice"])

    def test_speak_off_cuts_the_voice_but_a_waiting_chime_still_plays(self):
        self.make()
        self.rig.fish.tts_body = b"FAKE-MP3 dur=10"
        self.rig.alert("WARNING: one.")
        self.rig.alert("WARNING: two.", "2026-11-11 09:01:00")
        self.assertTrue(wait_until(lambda: self.rig.plays() == ["chime", "voice"], 4))
        self.rig.configure(speak=False)
        self.assertTrue(wait_until(lambda: self.rig.plays() == ["chime", "voice", "chime"], 3))
        pump(1.0)
        self.assertEqual(self.rig.plays(), ["chime", "voice", "chime"])

    def test_chime_off_cuts_the_chime_but_a_waiting_voice_still_plays(self):
        self.make()
        self.rig.alert("WARNING: one.")
        self.assertTrue(wait_until(lambda: self.rig.plays() == ["chime"], 3))
        self.rig.configure(chime=False, speak=True)
        self.assertTrue(run_to_end(self.rig, 1, 6))
        self.assertEqual(self.rig.plays(), ["chime", "voice"])

    def test_repeated_copy_is_spoken_once(self):
        self.make()
        self.rig.alert("WARNING: one.")
        self.rig.alert("WARNING: one.")
        self.assertTrue(run_to_end(self.rig, 2))
        pump(1.0)
        self.assertEqual(len(self.rig.fish.tts_calls()), 1)

    def test_speak_off_sends_nothing(self):
        self.rig = WatchRig(speak=False)
        self.rig.alert("WARNING: one.")
        self.assertTrue(run_to_end(self.rig, 1))
        self.assertEqual(self.rig.fish.tts_calls(), [])

    def test_no_voice_saved_sends_nothing(self):
        self.rig = WatchRig(speak=True, voice=None)
        self.rig.alert("WARNING: one.")
        self.assertTrue(run_to_end(self.rig, 1))
        self.assertEqual(self.rig.fish.tts_calls(), [])
        self.assertIsNone(self.text()["speak"])

    def test_no_key_speaks_nothing_and_shows_nothing(self):
        self.rig = WatchRig(speak=True, key=None)
        self.rig.alert("WARNING: one.")
        self.assertTrue(run_to_end(self.rig, 1))
        pump(0.5)
        self.assertEqual(self.rig.fish.tts_calls(), [])
        self.assertIsNone(self.text()["speak"])

    def test_chat_from_players_is_never_sent(self):
        self.make()
        self.rig.alert("[Tell] WARNING: nope.")
        self.rig.alert("[Team] hello")
        pump(1.5)
        self.assertEqual(self.rig.fish.tts_calls(), [])

    def test_removed_key_means_nothing_is_asked_even_with_a_saved_sound(self):
        self.make()
        self.rig.alert("WARNING: one.")
        self.assertTrue(run_to_end(self.rig, 2))
        self.rig.bus.set_key(None)
        self.rig.alert("WARNING: one.", "2026-11-11 09:01:00")
        self.assertTrue(run_to_end(self.rig, 3))
        pump(0.5)
        self.assertEqual(len(self.rig.fish.tts_calls()), 1)

    def test_changed_key_applies_to_the_next_alert(self):
        self.make()
        self.rig.alert("WARNING: one.")
        self.assertTrue(run_to_end(self.rig, 2))
        self.rig.bus.set_key("new-key")
        self.rig.alert("WARNING: two.", "2026-11-11 09:01:00")
        self.assertTrue(run_to_end(self.rig, 4))
        self.assertEqual([c["auth"] for c in self.rig.fish.tts_calls()], ["Bearer the-key", "Bearer new-key"])

    def test_saved_voice_id_is_used_whatever_the_voice_list_did(self):
        self.make(voice="gone-voice")
        self.rig.alert("WARNING: one.")
        self.assertTrue(run_to_end(self.rig, 2))
        self.assertEqual(self.rig.fish.tts_calls()[0]["body"]["reference_id"], "gone-voice")


class TestSpeakingErrors(RigCase):
    def make(self, **kw):
        self.rig = WatchRig(speak=True, **kw)

    def speak_message(self, seconds=6):
        self.assertTrue(wait_until(lambda: self.text()["speak"], seconds))
        return self.text()["speak"]

    def test_wrong_key(self):
        self.make()
        self.rig.fish.tts_status = 401
        self.rig.alert("WARNING: one.")
        self.assertEqual(self.speak_message(), "Last alert could not be spoken: Fish Audio did not accept your key.")
        self.assertTrue(any(c.get("kind") == "card" for c in self.rig.sent))

    def test_other_refusals_and_server_trouble(self):
        for status, reason in ((429, "Fish Audio says there are too many requests. Try again later."),
                               (503, "Fish Audio is having problems right now."),
                               (418, "Fish Audio refused the request (error 418).")):
            self.make()
            self.rig.fish.tts_status = status
            self.rig.alert("WARNING: one.")
            self.assertEqual(self.speak_message(), f"Last alert could not be spoken: {reason}")
            self.assertEqual(len(self.rig.fish.tts_calls()), 1)
            self.rig.stop()

    def test_no_internet(self):
        self.make()
        os.environ["COH_TEST_FISH_URL"] = "http://127.0.0.1:9"
        self.rig.alert("WARNING: one.")
        self.assertEqual(
            self.speak_message(),
            "Last alert could not be spoken: Fish Audio could not be reached. Check your internet connection.")

    def test_model_switch_only_for_model_refusals(self):
        self.make()
        self.rig.fish.model_errors = 1
        self.rig.alert("WARNING: one.")
        self.assertTrue(run_to_end(self.rig, 2))
        self.assertEqual([c["model"] for c in self.rig.fish.tts_calls()], ["s2.1-pro-free", "s2.1-pro"])
        self.rig.alert("WARNING: two.", "2026-11-11 09:01:00")
        self.assertTrue(run_to_end(self.rig, 4))
        self.assertEqual(self.rig.fish.tts_calls()[-1]["model"], "s2.1-pro")
        self.assertIsNone(self.text()["speak"])

    def test_unrelated_refusal_does_not_switch_the_model(self):
        self.make()
        self.rig.fish.tts_status = 402
        self.rig.fish.tts_error_text = '{"message": "no credit"}'
        self.rig.alert("WARNING: one.")
        self.speak_message()
        self.assertEqual([c["model"] for c in self.rig.fish.tts_calls()], ["s2.1-pro-free"])

    def test_password_store_reasons(self):
        for mode, reason in (("locked", "The password store is locked or access was refused."),
                             ("error", "The password store reported an error.")):
            self.make()
            self.rig.bus.set_mode(mode)
            self.rig.alert("WARNING: one.")
            self.assertEqual(self.speak_message(), f"Last alert could not be spoken: {reason}")
            self.assertEqual(self.rig.fish.tts_calls(), [])
            self.assertTrue(any(c.get("kind") == "card" for c in self.rig.sent))
            self.rig.stop()

    def test_no_password_store_running(self):
        self.make()
        self.rig.bus.stop_service()
        self.rig.alert("WARNING: one.")
        self.assertEqual(self.speak_message(),
                         "Last alert could not be spoken: No password store is running on this desktop.")

    def test_message_goes_when_the_next_alert_is_spoken_and_when_speak_is_turned_off(self):
        self.make()
        self.rig.fish.tts_status = 401
        self.rig.alert("WARNING: one.")
        self.speak_message()
        self.rig.fish.tts_status = 200
        self.rig.alert("WARNING: two.", "2026-11-11 09:01:00")
        self.assertTrue(wait_until(lambda: not self.text()["speak"], 6))
        self.rig.fish.tts_status = 401
        self.rig.alert("WARNING: three.", "2026-11-11 09:02:00")
        self.speak_message()
        self.rig.configure(speak=False)
        self.assertIsNone(self.text()["speak"])

    def test_sound_player_reason_and_game_close_clears(self):
        self.make()
        self.rig.alert("WARNING: one.")
        self.assertTrue(run_to_end(self.rig, 2))
        (self.rig.base / "sound.log.fail").write_text("failed")
        self.rig.alert("WARNING: two.", "2026-11-11 09:01:00")
        self.assertEqual(self.speak_message(),
                         "Last alert could not be spoken: The sound system could not play it.")
        self.rig.game.stop()
        self.assertTrue(wait_until(lambda: not self.text()["speak"], 6))

    def test_no_answer_in_time_then_the_next_alert_is_spoken(self):
        self.make()
        self.rig.fish.tts_delay = 20.0
        self.rig.alert("WARNING: one.")
        started = time.monotonic()
        self.assertEqual(self.speak_message(20),
                         "Last alert could not be spoken: Fish Audio did not answer within 15 seconds.")
        self.assertTrue(14.0 < time.monotonic() - started < 18.0)
        self.rig.fish.tts_delay = 0
        self.rig.alert("WARNING: two.", "2026-11-11 09:01:00")
        self.assertTrue(wait_until(lambda: not self.text()["speak"], 8))


class TestSavedSounds(RigCase):
    def make(self):
        self.rig = WatchRig(speak=True)

    def saved(self, text, voice="voice-1"):
        digest = hashlib.sha256(voice.encode() + b"\0" + text.encode()).hexdigest()
        return self.rig.cache / f"{digest}.mp3"

    def test_file_name_rule_and_second_time_comes_from_disk(self):
        self.make()
        text = "WARNING: Zombie attack in 'Boomtown/East'."
        self.rig.alert(text)
        self.assertTrue(run_to_end(self.rig, 2))
        self.assertTrue(self.saved(text).is_file())
        self.assertEqual(len(self.saved(text).name), 68)
        self.rig.alert(text, "2026-11-11 09:10:00")
        self.assertTrue(run_to_end(self.rig, 4))
        self.assertEqual(len(self.rig.fish.tts_calls()), 1)
        self.assertEqual(self.rig.plays(), ["chime", "voice", "chime", "voice"])

    def test_another_voice_makes_another_file(self):
        self.make()
        self.rig.alert("WARNING: one.")
        self.assertTrue(run_to_end(self.rig, 2))
        self.rig.configure(speak=True, voice="voice-2")
        self.rig.alert("WARNING: one.", "2026-11-11 09:10:00")
        self.assertTrue(run_to_end(self.rig, 4))
        self.assertEqual(len(list(self.rig.cache.glob("*.mp3"))), 2)

    def test_a_damaged_saved_sound_is_replaced_by_a_new_call(self):
        self.make()
        self.rig.alert("WARNING: one.")
        self.assertTrue(run_to_end(self.rig, 2))
        self.saved("WARNING: one.").write_bytes(b"broken")
        self.rig.alert("WARNING: one.", "2026-11-11 09:10:00")
        self.assertTrue(run_to_end(self.rig, 5, 10))
        self.assertEqual(len(self.rig.fish.tts_calls()), 2)
        self.assertTrue(self.saved("WARNING: one.").read_bytes().startswith(b"FAKE"))
        self.assertIsNone(self.text()["speak"])


class TestCacheFolder(unittest.TestCase):
    def test_trim_keeps_other_files_and_the_new_sound(self):
        folder = harness.fresh_dir("cache")
        cache = voicecache.VoiceCache(folder)
        for index in range(3):
            path = folder / f"{index:064x}.mp3"
            with open(path, "wb") as handle:
                handle.truncate(20 * 1024 * 1024)
            os.utime(path, (1000 + index, 1000 + index))
        (folder / "notes.txt").write_text("mine")
        cache.put("v", "new", b"x" * 1000)
        names = sorted(p.name for p in folder.iterdir())
        self.assertIn("notes.txt", names)
        self.assertIn(voicecache.file_name("v", "new"), names)
        total = sum(p.stat().st_size for p in folder.glob("*.mp3"))
        self.assertLess(total, voicecache.MAX_BYTES)
        self.assertNotIn(f"{0:064x}.mp3", names)

    def test_leftover_part_files_are_deleted_on_the_next_save(self):
        folder = harness.fresh_dir("cache2")
        leftover = folder / ("a" * 64 + ".mp3.part")
        leftover.write_bytes(b"x")
        (folder / "other.part").write_bytes(b"y")
        voicecache.VoiceCache(folder).put("v", "t", b"data")
        self.assertFalse(leftover.exists())
        self.assertTrue((folder / "other.part").exists())

    def test_unwritable_folder_is_silent(self):
        folder = harness.fresh_dir("cache3")
        blocker = folder / "file"
        blocker.write_text("x")
        voicecache.VoiceCache(blocker / "sub").put("v", "t", b"data")


if __name__ == "__main__":
    unittest.main()
