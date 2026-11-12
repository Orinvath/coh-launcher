"""Feature 9: the chime and voice settings, the key in the password store, the voice list and its messages."""

import json
import unittest

import harness
from harness import BridgeRig, FakeFish, pump, voice, wait_until

from launcher import pieces as piecesmod


def setUpModule():
    harness.app()


class VoiceCase(unittest.TestCase):
    def setUp(self):
        self.fish = FakeFish()
        self.addCleanup(self.fish.stop)
        self.rigs = []

    def rig(self, key=None, **kw):
        rig = BridgeRig(store=True, **kw)
        self.rigs.append(rig)
        if key:
            rig.bus.set_key(key)
        self.lines = []
        rig.bridge.stateChanged.connect(lambda text: self.lines.append((json.loads(text)["voice"]["line"] or {}).get("text")))
        return rig

    def tearDown(self):
        for rig in self.rigs:
            rig.close()

    def settle(self, rig, seconds=6):
        wait_until(lambda: rig.state()["voice"]["keyState"] not in ("reading", "closed")
                   and (rig.state()["voice"]["line"] or {}).get("kind") != "loading", seconds)

    def open(self, rig, tab="addons"):
        rig.send("settings-open")
        rig.send("settings-tab", tab=tab)
        self.settle(rig)
        return rig.state()["voice"]


class TestNoKeyAndReadErrors(VoiceCase):
    def test_no_key_only_the_prompt_never_loading(self):
        rig = self.rig()
        voice_state = self.open(rig)
        self.assertEqual(voice_state["keyState"], "none")
        self.assertEqual(voice_state["line"]["text"], "Enter your Fish Audio key to load voices.")
        self.assertEqual(voice_state["groups"], [])
        self.assertNotIn("Loading voices...", self.lines)
        self.assertEqual(self.fish.calls, [])

    def test_unreadable_store_shows_the_reason_never_loading_and_save_leaves_the_key(self):
        for mode, reason in (("locked", "the password store is locked or access was refused."),
                             ("error", "the password store reported an error.")):
            rig = self.rig(key="stored")
            rig.bus.set_mode(mode)
            voice_state = self.open(rig)
            self.assertEqual(voice_state["line"]["text"], f"Could not read your Fish Audio key: {reason}")
            self.assertNotIn("Loading voices...", self.lines)
            rig.bus.set_mode("ok")
            rig.save(alertChime=False)
            pump(0.5)
            self.assertEqual(rig.bus.key(), "stored")
            self.assertFalse(rig.file()["alertChime"])

    def test_no_store_running(self):
        rig = self.rig(key="stored")
        rig.bus.stop_service()
        voice_state = self.open(rig)
        self.assertEqual(voice_state["line"]["text"],
                         "Could not read your Fish Audio key: no password store is running on this desktop.")

    def test_no_password_store_piece_for_this_system(self):
        rig = BridgeRig()
        rig.close()
        from launcher.bridge import Bridge, Hooks

        pieces = piecesmod.Pieces(find_start_paths=None)
        noop = lambda *a: None  # noqa: E731
        bridge = Bridge(Hooks(noop, noop, noop, noop, noop, noop, noop, noop), harness.FakeRunner(),
                        shard_fetcher=lambda: [], config_dir=rig.config_dir,
                        feed_fetcher=lambda cb: cb(None, "x"), pieces=pieces)
        self.addCleanup(bridge.shutdown)
        bridge.dispatch("settings-open", "{}")
        state = bridge.snapshot()["voice"]
        self.assertEqual(state["keyState"], "unavailable")
        self.assertEqual(state["line"]["text"],
                         "Could not read your Fish Audio key: keeping the key is not available on this system yet.")
        self.assertEqual(state["groups"], [])
        bridge.dispatch("settings-save", json.dumps({
            "gameRoot": "", "protonPath": "", "compatPrefix": "", "skinId": "homecoming", "scale": 1,
            "showWatch": False, "startAutomatically": True, "alertChime": False, "speakAlerts": False,
            "voiceId": None, "key": {"mode": "keep", "value": ""}}))
        self.assertFalse(json.loads((rig.config_dir / "config.json").read_text())["alertChime"])


class TestVoiceList(VoiceCase):
    def test_loading_shows_then_voices_under_two_headings_and_no_message_is_left(self):
        self.fish.own = [voice("o1", "Mine")]
        self.fish.public = [voice("p1", "Theirs")]
        rig = self.rig(key="k")
        voice_state = self.open(rig)
        self.assertIn("Loading voices...", self.lines)
        self.assertIsNone(voice_state["line"])
        self.assertEqual(voice_state["groups"], [{"label": "My voices", "voices": [{"id": "o1", "title": "Mine"}]},
                                                 {"label": "Public voices", "voices": [{"id": "p1", "title": "Theirs"}]}])

    def test_only_good_voices_by_title_and_one_copy_under_my_voices(self):
        good = voice("good", "Good one")
        self.fish.own = [voice("failed", state="failed"), voice("training", state="training"),
                         voice("taken", dmca_taken_down=True), voice("taken2", takedown_category="x"),
                         voice("svc", type="svc"), good, voice("both", "Shared"), voice("notitle", title="")]
        self.fish.public = [voice("both", "Shared"), voice("pub", "Public one")]
        rig = self.rig(key="k")
        groups = self.open(rig)["groups"]
        self.assertEqual([v["id"] for v in groups[0]["voices"]], ["good", "both", "notitle"])
        self.assertEqual(groups[0]["voices"][2]["title"], "notitle")
        self.assertEqual([v["id"] for v in groups[1]["voices"]], ["pub"])

    def test_paging_and_query_values(self):
        self.fish.own = [voice(f"o{n}") for n in range(230)]
        self.fish.public = [voice(f"p{n}") for n in range(120)]
        rig = self.rig(key="k")
        groups = self.open(rig)["groups"]
        self.assertEqual((len(groups[0]["voices"]), len(groups[1]["voices"])), (230, 120))
        gets = [c for c in self.fish.calls if c["method"] == "GET"]
        own = [c["query"] for c in gets if c["query"].get("self") == "true"]
        public = [c["query"] for c in gets if "self" not in c["query"]]
        self.assertEqual([q["page_number"] for q in own], ["1", "2", "3"])
        self.assertEqual([q["page_number"] for q in public], ["1", "2"])
        for query in own + public:
            self.assertEqual((query["page_size"], query["sort_by"]), ("100", "score"))
        self.assertEqual({c["auth"] for c in gets}, {"Bearer k"})

    def test_paging_stops_at_the_limits(self):
        self.fish.has_more = False
        self.fish.own = [voice(f"o{n}") for n in range(2100)]
        self.fish.public = [voice(f"p{n}") for n in range(600)]
        rig = self.rig(key="k")
        groups = self.open(rig, "addons")["groups"]
        self.assertEqual((len(groups[0]["voices"]), len(groups[1]["voices"])), (2000, 500))

    def test_a_slow_answer_keeps_loading_on_the_line(self):
        self.fish.list_delay = 1.0
        self.fish.own = [voice("o1")]
        rig = self.rig(key="k")
        rig.send("settings-open")
        self.assertTrue(wait_until(lambda: (rig.state()["voice"]["line"] or {}).get("kind") == "loading", 3))
        pump(0.5)
        self.assertEqual(rig.state()["voice"]["line"]["text"], "Loading voices...")
        self.assertTrue(wait_until(lambda: rig.state()["voice"]["line"] is None, 5))

    def test_wrong_key_then_the_right_one_replaces_the_message(self):
        self.fish.list_status = 401
        rig = self.rig(key="wrong")
        voice_state = self.open(rig)
        self.assertEqual(voice_state["line"]["text"], "Could not load voices: Fish Audio did not accept your key.")
        self.fish.list_status = 200
        self.fish.list_delay = 0.6
        self.fish.own = [voice("o1")]
        rig.send("key-typed", key="right")
        self.assertEqual(rig.state()["voice"]["line"]["text"], "Loading voices...")
        self.assertTrue(wait_until(lambda: rig.state()["voice"]["line"] is None, 5))
        self.assertEqual(len(rig.state()["voice"]["groups"]), 1)

    def test_no_internet(self):
        import os

        rig = self.rig(key="k")
        os.environ["COH_TEST_FISH_URL"] = "http://127.0.0.1:9"
        voice_state = self.open(rig)
        self.assertEqual(voice_state["line"]["text"],
                         "Could not load voices: Fish Audio could not be reached. Check your internet connection.")

    def test_other_answers(self):
        for status, reason in ((429, "Fish Audio says there are too many requests. Try again later."),
                               (500, "Fish Audio is having problems right now."),
                               (404, "Fish Audio refused the request (error 404).")):
            self.fish.list_status = status
            rig = self.rig(key="k")
            self.assertEqual(self.open(rig)["line"]["text"], f"Could not load voices: {reason}")

    def test_no_answer_within_fifteen_seconds(self):
        self.fish.list_delay = 20.0
        rig = self.rig(key="k")
        rig.send("settings-open")
        self.assertTrue(wait_until(lambda: (rig.state()["voice"]["line"] or {}).get("kind") == "loadError", 18))
        self.assertEqual(rig.state()["voice"]["line"]["text"],
                         "Could not load voices: Fish Audio did not answer within 15 seconds.")

    def test_error_is_gone_after_cancel_and_reopen_with_a_working_key(self):
        self.fish.list_status = 401
        rig = self.rig(key="k")
        self.open(rig)
        rig.send("settings-cancel")
        self.assertIsNone(rig.state()["voice"]["line"])
        self.fish.list_status = 200
        self.fish.own = [voice("o1")]
        voice_state = self.open(rig)
        self.assertIsNone(voice_state["line"])
        self.assertEqual(len(voice_state["groups"]), 1)

    def test_emptying_the_key_box_empties_the_pulldown_and_asks_for_a_key(self):
        self.fish.own = [voice("o1")]
        rig = self.rig(key="k")
        self.open(rig)
        rig.send("key-typed", key="")
        state = rig.state()["voice"]
        self.assertEqual((state["groups"], state["keyState"]), ([], "none"))
        self.assertEqual(state["line"]["text"], "Enter your Fish Audio key to load voices.")


class TestSavingTheKey(VoiceCase):
    def test_saved_key_is_in_the_store_and_voice_and_switches_in_the_file(self):
        self.fish.own = [voice("o1", "Mine")]
        rig = self.rig()
        self.open(rig)
        rig.send("key-typed", key="abc")
        pump(0.3)
        rig.save(key={"mode": "set", "value": "abc"}, speakAlerts=True, voiceId="o1", alertChime=False)
        self.assertTrue(wait_until(lambda: not rig.state()["settingsOpen"], 5))
        self.assertEqual(rig.bus.key(), "abc")
        saved = rig.file()
        self.assertEqual((saved["speakAlerts"], saved["voiceId"], saved["alertChime"]), (True, "o1", False))
        self.assertNotIn("abc", json.dumps(saved))
        rig.send("settings-open")
        self.settle(rig)
        self.assertEqual(rig.state()["voice"]["keyState"], "stored")
        self.assertEqual(rig.state()["settings"]["voiceId"], "o1")

    def test_removing_the_key_deletes_it_and_empties_the_pulldown(self):
        self.fish.own = [voice("o1")]
        rig = self.rig(key="abc", voiceId="o1")
        self.open(rig)
        rig.save(key={"mode": "remove", "value": ""})
        self.assertTrue(wait_until(lambda: not rig.state()["settingsOpen"], 5))
        self.assertFalse(rig.bus.key())
        self.assertEqual(rig.state()["settings"]["voiceId"], "o1")
        voice_state = self.open(rig)
        self.assertEqual(voice_state["groups"], [])
        self.assertEqual(voice_state["line"]["text"], "Enter your Fish Audio key to load voices.")

    def test_a_failed_key_save_keeps_settings_open_saves_the_rest_and_can_be_tried_again(self):
        rig = self.rig()
        self.open(rig)
        rig.bus.stop_service()
        rig.save(key={"mode": "set", "value": "abc"}, alertChime=False)
        self.assertTrue(wait_until(lambda: rig.state()["voice"]["line"], 5))
        state = rig.state()
        self.assertTrue(state["settingsOpen"])
        self.assertEqual(state["settingsTab"], "addons")
        self.assertEqual(state["voice"]["line"]["text"],
                         "Could not save your Fish Audio key: no password store is running on this desktop.")
        self.assertFalse(rig.file()["alertChime"])
        rig.send("settings-cancel")
        self.assertIsNone(rig.state()["voice"]["line"])

    def test_locked_store_on_save_and_then_a_good_save_closes_settings(self):
        rig = self.rig()
        self.open(rig)
        rig.bus.set_mode("locked")
        rig.save(key={"mode": "set", "value": "abc"})
        self.assertTrue(wait_until(lambda: (rig.state()["voice"]["line"] or {}).get("kind") == "saveError", 5))
        self.assertIn("locked or access was refused", rig.state()["voice"]["line"]["text"])
        rig.bus.set_mode("ok")
        rig.save(key={"mode": "set", "value": "abc"})
        self.assertTrue(wait_until(lambda: not rig.state()["settingsOpen"], 5))
        self.assertEqual(rig.bus.key(), "abc")

    def test_a_failed_key_save_with_path_problems_reopens_on_general_with_the_message_waiting(self):
        rig = self.rig(valid_paths=False)
        rig.send("settings-open")
        rig.bus.stop_service()
        rig.save(key={"mode": "set", "value": "abc"})
        self.assertTrue(wait_until(lambda: (rig.state()["voice"]["line"] or {}).get("kind") in ("saveError", "readError"), 5))
        state = rig.state()
        self.assertTrue(state["settingsOpen"])
        self.assertEqual(state["settingsTab"], "general")

    def test_a_chosen_voice_that_is_not_in_the_list_is_kept(self):
        self.fish.list_status = 500
        rig = self.rig(key="k", voiceId="gone")
        self.open(rig)
        rig.save()
        self.assertEqual(rig.file()["voiceId"], "gone")

    def test_cancel_drops_voice_changes(self):
        rig = self.rig(key="k", voiceId="old")
        self.open(rig)
        rig.send("settings-cancel")
        self.assertEqual(rig.file()["voiceId"], "old")

    def test_a_refused_save_while_busy_never_touches_the_key(self):
        rig = self.rig(key="keep-me")
        rig.send("play")
        rig.send("settings-open")
        rig.save(key={"mode": "remove", "value": ""})
        pump(0.3)
        self.assertEqual(rig.bus.key(), "keep-me")

    def test_payload_is_checked(self):
        rig = self.rig()
        rig.send("settings-open")
        bad = rig.payload(key={"mode": "set", "value": ""})
        rig.bridge.dispatch("settings-save", json.dumps(bad))
        self.assertEqual(rig.state()["notice"]["text"], "That request was refused.")
        worse = rig.payload(showWatch="yes")
        rig.bridge.dispatch("settings-save", json.dumps(worse))
        self.assertEqual(rig.state()["notice"]["text"], "That request was refused.")


if __name__ == "__main__":
    unittest.main()
