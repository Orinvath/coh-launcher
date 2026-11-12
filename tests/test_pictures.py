"""app-run.json: the start command opens the real window, the picture command saves pictures and closes everything."""

import json
import os
import shlex
import subprocess
import time
import unittest

import harness
from harness import ROOT, fresh_dir

from PIL import Image


class TestAppRun(unittest.TestCase):
    def test_app_run_json_has_both_commands(self):
        commands = json.loads((ROOT / "app-run.json").read_text())
        self.assertEqual(set(commands), {"start", "picture"})
        self.assertIn("{out}", commands["picture"])

    def test_picture_command_saves_the_window_and_the_watch_pictures_and_leaves_nothing_running(self):
        out = fresh_dir("picture") / "window.png"
        command = json.loads((ROOT / "app-run.json").read_text())["picture"].replace("{out}", shlex.quote(str(out)))
        before = set(_processes())
        result = subprocess.run(command, shell=True, cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        window = Image.open(out)
        self.assertGreater(window.width, 1000)
        for name in ("day", "night", "alert"):
            watch = Image.open(out.with_name(f"window-{name}.png"))
            self.assertEqual(watch.size, (188, 250), name)
        time.sleep(1.0)
        left = [p for p in _processes() if p not in before]
        self.assertEqual(left, [], "the picture command left processes running")

    def test_start_command_opens_the_window_and_closes_with_its_process(self):
        folder = fresh_dir("start")
        env = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}
        env["HOME"] = str(folder)
        start = json.loads((ROOT / "app-run.json").read_text())["start"]
        # The start command with a test settings folder, so the launcher's own var folder is never used.
        process = subprocess.Popen(f"exec {start} --config-dir {shlex.quote(str(folder / 'cfg'))}", shell=True, cwd=ROOT, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 30
            windows = []
            while time.monotonic() < deadline and not windows:
                windows = harness.x_windows(process.pid, visible=True)
                time.sleep(0.3)
            self.assertTrue(windows, "the window did not appear")
        finally:
            process.terminate()
            process.wait(15)
        self.assertIsNotNone(process.returncode)


def _processes():
    found = []
    for entry in os.scandir("/proc"):
        if entry.name.isdigit():
            try:
                command = open(f"/proc/{entry.name}/cmdline", "rb").read().replace(b"\0", b" ").decode()
            except OSError:
                continue
            if any(word in command for word in ("Xvfb", "fake_game", "fake_secret_service", "dbus-daemon --session",
                                                "launcher.linux.watchhelper", "picture_window")):
                found.append(entry.name)
    return found


if __name__ == "__main__":
    unittest.main()
