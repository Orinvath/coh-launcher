"""Feature 4 on a hidden screen: the watch box over a stand-in game, its size and spot, hiding and showing,
dragging, and the helper's life."""

import os
import signal
import subprocess
import time
import unittest

import harness
from harness import (StandInGame, child_pids, fresh_dir, make_game, pump, set_active_window, wait_until, window_rect,
                     x_windows)

from launcher import pieces as piecesmod
from launcher.watchcontroller import WatchController


def setUpModule():
    harness.app()


class WatchWindowCase(unittest.TestCase):
    def setUp(self):
        self.base = fresh_dir("watch")
        self.root = make_game(self.base)
        self.exe = self.root / "bin/win64/live/cityofheroes.exe"
        self.pieces = piecesmod.load()
        self.control = WatchController(self.pieces, None, self.base / "voice")
        self.control.configure(str(self.root), False, False, None, None)
        self.failures = []
        self.control.failed.connect(self.failures.append)
        self.moves = []
        self.control.moved.connect(lambda x, y: self.moves.append((x, y)))
        self.games = []

    def tearDown(self):
        self.control.shutdown()
        for game in self.games:
            game.stop()

    def start_game(self, exe=None, title="City of Heroes"):
        game = StandInGame(exe or self.exe, title)
        self.games.append(game)
        return game

    def helper_pid(self):
        pids = child_pids("launcher.linux.watchhelper")
        return pids[0] if pids else None

    def box(self, visible=True):
        pid = self.helper_pid()
        windows = x_windows(pid, visible) if pid else []
        return windows[0] if windows else None

    def front(self, game):
        set_active_window(game.window)


class TestBoxOverGame(WatchWindowCase):
    def test_first_spot_and_size(self):
        self.control.set_enabled(True)
        game = self.start_game()
        self.front(game)
        self.assertTrue(wait_until(self.box), "the watch box never showed")
        self.assertEqual(window_rect(self.box()), (20, 16, 188, 250))
        self.assertTrue(wait_until(lambda: self.moves))
        self.assertEqual(self.moves[-1], (20, 16))

    def test_hides_for_another_window_and_shows_again(self):
        self.control.set_enabled(True)
        game = self.start_game()
        other = self.start_game(self.base / "other.exe", "City of Heroes")
        self.front(game)
        self.assertTrue(wait_until(self.box))
        started = time.monotonic()
        self.front(other)
        self.assertTrue(wait_until(lambda: self.box() is None, 2))
        self.assertLess(time.monotonic() - started, 0.7)
        self.front(game)
        self.assertTrue(wait_until(self.box, 2))
        set_active_window(0)
        self.assertTrue(wait_until(lambda: self.box() is None, 2))

    def test_minimised_game_hides_the_watch(self):
        self.control.set_enabled(True)
        game = self.start_game()
        self.front(game)
        self.assertTrue(wait_until(self.box))
        game.tell("hide")
        self.assertTrue(wait_until(lambda: self.box() is None, 2))
        game.tell("show")
        pump(0.3)
        self.front(game)
        self.assertTrue(wait_until(self.box, 3))

    def test_program_from_another_folder_is_not_the_game(self):
        self.control.set_enabled(True)
        impostor = self.base / "elsewhere"
        (impostor / "bin/win64/live").mkdir(parents=True)
        exe = impostor / "bin/win64/live/cityofheroes.exe"
        exe.write_bytes(b"")
        game = self.start_game(exe)
        self.front(game)
        pump(1.5)
        self.assertIsNone(self.box())

    def test_helper_closes_with_the_watch(self):
        self.control.set_enabled(True)
        self.assertTrue(wait_until(self.control.helper_ready))
        pid = self.helper_pid()
        self.control.set_enabled(False)
        self.assertTrue(wait_until(lambda: not os.path.exists(f"/proc/{pid}") or
                                   open(f"/proc/{pid}/stat").read().split(") ")[1][0] == "Z", 3))
        self.assertIsNone(self.helper_pid())

    def test_game_closing_hides_the_watch_and_it_returns(self):
        self.control.set_enabled(True)
        game = self.start_game()
        self.front(game)
        self.assertTrue(wait_until(self.box))
        game.stop()
        self.assertTrue(wait_until(lambda: self.box() is None, 6))
        game2 = self.start_game()
        self.front(game2)
        self.assertTrue(wait_until(self.box, 6))


def mouse(*steps):
    """Drive the pointer on the hidden screen with xdotool."""
    subprocess.run(["xdotool", *steps], check=True)


class TestDragging(WatchWindowCase):
    def setUp(self):
        super().setUp()
        self.screen = (1920, 1080)

    def show(self, spot=None):
        self.control.configure(str(self.root), False, False, None, spot)
        self.control.set_enabled(True)
        game = self.start_game()
        self.front(game)
        self.assertTrue(wait_until(self.box, 6))
        return game

    def drag(self, to_x, to_y):
        x, y, _w, _h = window_rect(self.box())
        start = (x + 70, y + 50)
        mouse("mousemove", str(start[0]), str(start[1]), "mousedown", "1")
        for step in range(1, 6):
            mouse("mousemove", str(start[0] + (to_x - x) * step // 5), str(start[1] + (to_y - y) * step // 5))
            pump(0.05)
            self.assertIsNotNone(self.box(), "the watch must stay visible while it is dragged")
        mouse("mouseup", "1")
        pump(0.4)

    def test_drag_to_the_bottom_right_corner_saves_the_spot_and_it_opens_there_next_time(self):
        self.show()
        self.drag(self.screen[0] - 188, self.screen[1] - 250)
        self.assertEqual(window_rect(self.box()), (1732, 830, 188, 250))
        self.assertTrue(wait_until(lambda: (1732, 830) in self.moves))
        self.control.set_enabled(False)
        self.control = WatchController(self.pieces, None, self.base / "voice")
        self.show(spot=[1732, 830])
        self.assertEqual(window_rect(self.box()), (1732, 830, 188, 250))

    def test_half_past_the_right_edge_slides_back_inside(self):
        self.show()
        self.drag(self.screen[0] - 94, 300)
        self.assertEqual(window_rect(self.box()), (1732, 300, 188, 250))

    def test_a_saved_spot_off_every_screen_opens_at_the_starting_spot_and_is_saved(self):
        self.show(spot=[5000, 4000])
        self.assertEqual(window_rect(self.box()), (20, 16, 188, 250))
        self.assertTrue(wait_until(lambda: (20, 16) in self.moves))

    def test_a_saved_spot_on_the_screen_is_used(self):
        self.show(spot=[400, 300])
        self.assertEqual(window_rect(self.box()), (400, 300, 188, 250))

    def test_the_box_is_painted(self):
        from Xlib import X, display

        self.show()
        shown = display.Display()

        def colours():
            window = shown.create_resource_object("window", self.box())
            data = window.get_image(0, 0, 188, 250, X.ZPixmap, 0xFFFFFFFF).data
            return len(set(data[i:i + 4] for i in range(0, len(data), 4)))

        painted = wait_until(lambda: colours() > 50, 3)
        shown.close()
        self.assertTrue(painted)


class TestHelperRestart(WatchWindowCase):
    def kill_helper(self):
        pid = self.helper_pid()
        os.kill(pid, signal.SIGKILL)
        return pid

    def test_one_quick_stop_is_restarted_without_error(self):
        self.control.set_enabled(True)
        self.assertTrue(wait_until(self.control.helper_ready))
        first = self.kill_helper()
        self.assertTrue(wait_until(lambda: self.control.helper_ready() and self.helper_pid() not in (None, first), 6))
        pump(0.5)
        self.assertEqual(self.failures, [])

    def test_two_quick_stops_in_a_row_are_an_error(self):
        self.control.set_enabled(True)
        self.assertTrue(wait_until(self.control.helper_ready))
        first = self.kill_helper()
        self.assertTrue(wait_until(lambda: self.control.helper_ready() and self.helper_pid() not in (None, first), 6))
        second = self.helper_pid()
        pump(1.0)
        os.kill(second, signal.SIGKILL)
        self.assertTrue(wait_until(lambda: self.failures, 6))
        self.assertEqual(self.failures, ["The watch helper stopped twice within a minute."])
        pump(1.0)
        self.assertIsNone(self.helper_pid())

    def test_a_stop_after_a_long_run_is_restarted_again(self):
        os.environ["COH_TEST_HELPER_MINUTE"] = "1.5"
        self.addCleanup(os.environ.pop, "COH_TEST_HELPER_MINUTE", None)
        self.control.set_enabled(True)
        self.assertTrue(wait_until(self.control.helper_ready))
        first = self.kill_helper()
        self.assertTrue(wait_until(lambda: self.control.helper_ready() and self.helper_pid() not in (None, first), 6))
        second = self.helper_pid()
        pump(2.0)
        os.kill(second, signal.SIGKILL)
        self.assertTrue(wait_until(lambda: self.control.helper_ready() and self.helper_pid() not in (None, second), 6))
        pump(0.5)
        self.assertEqual(self.failures, [])


class TestNoDisplay(unittest.TestCase):
    def test_no_x11_display_is_the_reason(self):
        base = fresh_dir("nodisplay")
        control = WatchController(piecesmod.load(), None, base / "voice")
        failures = []
        control.failed.connect(failures.append)
        saved = os.environ.pop("DISPLAY")
        try:
            control.set_enabled(True)
        finally:
            os.environ["DISPLAY"] = saved
        self.assertEqual(failures, ["No X11 display was found."])

    def test_no_piece_for_this_system(self):
        base = fresh_dir("nopiece")
        control = WatchController(piecesmod.Pieces(), None, base / "voice")
        failures = []
        control.failed.connect(failures.append)
        control.set_enabled(True)
        self.assertEqual(failures, ["Not available on this system yet."])


if __name__ == "__main__":
    unittest.main()
