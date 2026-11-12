"""Feature 4 in the launcher: Play turns the watch on, the watch message, the toast data, and Settings staying in step."""

import os
import unittest

import harness
from harness import BridgeRig, pump, wait_until

WATCH_ERROR = "The game watch could not open. No X11 display was found."


def setUpModule():
    harness.app()


class NoDisplay:
    """Runs the launcher with DISPLAY not set."""

    def __enter__(self):
        self.saved = os.environ.pop("DISPLAY", None)

    def __exit__(self, *_):
        if self.saved is not None:
            os.environ["DISPLAY"] = self.saved


class TestWatchError(unittest.TestCase):
    def rig(self, **kw):
        rig = BridgeRig(showWatch=False, **kw)
        self.addCleanup(rig.close)
        return rig

    def test_turning_it_on_without_a_display_gives_the_message_and_it_goes_back_off(self):
        rig = self.rig()
        rig.send("settings-open")
        with NoDisplay():
            state = rig.save(showWatch=True)
        self.assertEqual(state["watch"]["error"], WATCH_ERROR)
        self.assertFalse(state["settingsOpen"])
        self.assertFalse(rig.file()["showWatch"])
        self.assertEqual(state["phase"], "ready")
        self.assertIsNone(state["error"])

    def test_the_launcher_is_not_in_its_error_state_and_play_still_starts_and_clears_the_message(self):
        rig = self.rig(startAutomatically=False)
        rig.send("settings-open")
        with NoDisplay():
            rig.save(showWatch=True)
        self.assertEqual(rig.state()["phase"], "ready")
        state = rig.send("play")
        self.assertEqual(rig.runner.starts[-1]["label"], "update")
        self.assertIsNone(state["watch"]["error"])

    def test_home_clears_it_and_a_notice_does_not(self):
        rig = self.rig()
        rig.send("settings-open")
        with NoDisplay():
            rig.save(showWatch=True)
        rig.send("home")
        self.assertIsNone(rig.state()["watch"]["error"])

    def test_it_stays_while_another_notice_comes(self):
        rig = self.rig()
        rig.send("settings-open")
        with NoDisplay():
            rig.save(showWatch=True)
        rig.bridge._notice = {"tone": "info", "text": "Requested News in Chrome."}
        self.assertEqual(rig.state()["watch"]["error"], WATCH_ERROR)

    def test_turning_it_on_again_clears_the_old_message_before_trying(self):
        rig = self.rig()
        rig.send("settings-open")
        with NoDisplay():
            rig.save(showWatch=True)
        rig.send("settings-open")
        rig.save(showWatch=True)
        self.assertIsNone(rig.state()["watch"]["error"])
        self.assertTrue(rig.file()["showWatch"])


class TestStartAutomatically(unittest.TestCase):
    def rig(self, **kw):
        rig = BridgeRig(**kw)
        self.addCleanup(rig.close)
        return rig

    def test_launch_requested_turns_it_on_and_saves(self):
        rig = self.rig(showWatch=False)
        rig.send("play")
        rig.runner.finish("Homecoming is up to date\n")
        rig.runner.finish("")
        state = rig.state()
        self.assertEqual(state["notice"]["text"], "Launch requested")
        self.assertTrue(rig.file()["showWatch"])
        self.assertTrue(state["settings"]["showWatch"])
        self.assertTrue(wait_until(rig.bridge._watch.helper_ready, 8))

    def test_nothing_changes_when_it_is_already_on(self):
        rig = self.rig(showWatch=True)
        serial = rig.state()["settings"]["showWatchSerial"]
        rig.send("play")
        rig.runner.finish("Homecoming is up to date\n")
        rig.runner.finish("")
        self.assertEqual(rig.state()["settings"]["showWatchSerial"], serial)

    def test_off_when_start_automatically_is_off(self):
        rig = self.rig(showWatch=False, startAutomatically=False)
        rig.send("play")
        rig.runner.finish("Homecoming is up to date\n")
        rig.runner.finish("")
        self.assertFalse(rig.file()["showWatch"])

    def test_a_game_start_error_does_not_turn_it_on(self):
        rig = self.rig(showWatch=False)
        rig.send("play")
        rig.runner.finish("Homecoming is up to date\n")
        rig.runner.finish("ERROR: could not start\n", code=1)
        self.assertFalse(rig.file()["showWatch"])
        self.assertFalse(rig.state()["settings"]["showWatch"])

    def test_the_time_limit_warning_during_an_update_does_not_turn_it_on(self):
        rig = self.rig(showWatch=False)
        rig.send("play")
        rig.runner.warn_time_limit()
        self.assertFalse(rig.file()["showWatch"])

    def test_the_time_limit_warning_during_the_launch_turns_it_on_and_stays_on_after_a_later_error(self):
        rig = self.rig(showWatch=False)
        rig.send("play")
        rig.runner.finish("Homecoming is up to date\n")
        rig.runner.warn_time_limit()
        self.assertTrue(rig.file()["showWatch"])
        self.assertTrue(rig.state()["settings"]["showWatch"])
        self.assertIn("did not finish", rig.state()["notice"]["text"])
        rig.runner.finish("ERROR: late failure\n", code=1)
        self.assertTrue(rig.file()["showWatch"])

    def test_open_settings_switch_follows_and_save_and_cancel_keep_it_on(self):
        rig = self.rig(showWatch=False)
        rig.send("play")
        rig.send("settings-open")
        before = rig.state()["settings"]["showWatchSerial"]
        rig.runner.finish("Homecoming is up to date\n")
        rig.runner.finish("")
        settings = rig.state()["settings"]
        self.assertTrue(settings["showWatch"])
        self.assertNotEqual(settings["showWatchSerial"], before)
        rig.send("settings-cancel")
        self.assertTrue(rig.file()["showWatch"])

    def test_failure_while_settings_is_open_turns_the_switch_off_and_shows_the_message(self):
        rig = self.rig(showWatch=False)
        rig.send("play")
        rig.send("settings-open")
        with NoDisplay():
            rig.runner.finish("Homecoming is up to date\n")
            rig.runner.finish("")
        state = rig.state()
        self.assertFalse(state["settings"]["showWatch"])
        self.assertEqual(state["watch"]["error"], WATCH_ERROR)
        self.assertEqual(state["notice"]["text"], "Launch requested")
        rig.save(showWatch=False)
        self.assertFalse(rig.file()["showWatch"])

    def test_no_game_folder_message_follows_the_saved_folder(self):
        rig = self.rig(showWatch=True, gameRoot="")
        self.assertEqual(rig.state()["watch"]["gameFolderMessage"], "Set your game folder in the General tab.")
        rig.send("settings-open")
        rig.save(gameRoot=str(rig.root), protonPath=str(rig.proton), compatPrefix=str(rig.prefix))
        self.assertIsNone(rig.state()["watch"]["gameFolderMessage"])
        rig.send("settings-open")
        rig.save(showWatch=False)
        self.assertIsNone(rig.state()["watch"]["gameFolderMessage"])

    def test_turning_it_off_and_saving_closes_the_helper(self):
        rig = self.rig(showWatch=True)
        self.assertTrue(wait_until(rig.bridge._watch.helper_ready, 8))
        rig.send("settings-open")
        rig.save(showWatch=False)
        self.assertFalse(rig.bridge._watch.helper_ready())
        self.assertEqual(harness.child_pids("launcher.linux.watchhelper"), [])

    def test_quitting_leaves_no_helper(self):
        rig = self.rig(showWatch=True)
        self.assertTrue(wait_until(rig.bridge._watch.helper_ready, 8))
        rig.bridge.shutdown()
        pump(0.3)
        self.assertEqual(harness.child_pids("launcher.linux.watchhelper"), [])


if __name__ == "__main__":
    unittest.main()
