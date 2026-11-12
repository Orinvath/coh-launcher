"""Shared helpers for the tests: the hidden screen, stand-in game, stand-in password store and Fish Audio, and waiting.

Everything started here is stopped by its own process id. Nothing touches the real desktop or the launcher's own var folder.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# No test ever reaches the desktop's own message bus: without a private bus the password store is simply not there.
NO_BUS = "unix:path=/nonexistent/coh-test-no-bus"
os.environ.setdefault("COH_TEST_SECRETS_BUS", NO_BUS)
TMP = ROOT / "tests" / "tmp"
HERE = Path(__file__).resolve().parent


def app():
    return QApplication.instance() or QApplication(sys.argv[:1])


def wait_until(check, seconds=8.0, step=0.02):
    """Run the Qt event loop until check() is true. Returns the last value of check()."""
    deadline = time.monotonic() + seconds
    value = check()
    while not value and time.monotonic() < deadline:
        app().processEvents()
        time.sleep(step)
        value = check()
    return value


def pump(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app().processEvents()
        time.sleep(0.01)


def fresh_dir(name):
    TMP.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix=name + "-", dir=TMP))
    return path


def clean_tmp():
    shutil.rmtree(TMP, ignore_errors=True)


def make_game(base):
    """A test game folder: the Homecoming install files and the stand-in program's place."""
    root = Path(base) / "game"
    (root / "bin/win64/live").mkdir(parents=True)
    (root / "bin/win64/launchercli.exe").write_bytes(b"")
    (root / "bin/win64/live/cityofheroes.exe").write_bytes(b"")
    (root / "settings/launcher/profiles").mkdir(parents=True)
    return root


def write_config(config_dir, **overrides):
    """A version 2 settings file with test values."""
    from launcher import config as cfgmod

    data = {"version": 2, "gameRoot": "", "protonPath": "", "compatPrefix": "", "skinId": "homecoming", "scale": 1,
            "lastProfile": "live", "showWatch": False, "startAutomatically": True, "watchSpot": None,
            "alertChime": True, "speakAlerts": False, "voiceId": None}
    data.update(overrides)
    cfgmod.save_config(config_dir, data)
    return data


def chat_log(game_root, account="acct", day="2026-11-11"):
    folder = Path(game_root) / "accounts" / account / "Logs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"chatlog {day}.txt"


def append(path, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    with open(path, "ab") as handle:
        handle.write(data)


def log_line(text, stamp="2026-11-11 09:00:00"):
    return f"{stamp} {text}\r\n"


class StandInGame:
    """A program named cityofheroes.exe in a test game folder, with one window."""

    def __init__(self, exe, title="City of Heroes"):
        self.process = subprocess.Popen(
            [str(exe), str(HERE / "fake_game.py"), title], executable=sys.executable,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        line = self.process.stdout.readline().split()
        self.window = int(line[1])

    def tell(self, word):
        self.process.stdin.write(word + "\n")
        self.process.stdin.flush()

    def stop(self):
        if self.process.poll() is None:
            self.tell("quit")
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def set_active_window(window_id):
    """Play the window manager: set the root window's active-window value (0 for none)."""
    from Xlib import X, Xatom, display

    shown = display.Display()
    root = shown.screen().root
    root.change_property(shown.intern_atom("_NET_ACTIVE_WINDOW"), Xatom.WINDOW, 32, [window_id], X.PropModeReplace)
    shown.sync()
    shown.close()


def child_pids(cmd_part):
    """Processes started by this test process whose command line holds cmd_part."""
    found = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                if (entry / "stat").read_text().split(") ")[-1].split()[1] == str(os.getpid()) and \
                        cmd_part in (entry / "cmdline").read_text().replace("\0", " "):
                    found.append(int(entry.name))
            except OSError:
                continue
    return found


def x_windows(pid, visible=True):
    command = ["xdotool", "search"] + (["--onlyvisible"] if visible else []) + ["--pid", str(pid)]
    result = subprocess.run(command, capture_output=True, text=True)
    return [int(w) for w in result.stdout.split()]


def window_rect(window_id):
    out = subprocess.run(["xdotool", "getwindowgeometry", str(window_id)], capture_output=True, text=True).stdout
    position = out.split("Position:")[1].split("(")[0].strip().split(",")
    size = out.split("Geometry:")[1].strip().split("x")
    return int(position[0]), int(position[1]), int(size[0]), int(size[1])


class PrivateBus:
    """A private message bus with the stand-in password store. The real desktop bus is never used."""

    def __init__(self, base):
        self.store = Path(base) / "store.json"
        self.mode = Path(base) / "mode"
        self.mode.write_text("ok")
        self.bus = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                                    stdout=subprocess.PIPE, text=True)
        self.address = self.bus.stdout.readline().strip()
        env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=self.address)
        self.service = subprocess.Popen([sys.executable, str(HERE / "fake_secret_service.py"), str(self.store), str(self.mode)],
                                        env=env, stdout=subprocess.PIPE, text=True)
        self.service.stdout.readline()
        os.environ["COH_TEST_SECRETS_BUS"] = self.address

    def key(self):
        return json.loads(self.store.read_text()).get("value") if self.store.exists() else None

    def set_key(self, value):
        self.store.write_text(json.dumps({"value": value} if value else {}))

    def set_mode(self, mode):
        self.mode.write_text(mode)

    def stop_service(self):
        """The store stops running (nothing answers on the bus)."""
        if self.service.poll() is None:
            self.service.kill()
            self.service.wait()

    def stop(self):
        os.environ["COH_TEST_SECRETS_BUS"] = NO_BUS
        for process in (self.service, self.bus):
            if process.poll() is None:
                process.kill()
                process.wait()


class FakeFish:
    """A stand-in Fish Audio on a local port. Records every call; answers as told."""

    def __init__(self):
        self.own = []
        self.public = []
        self.pages = {}
        self.calls = []
        self.tts_status = 200
        self.tts_delay = 0.0
        self.tts_body = b"FAKE-MP3 dur=0.3"
        self.tts_error_text = ""
        self.model_errors = 0
        self.list_status = 200
        self.list_delay = 0.0
        self.has_more = True
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _reply(self, status, body, kind="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urlparse(self.path)
                query = {key: values[0] for key, values in parse_qs(url.query).items()}
                outer.calls.append({"method": "GET", "path": url.path, "query": query, "time": time.time(),
                                    "auth": self.headers.get("Authorization")})
                time.sleep(outer.list_delay)
                if outer.list_status != 200:
                    self._reply(outer.list_status, b"{}")
                    return
                source = outer.own if query.get("self") == "true" else outer.public
                number, size = int(query["page_number"]), int(query["page_size"])
                items = source[(number - 1) * size:number * size]
                answer = {"items": items, "total": len(source)}
                if outer.has_more:
                    answer["has_more"] = number * size < len(source)
                self._reply(200, json.dumps(answer).encode())

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.calls.append({"method": "POST", "path": self.path, "body": body, "time": time.time(),
                                    "auth": self.headers.get("Authorization"), "model": self.headers.get("model")})
                time.sleep(outer.tts_delay)
                if outer.model_errors and self.headers.get("model") == "s2.1-pro-free":
                    self._reply(402, b'{"message": "model not available"}')
                elif outer.tts_status != 200:
                    self._reply(outer.tts_status, outer.tts_error_text.encode() or b"{}")
                else:
                    self._reply(200, outer.tts_body, "audio/mpeg")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        os.environ["COH_TEST_FISH_URL"] = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tts_calls(self):
        return [call for call in self.calls if call["method"] == "POST"]

    def stop(self):
        os.environ.pop("COH_TEST_FISH_URL", None)
        self.server.shutdown()
        self.server.server_close()


def voice(voice_id, title=None, **fields):
    item = {"_id": voice_id, "title": title or voice_id, "type": "tts", "state": "trained",
            "visibility": "public", "dmca_taken_down": False, "takedown_category": ""}
    item.update(fields)
    return item


def sound_events(log):
    path = Path(log)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class WatchRig:
    """A watch controller with a stand-in game, a recording sound player, a stand-in password store and Fish Audio."""

    def __init__(self, speak=False, voice="voice-1", chime=True, key="the-key", store=True):
        from launcher import pieces as piecesmod
        from launcher.watchcontroller import WatchController

        self.base = fresh_dir("rig")
        self.root = make_game(self.base)
        self.log = chat_log(self.root)
        self.sound_log = self.base / "sound.log"
        os.environ["COH_TEST_SOUND_LOG"] = str(self.sound_log)
        self.fish = FakeFish()
        self.bus = PrivateBus(self.base) if store else None
        if self.bus and key:
            self.bus.set_key(key)
        self.pieces = piecesmod.load()
        secret = self.pieces.secret_store(None) if store else None
        self.cache = self.base / "voice"
        self.control = WatchController(self.pieces, secret, self.cache)
        self.control.configure(str(self.root), chime, speak, voice, None)
        self.sent = []
        self.control._send = lambda **command: self.sent.append(command)
        self.control.set_enabled(True)
        self.game = StandInGame(self.root / "bin/win64/live/cityofheroes.exe")
        if not wait_until(self.control.game_running, 6):
            raise RuntimeError("the stand-in game was not found")

    def alert(self, text, stamp="2026-11-11 09:00:00"):
        append(self.log, log_line(text, stamp))

    def configure(self, chime=True, speak=False, voice="voice-1"):
        self.control.configure(str(self.root), chime, speak, voice, None)

    def events(self, kind=None):
        rows = sound_events(self.sound_log)
        return [r for r in rows if kind is None or r.get("kind") == kind or r["event"] == kind]

    def plays(self):
        return [(r["kind"]) for r in sound_events(self.sound_log) if r["event"] == "play"]

    def stop(self):
        self.control.shutdown()
        self.game.stop()
        self.fish.stop()
        if self.bus:
            self.bus.stop()
        os.environ.pop("COH_TEST_SOUND_LOG", None)


LIST_OUTPUT = (
    "ID           Name            Version                        State       \n"
    "------------ --------------- ------------------------------ ------------\n"
    "beta         Open Beta                                      notinstalled\n"
    "live         Homecoming      Issue 28, Page 4 - 28.4.8623   installed   \n"
)


class FakeRunner:
    """Stands in for the official launcher runner. A job ends only when the test says so."""

    def __init__(self):
        from PySide6.QtCore import QObject, Signal

        class Signals(QObject):
            output = Signal(str, str)
            timed_out = Signal(str)
            finished = Signal(str, object)

        self._signals = Signals()
        self.output, self.timed_out, self.finished = self._signals.output, self._signals.timed_out, self._signals.finished
        self.starts = []

    def start(self, job_id, argv, cwd, env, timeout_ms, inactivity=False, label="job"):
        self.starts.append({"job_id": job_id, "label": label})

    def last(self):
        return self.starts[-1]["job_id"]

    def finish(self, text="", code=0):
        self.finished.emit(self.last(), {"exit_code": code, "timed_out": False, "output": text})

    def warn_time_limit(self):
        self.timed_out.emit(self.last())


class BridgeRig:
    """A launcher bridge on a test settings folder, a stand-in home, and a runner that ends jobs when told."""

    def __init__(self, config=True, valid_paths=True, home=None, store=False, base=None, **settings):
        from launcher.bridge import Bridge, Hooks

        self.base = base or fresh_dir("bridge")
        self.config_dir = self.base / "cfg"
        self.root = self.base / "game"
        if not self.root.exists():
            make_game(self.base)
        self.proton = self.base / "proton"
        self.proton.write_bytes(b"")
        self.prefix = self.base / "prefix"
        (self.prefix / "pfx/drive_c").mkdir(parents=True, exist_ok=True)
        self.scales = []
        self.home = home if home is not None else self.base / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self._saved_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        if config:
            paths = {"gameRoot": str(self.root), "protonPath": str(self.proton), "compatPrefix": str(self.prefix)}
            write_config(self.config_dir, **{**(paths if valid_paths else {}), **settings})
        self.bus = PrivateBus(self.base) if store else None
        self.runner = FakeRunner()
        noop = lambda *args: None  # noqa: E731
        hooks = Hooks(noop, noop, noop, noop, noop, noop, noop, self.scales.append)
        self.bridge = Bridge(hooks, self.runner, shard_fetcher=lambda: [], config_dir=self.config_dir,
                             feed_fetcher=lambda callback: callback(None, "offline"))
        self.bridge.start()
        if self.runner.starts and self.runner.starts[-1]["label"] == "discover":
            self.runner.finish(LIST_OUTPUT)

    def state(self):
        return self.bridge.snapshot()

    def send(self, action, **payload):
        import json

        self.bridge.dispatch(action, json.dumps(payload))
        pump(0.05)
        return self.state()

    def payload(self, **over):
        s = self.state()["settings"]
        data = {"gameRoot": s["gameRoot"], "protonPath": s["protonPath"], "compatPrefix": s["compatPrefix"],
                "skinId": s["skinId"], "scale": s["scale"], "showWatch": s["showWatch"],
                "startAutomatically": s["startAutomatically"], "alertChime": s["alertChime"],
                "speakAlerts": s["speakAlerts"], "voiceId": s["voiceId"], "key": {"mode": "keep", "value": ""}}
        data.update(over)
        return data

    def save(self, **over):
        return self.send("settings-save", **self.payload(**over))

    def file(self):
        return json.loads((self.config_dir / "config.json").read_text())

    def close(self):
        self.bridge.shutdown()
        if self.bus:
            self.bus.stop()
        if self._saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._saved_home
