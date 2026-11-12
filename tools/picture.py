#!/usr/bin/env python3
"""app-run.json "picture": opens the real launcher window with stand-in data on its own hidden screen,
saves a picture of it to the given path, and closes everything it started.

    tools/picture.py <out.png>

Beside <out.png> it also writes <out>-day.png, <out>-night.png and <out>-alert.png: the watch's day face,
night face and an alert card, painted by the launcher's own watch painting code at 152 x 100 pixels.
The stand-in data (a home folder, a game folder, a password store with a key, a Fish Audio with voices, a game
program) lives in a temporary folder inside the project. Nothing of the user's is read or written.
Every process started here is stopped by its own process id.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

import harness  # noqa: E402  (the stand-in password store, Fish Audio and game program)

WINDOW_SCRIPT = ROOT / "tools" / "picture_window.py"
SCREEN = "1700x1000x24"
STAND_IN_KEY = "stand-in-key"


def start_screen(work):
    """A hidden X11 screen. Returns (process, DISPLAY value)."""
    shown = work / "display"
    with shown.open("wb") as handle:
        process = subprocess.Popen(["Xvfb", "-screen", "0", SCREEN, "-nolisten", "tcp", "-displayfd", str(handle.fileno())],
                                   pass_fds=(handle.fileno(),), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        if shown.read_text().strip():
            return process, ":" + shown.read_text().strip()
        harness.time.sleep(0.1)
    process.kill()
    raise RuntimeError("The hidden screen did not start.")


def stand_in_home(work):
    """A home folder with a Homecoming install, a prefix and a Proton, in the usual places."""
    home = work / "home"
    game = home / "Games/Homecoming/pfx/drive_c/Games/Homecoming"
    (game / "bin/win64/live").mkdir(parents=True)
    (game / "bin/win64/launchercli.exe").write_bytes(b"")
    (game / "bin/win64/live/cityofheroes.exe").write_bytes(b"")
    (game / "settings/launcher/profiles").mkdir(parents=True)
    proton = home / ".local/share/Steam/steamapps/common/Proton 10.0"
    proton.mkdir(parents=True)
    (proton / "proton").write_bytes(b"")
    return home, game


def main(out):
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    harness.TMP.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="picture-", dir=harness.TMP))
    started = []
    bus = fish = game = None
    try:
        screen, display = start_screen(work)
        started.append(screen)
        home, game_root = stand_in_home(work)
        bus = harness.PrivateBus(work)
        bus.set_key(STAND_IN_KEY)
        fish = harness.FakeFish()
        fish.own = [harness.voice("own-1", "Narrator"), harness.voice("own-2", "Calm hero")]
        fish.public = [harness.voice(f"pub-{n}", title) for n, title in enumerate(("Bright guide", "Deep announcer"))]
        env = {key: value for key, value in os.environ.items() if key != "WAYLAND_DISPLAY"}
        env.update(DISPLAY=display, GDK_BACKEND="x11", QT_QPA_PLATFORM="xcb", QT_ENABLE_HIGHDPI_SCALING="0",
                   HOME=str(home), COH_TEST_SOUND_LOG=str(work / "sound.log"), COH_TEST_SECRETS_BUS=bus.address,
                   COH_TEST_FISH_URL=os.environ["COH_TEST_FISH_URL"])
        game = subprocess.Popen(
            [str(game_root / "bin/win64/live/cityofheroes.exe"), str(ROOT / "tests/fake_game.py")],
            executable=sys.executable, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        game.stdout.readline()
        window = subprocess.run([sys.executable, str(WINDOW_SCRIPT), str(out), str(work / "config")], env=env, cwd=ROOT)
        return window.returncode
    finally:
        if game is not None and game.poll() is None:
            game.stdin.write("quit\n")
            game.stdin.flush()
            game.wait(10)
        if fish is not None:
            fish.stop()
        if bus is not None:
            bus.stop()
        for process in started:
            process.terminate()
            process.wait(10)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(" ".join(sys.argv[1:])))
