"""Features 5 and 6: following the chat log, which lines are city alerts, repeats, and the card's timing."""

import datetime as dt
import os
import unittest

import harness
from harness import StandInGame, append, chat_log, fresh_dir, log_line, make_game, pump, wait_until

from launcher import alerts, gametime, pieces as piecesmod, watchface
from launcher.chatlog import ChatLogFollower
from launcher.watchcontroller import WatchController


def setUpModule():
    harness.app()


class TestWhichLinesAreAlerts(unittest.TestCase):
    def test_alerts_from_the_game(self):
        for text in ("WARNING: Zombie attack in Skyway City.", "Adamastor has appeared in Echo: Dark Astoria.",
                     "ALERT CANCELED: The zombies have disappeared from Skyway City.",
                     "ALERT CANCELLED: The Rikti have retreated.", "DANGER!: Rikti invasion forces have been spotted.",
                     "WARNING: Zombie attack in 'Boomtown'.", "Jurassik has appeared in 'Crey's Folly'."):
            self.assertEqual(alerts.alert_text(text), text)

    def test_vanguard_tag_is_taken_off(self):
        self.assertEqual(
            alerts.alert_text("[Vanguard] WARNING: Rikti ships have been detected heading for Cap Au Diable."),
            "WARNING: Rikti ships have been detected heading for Cap Au Diable.")

    def test_chat_channels_never_count(self):
        for line in ("[General] WARNING: fake alert", "[Looking For Group] Someone: WARNING: Zombie attack in Skyway City.",
                     "[Tell] WARNING: x", "[Team] Adamastor has appeared in Echo: Dark Astoria.",
                     "Someone: WARNING: Zombie attack.", "Entering Atlas Plaza.", "Adamastor has appeared in Echo",
                     "[Vanguard] Hello there"):
            self.assertIsNone(alerts.alert_text(line), line)


class TestRepeats(unittest.TestCase):
    def shown(self, seconds, text="x"):
        repeat = alerts.RepeatFilter()
        base = dt.datetime(2026, 11, 11, 9, 0, 0)
        return [s for s in seconds if repeat.is_new(text, base + dt.timedelta(seconds=s))]

    def test_same_second_is_one_alert(self):
        self.assertEqual(self.shown([0, 0]), [0])

    def test_ten_seconds_later_is_new(self):
        self.assertEqual(self.shown([0, 10]), [0, 10])

    def test_counted_from_the_copy_that_made_a_card(self):
        self.assertEqual(self.shown([0, 3, 6, 9, 12]), [0, 6, 12])
        self.assertEqual(self.shown([0, 4, 8]), [0, 8])

    def test_earlier_log_time_is_new(self):
        self.assertEqual(self.shown([10, 5]), [10, 5])

    def test_different_texts_in_the_same_second(self):
        repeat = alerts.RepeatFilter()
        now = dt.datetime(2026, 11, 11, 9, 0, 0)
        self.assertTrue(repeat.is_new("a", now))
        self.assertTrue(repeat.is_new("b", now))

    def test_old_texts_are_forgotten(self):
        repeat = alerts.RepeatFilter()
        now = dt.datetime(2026, 11, 11, 9, 0, 0)
        repeat.is_new("a", now)
        repeat.forget_old(now + dt.timedelta(seconds=6))
        self.assertEqual(repeat._shown, {})


class TestCardTiming(unittest.TestCase):
    def test_showing_time(self):
        self.assertEqual(alerts.card_seconds(0), 10)
        self.assertEqual(alerts.card_seconds(30), 10)
        self.assertEqual(alerts.card_seconds(120), 13)

    def test_scroll_stands_still_for_two_seconds_then_15_px_per_second_then_stops(self):
        self.assertEqual(alerts.scroll_offset(1.9, 60), 0)
        self.assertEqual(alerts.scroll_offset(3.0, 60), 15)
        self.assertEqual(alerts.scroll_offset(100, 60), 60)

    def test_text_that_fits_does_not_move_and_long_text_does_not_fit(self):
        self.assertEqual(watchface.card_layout("WARNING: Zombie attack in Skyway City.").hidden, 0)
        self.assertEqual(watchface.card_layout(
            "ALERT CANCELED: The zombies have disappeared from Skyway City.").hidden, 0)
        self.assertGreater(watchface.card_layout("word " * 40).hidden, 0)

    def test_card_is_always_the_same_box(self):
        for text in ("a", "WARNING: Zombie attack in Skyway City.", "word " * 80):
            image = watchface.render(watchface.Card("5:39 PM", text, 3.0))
            self.assertEqual((image.width(), image.height()), (watchface.WIDTH, watchface.HEIGHT))


class TestFollowing(unittest.TestCase):
    def test_only_new_lines_are_read(self):
        root = make_game(fresh_dir("follow"))
        log = chat_log(root)
        append(log, log_line("WARNING: old one."))
        follower = ChatLogFollower(root)
        self.assertEqual(follower.poll(), ([], True))
        append(log, log_line("WARNING: new one."))
        lines, found = follower.poll()
        self.assertEqual([m for _, m in lines], ["WARNING: new one."])

    def test_a_new_files_first_line_is_read_and_an_old_other_account_is_not(self):
        root = make_game(fresh_dir("follow2"))
        old = chat_log(root, "other", "2026-11-09")
        append(old, log_line("WARNING: very old."))
        follower = ChatLogFollower(root)
        new = chat_log(root, "acct", "2026-11-12")
        append(new, log_line("WARNING: first line of a new day."))
        self.assertEqual([m for _, m in follower.poll()[0]], ["WARNING: first line of a new day."])
        append(old, log_line("WARNING: new in the old file."))
        os.utime(old, (9e9, 9e9))
        self.assertEqual([m for _, m in follower.poll()[0]], ["WARNING: new in the old file."])

    def test_unusable_lines_are_skipped_and_text_is_utf8(self):
        root = make_game(fresh_dir("follow3"))
        log = chat_log(root)
        follower = ChatLogFollower(root)
        append(log, b"\xef\xbb\xbf")
        append(log, b"\r\n")
        append(log, b"no date here\r\n")
        append(log, b"2026-11-11 09:00:00 bad \xff\xfe bytes\r\n")
        append(log, log_line("WARNING: It’s here."))
        self.assertEqual([m for _, m in follower.poll()[0]], ["WARNING: It’s here."])

    def test_half_written_line_waits_for_its_end(self):
        root = make_game(fresh_dir("follow4"))
        log = chat_log(root)
        follower = ChatLogFollower(root)
        append(log, "2026-11-11 09:00:00 WARNING: Zombie atta")
        self.assertEqual(follower.poll()[0], [])
        append(log, "ck in Skyway City.\r\n")
        self.assertEqual([m for _, m in follower.poll()[0]], ["WARNING: Zombie attack in Skyway City."])
        self.assertEqual(follower.poll()[0], [])

    def test_a_file_that_got_shorter_is_read_again_from_its_start(self):
        root = make_game(fresh_dir("follow5"))
        log = chat_log(root)
        follower = ChatLogFollower(root)
        append(log, log_line("WARNING: a long first line to make the file longer."))
        follower.poll()
        log.write_bytes(log_line("WARNING: short.").encode())
        self.assertEqual([m for _, m in follower.poll()[0]], ["WARNING: short."])

    def test_no_log_found(self):
        root = make_game(fresh_dir("follow6"))
        self.assertEqual(ChatLogFollower(root).poll(), ([], False))


class RunningGame(unittest.TestCase):
    """A watch controller with a stand-in game running. What it sends to the helper is recorded."""

    def setUp(self):
        self.base = fresh_dir("alerts")
        self.root = make_game(self.base)
        self.log = chat_log(self.root)
        append(self.log, log_line("WARNING: an old alert."))
        self.control = WatchController(piecesmod.load(), None, self.base / "voice")
        self.control.configure(str(self.root), False, False, None, None)
        self.sent = []
        self.control._send = lambda **command: self.sent.append(command)
        self.game = None

    def start(self):
        self.control.set_enabled(True)
        self.game = StandInGame(self.root / "bin/win64/live/cityofheroes.exe")
        self.assertTrue(wait_until(self.control.game_running, 5))

    def tearDown(self):
        self.control.shutdown()
        if self.game:
            self.game.stop()

    def cards(self):
        return [c for c in self.sent if c.get("cmd") == "view" and c["kind"] == "card"]

    def card_texts(self):
        return [c["text"] for c in self.cards()]


class TestAlertsOnTheWatch(RunningGame):
    def test_new_line_shows_within_two_seconds_and_old_ones_never(self):
        self.start()
        append(self.log, log_line("WARNING: Zombie attack in Skyway City."))
        self.assertTrue(wait_until(self.cards, 2.0))
        self.assertEqual(set(self.card_texts()), {"WARNING: Zombie attack in Skyway City."})

    def test_card_time_and_ripples(self):
        self.start()
        append(self.log, log_line("Adamastor has appeared in Echo: Dark Astoria."))
        self.assertTrue(wait_until(self.cards, 2.0))
        self.assertIn({"cmd": "ripples"}, self.sent)
        self.assertRegex(self.cards()[0]["time"], r"^\d{1,2}:\d\d (AM|PM)$")

    def test_chat_channel_line_shows_nothing(self):
        self.start()
        append(self.log, log_line("[General] WARNING: fake alert"))
        pump(1.5)
        self.assertEqual(self.cards(), [])

    def test_same_alert_twice_in_the_same_second_is_one_card_and_one_burst(self):
        self.start()
        line = log_line("Adamastor has appeared in Echo: Dark Astoria.")
        append(self.log, line + line)
        self.assertTrue(wait_until(self.cards, 2.0))
        pump(0.6)
        self.assertEqual(self.sent.count({"cmd": "ripples"}), 1)

    def test_a_newer_alert_replaces_the_card_at_once(self):
        self.start()
        append(self.log, log_line("WARNING: first."))
        self.assertTrue(wait_until(self.cards, 2.0))
        append(self.log, log_line("WARNING: second.", "2026-11-11 09:00:03"))
        self.assertTrue(wait_until(lambda: "WARNING: second." in self.card_texts(), 2.0))
        self.assertEqual(self.sent.count({"cmd": "ripples"}), 2)

    def test_card_goes_back_to_the_face_after_ten_seconds(self):
        self.start()
        append(self.log, log_line("WARNING: short."))
        self.assertTrue(wait_until(self.cards, 2.0))
        count = len(self.sent)
        self.assertTrue(wait_until(lambda: any(c.get("kind") == "face" for c in self.sent[count:]), 11.5))

    def test_nothing_after_the_game_closed(self):
        self.start()
        self.game.stop()
        self.assertTrue(wait_until(lambda: not self.control.game_running(), 6))
        append(self.log, log_line("WARNING: too late."))
        pump(1.5)
        self.assertEqual(self.cards(), [])

    def test_strip_time_changes_over_a_minute(self):
        os.environ["COH_TEST_NOW"] = "2026-11-11T12:15:00"
        self.addCleanup(os.environ.pop, "COH_TEST_NOW", None)
        self.start()
        append(self.log, log_line("WARNING: stays."))
        self.assertTrue(wait_until(self.cards, 2.0))
        self.assertTrue(wait_until(lambda: len({c["time"] for c in self.cards()}) > 1, 4.0))


class TestChatLogMessage(RunningGame):
    def test_no_log_message_shows_goes_with_a_log_and_with_the_game(self):
        for path in self.root.glob("accounts"):
            import shutil
            shutil.rmtree(path)
        self.start()
        self.assertTrue(wait_until(lambda: self.control.messages()["chatlog"], 3))
        chat_log(self.root).write_bytes(b"")
        self.assertTrue(wait_until(lambda: not self.control.messages()["chatlog"], 3))
        shutil_rm = __import__("shutil").rmtree
        shutil_rm(self.root / "accounts")
        self.assertTrue(wait_until(lambda: self.control.messages()["chatlog"], 3))
        self.game.stop()
        self.assertTrue(wait_until(lambda: not self.control.messages()["chatlog"], 6))

    def test_no_message_while_the_game_is_not_running(self):
        __import__("shutil").rmtree(self.root / "accounts")
        self.control.set_enabled(True)
        pump(2.0)
        self.assertFalse(self.control.messages()["chatlog"])


class TestGameClock(unittest.TestCase):
    def clock(self, h, m, s=0):
        return gametime.game_clock(3600 * 7 + h * 3600 + m * 60 + s)

    def test_noon_and_midnight(self):
        noon = gametime.game_clock(12 * 3600 + 15 * 60)
        self.assertEqual((noon.text, noon.is_day), ("12:00 PM", True))
        midnight = gametime.game_clock(12 * 3600)
        self.assertEqual((midnight.text, midnight.is_day), ("12:00 AM", False))

    def test_a_game_minute_every_1_25_seconds_so_ten_seconds_is_eight_minutes(self):
        start = gametime.game_clock(1000 * 3600)
        later = gametime.game_clock(1000 * 3600 + 10)
        self.assertEqual(later.minute - start.minute, 8)

    def test_example_from_the_game(self):
        self.assertEqual(gametime.game_clock(1.0 * 3600 + 17.65 / 24 * 1800).text, "5:39 PM")

    def test_day_and_night_edges(self):
        edge = lambda h, m: gametime.game_clock(7 * 60 + 30 + (h * 60 + m - 360) * 1.25)  # noqa: E731
        self.assertEqual(gametime.game_clock(449.9).text, "5:59 AM")
        self.assertFalse(gametime.game_clock(449.9).is_day)
        self.assertEqual(gametime.game_clock(450).text, "6:00 AM")
        self.assertTrue(gametime.game_clock(450).is_day)
        self.assertEqual(gametime.game_clock(1574.9).text, "8:59 PM")
        self.assertTrue(gametime.game_clock(1574.9).is_day)
        self.assertEqual(gametime.game_clock(1575).text, "9:00 PM")
        self.assertFalse(gametime.game_clock(1575).is_day)
        self.assertEqual(gametime.game_clock(1799.99).text, "11:59 PM")
        self.assertEqual(gametime.game_clock(1800).text, "12:00 AM")
        self.assertEqual(gametime.game_clock(899.9).text, "11:59 AM")
        self.assertEqual(gametime.game_clock(900).text, "12:00 PM")
        del edge

    def test_every_time_fits_in_the_box(self):
        for h in range(24):
            for m in range(0, 60, 7):
                clock = gametime.GameClock(h, m)
                view = watchface.Face(clock.digits, clock.ampm, clock.is_day)
                self.assertEqual(watchface.render(view).width(), watchface.WIDTH)
                self.assertLess(watchface.text_width(clock.digits, clock.ampm), watchface.DESIGN_W - 10)


if __name__ == "__main__":
    unittest.main()
