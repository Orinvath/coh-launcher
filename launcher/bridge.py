"""Native bridge: strict action allowlist, validated payloads, and the one state machine.

The page calls dispatch(action, payloadJSON) and receives stateChanged(JSON).
Unknown actions, unknown payload fields and out-of-allowlist profiles or
paths are refused before any effect. Only one job runs at a time, and every
job completion carries its job id so a stale result cannot overwrite newer
state.
"""

import datetime as dt
import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from . import config as cfgmod
from . import feed as feedmod
from . import homecoming as hc
from . import messages
from . import pieces as piecesmod
from . import skins as skinmod
from .voicesettings import VoiceSettings
from .watchcontroller import WatchController

SHARD_REFRESH_MS = 60_000
MAX_PAYLOAD_CHARS = 16_384
OUTPUT_TAIL_LIMIT = 256 * 1024
BUSY_PERCENT_CAP = 99  # 100 shows only after official success
PERCENT_RE = re.compile(r"(?<![0-9])(100|[0-9]{1,2})\s?%")
APPLYING_RE = re.compile(r"applying", re.IGNORECASE)
PHASE_ORDER = ("checking", "downloading", "applying", "launching")
ACTIONS = frozenset(
    {
        "play", "beta", "confirm-install", "cancel-install", "retry", "home",
        "news", "forums", "discord", "support", "article", "screenshots", "capture-studio",
        "settings-open", "settings-cancel", "settings-save", "settings-tab", "key-typed",
        "choose-game-root", "choose-proton", "choose-prefix", "open-official",
        "minimize", "close", "close-confirm", "close-cancel", "window-drag",
    }
)
TABS = ("general", "addons")
KEY_MODES = ("keep", "set", "remove")
MAX_KEY_CHARS = 4096
PAYLOAD_KEYS = {
    "confirm-install": frozenset({"profile"}),
    "article": frozenset({"id"}),
    "settings-save": frozenset({
        "gameRoot", "protonPath", "compatPrefix", "skinId", "scale",
        "showWatch", "startAutomatically", "alertChime", "speakAlerts", "voiceId", "key",
    }),
    "settings-tab": frozenset({"tab"}),
    "key-typed": frozenset({"key"}),
    "window-drag": frozenset(),
}


@dataclass
class Hooks:
    """Effects the bridge may request. The app supplies real ones; tests supply fakes."""

    open_url: object  # (url) -> None, raises OSError
    open_folder: object  # (path) -> None, raises OSError
    start_detached: object  # (argv, cwd, env) -> None, raises OSError
    pick_path: object  # (field, current) -> str | None
    minimize: object  # () -> None
    drag: object  # () -> None
    quit_app: object  # () -> None
    resize_for_scale: object  # (scale) -> None
    hide_window: object = None  # () -> None; used when the user closes during an operation


def _advance(job, phase):
    """Move the job phase forward only. The phase never goes back."""
    if PHASE_ORDER.index(phase) > PHASE_ORDER.index(job["phase"]):
        job["phase"] = phase


def _transfer_detail(telemetry):
    """Rate and received bytes in decimal MB. An unknown value is shown as a dash, never as zero."""
    if not telemetry or not telemetry.get("available"):
        return "— MB/s • — MB received"
    rate, received = telemetry.get("bytesPerSecond"), telemetry.get("receivedBytes")
    rate_text = "— MB/s" if rate is None else f"{rate / 1e6:.1f} MB/s"
    received_text = "— MB received" if received is None else f"{received / 1e6:.1f} MB received"
    return f"{rate_text} • {received_text}"


def _phase_copy(job):
    """Primary and secondary status lines for the update meter."""
    kind, phase = job["kind"], job["phase"]
    if kind == "launch":
        return "LAUNCHING", "Starting Homecoming…"
    if kind == "discover":
        return "CHECKING INSTALLATION", "Checking files…"
    if phase == "checking":
        return ("INSTALLING" if kind == "install" else "CHECKING FOR UPDATES"), "Checking files…"
    if phase == "downloading":
        progress = job["progress"]
        label = "DOWNLOADING" if progress is None else f"DOWNLOADING {min(progress, BUSY_PERCENT_CAP)}%"
        return label, _transfer_detail(job["telemetry"])
    return "INSTALLING" if kind == "install" else "APPLYING UPDATE", "Applying files…"


class Bridge(QObject):
    stateChanged = Signal(str)
    _shards_done = Signal(bool, object)

    def __init__(self, hooks, runner, shard_fetcher=hc.fetch_shard_status,
                 config_dir=cfgmod.DEFAULT_CONFIG_DIR, skins_dir=skinmod.SKINS_DIR, parent=None,
                 feed_fetcher=None, feed_cache=None, pieces=None):
        super().__init__(parent)
        pieces = pieces or piecesmod.load()
        self._hooks = hooks
        self._runner = runner
        self._fetch_shards = shard_fetcher
        self._config_dir = Path(config_dir)
        self._skins_dir = Path(skins_dir)
        self._feed_state = "offline"
        self._articles = feedmod.bundled_articles()
        self._fetched_at = None
        self._feed_cache = Path(feed_cache) if feed_cache else self._config_dir / feedmod.CACHE_NAME
        cached = feedmod.load_cache(self._feed_cache)
        if cached is not None:
            self._articles, self._fetched_at = cached
            self._feed_state = "saved"
        self._feed_inflight = False
        self._feed_fetch = feed_fetcher or self._default_feed_fetcher()
        self._feed_timer = QTimer(self)
        self._feed_timer.timeout.connect(self.refresh_feed)
        self._notice_serial = 0
        start_paths = pieces.find_start_paths(os.environ) if pieces.find_start_paths else {}
        self._config, invalid = cfgmod.load_config(self._config_dir, start_paths)
        self._notice = {"tone": "warn", "text": cfgmod.INVALID_COPY} if invalid else None
        store = pieces.secret_store(self) if pieces.secret_store else None
        self._store = store
        self._watch = WatchController(pieces, store, self._config_dir / "cache" / "voice", self)
        self._watch.changed.connect(self._changed)
        self._watch.failed.connect(self._watch_failed)
        self._watch.moved.connect(self._watch_moved)
        self._voices = VoiceSettings(store, self)
        self._voices.changed.connect(self._changed)
        self._open_settings_at_start = invalid
        self._watch_error = None
        self._settings_open = False
        self._settings_tab = "general"
        self._settings_epoch = 0
        self._show_watch_serial = 0
        self._picked = None
        self._saving = False
        self._settings_error = None
        self._skins = []
        self._skin_errors = []
        self._phase = "discovering"
        self._profiles = {
            pid: {"id": pid, "name": hc.PROFILE_NAMES[pid], "installed": False, "version": None}
            for pid in cfgmod.PROFILE_IDS
        }
        self._job = None
        self._job_seq = 0
        self._confirm = None
        self._error = None
        self._path_problems = []
        self._verify_install = None
        self._shards = [{"name": name, "status": "Unknown"} for name in hc.SHARD_ORDER]
        self._shards_state = "loading"
        self._shards_inflight = False
        self._shard_timer = QTimer(self)
        self._shard_timer.timeout.connect(self._refresh_shards)
        self._shards_done.connect(self._on_shards)
        self._closing = False
        self._runner.output.connect(self._on_output)
        self._runner.timed_out.connect(self._on_timed_out)
        self._runner.finished.connect(self._on_finished)
        if hasattr(runner, "telemetry"):
            runner.telemetry.connect(self._on_telemetry)
        self._reload_skins()
        self._handlers = {
            "play": lambda _p: self._play("live"),
            "beta": lambda _p: self._play("beta"),
            "confirm-install": self._confirm_install,
            "cancel-install": self._cancel_install,
            "retry": lambda _p: self._retry(),
            "home": lambda _p: self._clear_notice(),
            "news": lambda _p: self._open_link("news"),
            "forums": lambda _p: self._open_link("forums"),
            "discord": lambda _p: self._open_link("discord"),
            "support": lambda _p: self._open_link("support"),
            "article": self._open_article,
            "screenshots": lambda _p: self._screenshots(),
            "capture-studio": lambda _p: self._open_capture_studio(),
            "settings-open": lambda _p: self._settings_open_action(),
            "settings-cancel": lambda _p: self._settings_cancel(),
            "settings-save": self._settings_save,
            "settings-tab": self._settings_tab_action,
            "key-typed": self._key_typed,
            "choose-game-root": lambda _p: self._choose_path("gameRoot"),
            "choose-proton": lambda _p: self._choose_path("protonPath"),
            "choose-prefix": lambda _p: self._choose_path("compatPrefix"),
            "open-official": lambda _p: self._open_official(),
            "minimize": lambda _p: self._hooks.minimize(),
            "close": lambda _p: self._close_request(),
            "close-confirm": lambda _p: self._close_confirm(),
            "close-cancel": lambda _p: self._close_cancel(),
            "window-drag": lambda _p: self._hooks.drag(),
        }

    # ----- startup and state -------------------------------------------------

    def start(self):
        self._apply_watch_settings()
        self._watch.set_enabled(self._config["showWatch"])
        if self._open_settings_at_start:
            self._begin_settings()
        self._recheck_paths()
        self._refresh_shards()
        self._shard_timer.start(SHARD_REFRESH_MS)
        self.refresh_feed()
        self._feed_timer.start(feedmod.FEED_INTERVAL_MS)
        self._changed()

    def _default_feed_fetcher(self):
        # Imported here so tests that inject a fetcher never load the Qt web engine.
        from .feedtransport import FeedTransport

        return FeedTransport(self).fetch

    # ----- live news --------------------------------------------------------

    def refresh_feed(self):
        """Start one feed request. A request that is still running is not repeated."""
        if self._feed_inflight:
            return
        self._feed_inflight = True
        self._feed_fetch(self._on_feed_result)

    def _on_feed_result(self, text, error):
        self._feed_inflight = False
        articles = None
        if text is not None:
            try:
                articles = feedmod.normalize_feed(text)
            except feedmod.FeedError as exc:
                error = str(exc)
        if articles:
            self._articles = articles
            self._fetched_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            self._feed_state = "live"
            try:
                feedmod.save_cache(self._feed_cache, articles, self._fetched_at)
            except OSError:
                print("The news cache could not be saved.", file=sys.stderr)
        elif self._feed_state == "live":
            # Keep the last good articles and mark them saved. Their date stays visible.
            self._feed_state = "saved"
        if error and not articles:
            print(f"News feed unavailable: {error}", file=sys.stderr)
        self._changed()

    def _feed_badge(self):
        if self._feed_state == "offline":
            return "OFFLINE NEWS"
        if self._feed_state == "saved":
            return f"SAVED NEWS · {feedmod.format_saved_date(self._fetched_at)}"
        return ""

    def _article(self, article_id):
        for article in self._articles:
            if article["id"] == article_id:
                return article
        return None

    def busy(self):
        return self._job is not None

    def request_close_confirm(self):
        if self._job is not None:
            self._confirm = {"kind": "close", "profile": None}
            self._changed()

    def skin_asset(self, skin_id, key):
        """Return the validated file for one skin asset, or None. Used by the skin URL scheme."""
        for skin in self._skins:
            if skin.id == skin_id:
                return skin.assets.get(key)
        return None

    def snapshot(self):
        applied = self._applied_skin()
        return {
            "mode": "native",
            "phase": self._phase,
            "profile": self._config["lastProfile"],
            "profiles": [dict(self._profiles[pid]) for pid in cfgmod.PROFILE_IDS],
            "job": None if self._job is None else self._job_view(),
            "shards": [dict(row) for row in self._shards],
            "shardsState": self._shards_state,
            "feed": {
                "state": self._feed_state,
                "articles": [dict(a) for a in self._articles],
                "fetchedAt": self._fetched_at,
                "badge": self._feed_badge(),
            },
            "error": self._error,
            "notice": self._notice,
            "confirm": dict(self._confirm) if self._confirm else None,
            "settingsOpen": self._settings_open,
            "settingsTab": self._settings_tab,
            "settingsSaving": self._saving,
            "settingsError": self._settings_error,
            "watch": self._watch_view(),
            "voice": self._voices.view(),
            "settings": self._settings_view(),
            "pathProblems": [dict(row) for row in self._path_problems],
            "skins": [self._skin_view(skin) for skin in self._skins],
            "skinErrors": [dict(row) for row in self._skin_errors],
            "skinId": applied,
            "scale": self._config["scale"],
        }

    def _job_view(self):
        job = self._job
        label, detail = _phase_copy(job)
        progress = job["progress"]
        if progress is not None:
            progress = min(progress, BUSY_PERCENT_CAP)
        if job["uncertain"]:
            message = hc.TIMEOUT_COPY
        elif progress is not None:
            message = f"{progress}%"
        else:
            message = "Working…"
        return {
            "kind": job["kind"],
            "profile": job["profile"],
            "phase": job["phase"],
            "progress": progress,
            "message": message,
            "label": label,
            "detail": detail,
            "uncertain": job["uncertain"],
            "telemetry": dict(job["telemetry"]) if job["telemetry"] else None,
        }

    def _settings_view(self):
        live = self._profiles["live"]
        return {
            "gameRoot": self._config["gameRoot"],
            "protonPath": self._config["protonPath"],
            "compatPrefix": self._config["compatPrefix"],
            "skinId": self._config["skinId"],
            "scale": self._config["scale"],
            "showWatch": self._config["showWatch"],
            "showWatchSerial": self._show_watch_serial,
            "startAutomatically": self._config["startAutomatically"],
            "alertChime": self._config["alertChime"],
            "speakAlerts": self._config["speakAlerts"],
            "voiceId": self._config["voiceId"],
            "picked": self._picked,
            "epoch": self._settings_epoch,
            "lastProfile": self._config["lastProfile"],
            "installed": live["installed"],
            "version": live["version"],
        }

    def _watch_view(self):
        """The watch messages the Game watch section and the toast show."""
        live = self._watch.messages()
        root = self._config["gameRoot"]
        return {
            "error": self._watch_error,
            "gameFolderMessage": messages.NO_GAME_FOLDER
            if self._config["showWatch"] and not (root and Path(root).is_dir()) else None,
            "chatLogMessage": messages.NO_CHAT_LOG if live["chatlog"] else None,
            "chimeError": live["chime"],
            "speakError": live["speak"],
        }

    def _skin_view(self, skin):
        return {
            "id": skin.id,
            "name": skin.name,
            "tokens": dict(skin.tokens),
            "assets": {key: f"homecoming-skin://{skin.id}/{key}" for key in skinmod.ASSET_KEYS},
        }

    def _applied_skin(self):
        ids = [skin.id for skin in self._skins]
        if self._config["skinId"] in ids:
            return self._config["skinId"]
        return ids[0] if ids else ""

    def _changed(self):
        self.stateChanged.emit(json.dumps(self.snapshot()))

    def _clear_notice(self):
        self._notice = None
        self._watch_error = None

    def _reload_skins(self):
        self._skins, errors = skinmod.scan_skins(self._skins_dir)
        self._skin_errors = errors

    def _recheck_paths(self):
        self._path_problems = cfgmod.path_problems(self._config)
        if self._path_problems:
            self._phase = "missing"
            self._begin_settings()
        elif self._job is None:
            self._discover()

    # ----- official CLI jobs -------------------------------------------------

    def _start_job(self, kind, profile, command, timeout_ms, inactivity):
        argv, cwd, env = hc.cli_command(self._config, command, profile)
        self._job_seq += 1
        job_id = str(self._job_seq)
        self._job = {
            "id": job_id, "kind": kind, "profile": profile, "text": "", "progress": None, "uncertain": False,
            "phase": "launching" if kind == "launch" else "checking", "telemetry": None,
        }
        self._phase = "discovering" if kind == "discover" else "busy"
        self._error = None
        self._runner.start(job_id, argv, cwd, env, timeout_ms, inactivity, label=kind)
        self._changed()

    def _discover(self):
        self._start_job("discover", None, "list", hc.TIMEOUT_MS, False)

    def _retry(self):
        if self._job is None:
            self._error = None
            self._notice = None
            self._recheck_paths()

    def _play(self, profile):
        if self._job is not None:
            return
        if self._phase == "error":
            self._retry()
            return
        if self._phase != "ready":
            return
        self._notice = None
        self._watch_error = None
        if not self._profiles[profile]["installed"]:
            self._confirm = {"kind": "install", "profile": profile}
            return
        self._start_job("update", profile, "update", hc.INACTIVITY_MS, True)

    def _confirm_install(self, payload):
        pending = self._confirm
        if self._job is not None or not pending or pending["kind"] != "install":
            return
        if pending["profile"] != payload["profile"]:
            return
        self._confirm = None
        self._start_job("install", payload["profile"], "install", hc.INACTIVITY_MS, True)

    def _cancel_install(self, _payload):
        if self._confirm and self._confirm["kind"] == "install":
            self._confirm = None

    def _on_output(self, job_id, chunk):
        if self._job is not None and self._job["id"] == job_id:
            self._job["text"] = (self._job["text"] + chunk)[-OUTPUT_TAIL_LIMIT:]
            # A percentage counts only once its % sign has arrived, so split chunks cannot give a partial value.
            matches = PERCENT_RE.findall(self._job["text"])
            progress = int(matches[-1]) if matches else None
            phase = self._job["phase"]
            if progress is not None:
                _advance(self._job, "downloading")
            if APPLYING_RE.search(self._job["text"]):
                _advance(self._job, "applying")
            if progress != self._job["progress"] or phase != self._job["phase"]:
                self._job["progress"] = progress
                self._changed()

    def _on_telemetry(self, job_id, data):
        job = self._job
        if job is None or job["id"] != job_id:
            return
        job["telemetry"] = dict(data)
        if data.get("available") and (data.get("receivedBytes") or 0) > 0:
            _advance(job, "downloading")
        self._changed()

    def _on_timed_out(self, job_id):
        """The window passed. The process still runs, so the busy lock stays until it really exits."""
        if self._job is None or self._job["id"] != job_id:
            return
        self._job["uncertain"] = True
        self._notice = {"tone": "warn", "text": hc.TIMEOUT_COPY}
        if self._job["kind"] == "launch":
            self._watch_on_by_play()
        self._changed()

    def _on_finished(self, job_id, result):
        job = self._job
        if job is None or job["id"] != job_id:
            return
        self._job = None
        kind = job["kind"]
        command = "list" if kind == "discover" else kind
        # The real process result decides. An earlier timeout only marks the outcome as uncertain.
        outcome = hc.classify_output(command, result["output"], result["exit_code"], False)
        closing = self._closing
        self._closing = False
        if kind == "discover":
            self._finish_discover(outcome)
        elif kind == "install":
            self._finish_install(job, outcome)
        elif kind == "update":
            self._finish_update(job, outcome, chain=not (closing or job["uncertain"]))
        elif kind == "launch":
            self._finish_launch(outcome, uncertain=job["uncertain"])
        self._changed()
        if closing:
            self._hooks.quit_app()

    def _fail(self, message):
        self._phase = "error"
        self._error = message

    def _finish_discover(self, outcome):
        if not outcome.ok:
            self._fail(outcome.message)
            return
        for pid in cfgmod.PROFILE_IDS:
            row = outcome.data.get(pid)
            self._profiles[pid] = row or {
                "id": pid, "name": hc.PROFILE_NAMES[pid], "installed": False, "updateAvailable": False, "version": None,
            }
        self._phase = "ready"
        if self._verify_install:
            profile = self._verify_install
            self._verify_install = None
            if not self._profiles[profile]["installed"]:
                self._fail("The install did not finish. Open the official launcher to check its status.")

    def _finish_install(self, job, outcome):
        if not outcome.ok:
            self._fail(outcome.message)
            return
        self._notice = {"tone": "ok", "text": "Install succeeded"}
        self._verify_install = job["profile"]
        self._discover()

    def _finish_update(self, job, outcome, chain=True):
        if not outcome.ok:
            self._fail(outcome.message)
            return
        if not chain:
            # Late success after a timeout, or the user closed the window: never launch automatically.
            self._phase = "ready"
            self._notice = {"tone": "info", "text": "Update finished. Press Play to launch."}
            return
        self._start_job("launch", job["profile"], "launch", hc.TIMEOUT_MS, False)

    def _finish_launch(self, outcome, uncertain=False):
        self._phase = "ready"
        if outcome.ok and uncertain:
            # The window passed earlier, so the launch stays uncertain even though it exited cleanly later.
            self._notice = {"tone": "warn", "text": hc.TIMEOUT_COPY}
        elif outcome.ok:
            self._notice = {"tone": "info", "text": "Launch requested"}
            self._watch_on_by_play()
        else:
            self._fail(outcome.message)

    # ----- shard status ------------------------------------------------------

    def _refresh_shards(self):
        if self._shards_inflight:
            return
        self._shards_inflight = True
        threading.Thread(target=self._shard_worker, daemon=True).start()

    def _shard_worker(self):
        try:
            rows = self._fetch_shards()
        except Exception as exc:  # reported in state as Unknown or stale, and logged here
            print(f"Shard status unavailable: {exc}", file=sys.stderr)
            self._shards_done.emit(False, None)
            return
        self._shards_done.emit(True, rows)

    def _on_shards(self, ok, rows):
        self._shards_inflight = False
        if ok:
            self._shards = hc.merge_shards(rows)
            self._shards_state = "fresh"
        elif self._shards_state in ("fresh", "stale"):
            self._shards = [{"name": row["name"], "status": "Unknown"} for row in self._shards]
            self._shards_state = "stale"
        else:
            self._shards = [{"name": name, "status": "Unknown"} for name in hc.SHARD_ORDER]
            self._shards_state = "unavailable"
        self._changed()

    # ----- links, folders and official launcher ------------------------------

    def _open_link(self, key):
        self._request_link(hc.EXTERNAL_URLS[key], hc.LINK_LABELS[key])

    def _open_article(self, payload):
        article = self._article(payload["id"])
        if article is None:
            self._notice = {"tone": "error", "text": "That request was refused."}
            return
        self._request_link(article["url"], article["category"].title())

    def _request_link(self, url, label):
        # The notice says a request was sent. It does not claim the page has loaded.
        if not hc.is_allowed_link(url):
            self._notice = {"tone": "error", "text": "That link is not on the launcher's allowlist."}
            return
        try:
            self._hooks.open_url(url)
        except OSError as exc:
            self._notice = {"tone": "error", "text": str(exc)}
            return
        self._notice_serial += 1
        self._notice = {"tone": "info", "text": f"Requested {label} in Chrome.", "serial": self._notice_serial}

    def _screenshots(self):
        folder = Path(self._config["gameRoot"]) / "screenshots"
        if not folder.is_dir():
            self._notice = {"tone": "error", "text": "No screenshots folder was found in this installation."}
            return
        try:
            self._hooks.open_folder(str(folder))
        except OSError as exc:
            self._notice = {"tone": "error", "text": str(exc)}

    def _open_capture_studio(self):
        path = cfgmod.PROJECT_ROOT.parent / "COH Capture Studio" / "run.py"
        if not path.is_file():
            self._notice = {"tone": "error", "text": "COH Capture Studio is not installed beside this launcher."}
            return
        env = dict(os.environ)
        for key in ("QT_QPA_PLATFORM", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_PLUGIN_PATH"):
            env.pop(key, None)
        try:
            self._hooks.start_detached([sys.executable, str(path)], str(path.parent), env)
        except OSError as exc:
            self._notice = {"tone": "error", "text": str(exc)}

    def _open_official(self):
        if self._path_problems:
            self._notice = {"tone": "error", "text": "Review your installation paths before opening the official launcher."}
            return
        argv, cwd, env = hc.open_official_argv(self._config)
        try:
            self._hooks.start_detached(argv, cwd, env)
        except OSError as exc:
            self._notice = {"tone": "error", "text": str(exc)}

    # ----- the watch ---------------------------------------------------------

    def _apply_watch_settings(self):
        config = self._config
        self._watch.configure(
            config["gameRoot"], config["alertChime"], config["speakAlerts"], config["voiceId"], config["watchSpot"]
        )

    def _set_show_watch(self, on):
        """Show watch changed by the launcher itself: saved at once, and an open Settings switch follows."""
        self._config = dict(self._config, showWatch=on)
        self._show_watch_serial += 1
        try:
            cfgmod.save_config(self._config_dir, self._config)
        except OSError:
            print("The watch setting could not be saved.", file=sys.stderr)

    def _watch_on_by_play(self):
        """Start automatically: Play turns the watch on when the launch was requested or its time limit passed."""
        if not self._config["startAutomatically"] or self._config["showWatch"]:
            return
        self._set_show_watch(True)
        self._watch.set_enabled(True)

    def _watch_failed(self, reason):
        self._watch_error = f"{messages.WATCH_FAILED} {reason}"
        self._set_show_watch(False)
        self._changed()

    def _watch_moved(self, x, y):
        self._config = dict(self._config, watchSpot=[x, y])
        try:
            cfgmod.save_config(self._config_dir, self._config)
        except OSError:
            print("The watch spot could not be saved.", file=sys.stderr)
        self._apply_watch_settings()

    def shutdown(self):
        self._watch.shutdown()

    # ----- settings ----------------------------------------------------------

    def _begin_settings(self, tab="general"):
        self._settings_open = True
        self._settings_tab = tab
        self._settings_epoch += 1
        self._picked = None
        self._settings_error = None
        self._voices.open()

    def _end_settings(self):
        self._settings_open = False
        self._settings_error = None
        self._saving = False
        self._settings_epoch += 1
        self._voices.close()

    def _settings_open_action(self):
        self._reload_skins()
        self._begin_settings()

    def _settings_cancel(self):
        self._end_settings()

    def _settings_tab_action(self, payload):
        self._settings_tab = payload["tab"]

    def _settings_save(self, payload):
        if self._saving:
            return
        if self._job is not None:
            self._notice = {"tone": "error", "text": "Wait for the current operation to finish."}
            return
        try:
            game = cfgmod.check_optional(cfgmod.check_game_root, payload["gameRoot"])
            proton = cfgmod.check_optional(cfgmod.check_proton, payload["protonPath"])
            prefix = cfgmod.check_optional(cfgmod.check_prefix, payload["compatPrefix"])
            skin_id = self._checked_skin(payload["skinId"])
            scale = cfgmod.check_scale(payload["scale"])
        except cfgmod.SettingsError as exc:
            self._settings_error = {"field": exc.field, "message": exc.message}
            return
        # The watch spot is never part of Save: it keeps the spot saved at this moment.
        new = dict(
            self._config, gameRoot=game, protonPath=proton, compatPrefix=prefix, skinId=skin_id, scale=scale,
            showWatch=payload["showWatch"], startAutomatically=payload["startAutomatically"],
            alertChime=payload["alertChime"], speakAlerts=payload["speakAlerts"], voiceId=payload["voiceId"],
        )
        try:
            cfgmod.save_config(self._config_dir, new)
        except OSError:
            self._settings_error = {
                "field": "save",
                "message": "Settings could not be saved. Check that the var folder is writable.",
            }
            return
        old, self._config = self._config, new
        self._show_watch_serial += 1
        self._apply_watch_settings()
        self._apply_show_watch(old["showWatch"], new["showWatch"])
        self._hooks.resize_for_scale(scale)
        self._save_key(payload["key"], old, new)

    def _apply_show_watch(self, was, now):
        if now and not was:
            self._watch_error = None
        self._watch.set_enabled(now)

    def _save_key(self, key, old, new):
        """Keep, store or delete the Fish Audio key, then finish the Save."""
        if key["mode"] == "keep" or self._store is None:
            self._finish_save(old, new, None)
            return
        self._saving = True
        done = lambda _value, error: self._finish_save(old, new, error)  # noqa: E731
        if key["mode"] == "set":
            self._store.write(key["value"], done)
        else:
            self._store.delete(lambda value, error: self._key_deleted(done, value, error))

    def _key_deleted(self, done, value, error):
        if not error:
            self._watch.key_removed()
        done(value, error)

    def _finish_save(self, old, new, key_error):
        self._saving = False
        paths_changed = any(new[key] != old[key] for key in cfgmod.PATH_KEYS)
        if key_error:
            self._voices.save_failed(key_error)
            self._settings_tab = "addons"
        else:
            self._end_settings()
        if paths_changed or self._phase in ("missing", "error"):
            self._recheck_paths()
        self._changed()

    def _checked_skin(self, skin_id):
        if skin_id not in [skin.id for skin in self._skins]:
            raise cfgmod.SettingsError("skinId", "Skin: choose a valid skin from the list.")
        return skin_id

    def _choose_path(self, field):
        if self._job is not None or not self._settings_open:
            return
        picked = self._hooks.pick_path(field, self._picked_value(field))
        if picked:
            serial = (self._picked or {"serial": 0})["serial"] + 1
            self._picked = {"field": field, "value": picked, "serial": serial}
            self._settings_error = None

    def _picked_value(self, field):
        return self._config[field]

    def _key_typed(self, payload):
        self._voices.typed(payload["key"])

    # ----- close flow --------------------------------------------------------

    def _close_request(self):
        if self._job is not None:
            self._confirm = {"kind": "close", "profile": None}
        else:
            self._hooks.quit_app()

    def _close_confirm(self):
        if not self._confirm or self._confirm["kind"] != "close":
            return
        self._confirm = None
        if self._job is None:
            self._hooks.quit_app()
            return
        # The official process keeps running under the runner. Hide the window now, and quit after it ends.
        self._closing = True
        if self._hooks.hide_window is not None:
            self._hooks.hide_window()

    def _close_cancel(self):
        if self._confirm and self._confirm["kind"] == "close":
            self._confirm = None

    # ----- page entry points -------------------------------------------------

    @Slot(result=str)
    def getState(self):
        return json.dumps(self.snapshot())

    @Slot(str, str)
    def dispatch(self, action, payload_json):
        payload = self._parse_payload(action, payload_json)
        if payload is None:
            self._notice = {"tone": "error", "text": "That request was refused."}
            self._changed()
            return
        self._handlers[action](payload)
        self._changed()

    def _parse_payload(self, action, payload_json):
        if action not in ACTIONS or not isinstance(payload_json, str):
            return None
        if len(payload_json) > MAX_PAYLOAD_CHARS:
            return None
        try:
            payload = json.loads(payload_json) if payload_json.strip() else {}
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        allowed = PAYLOAD_KEYS.get(action, frozenset())
        if set(payload) - allowed:
            return None
        if action == "confirm-install" and payload.get("profile") not in cfgmod.PROFILE_IDS:
            return None
        if action == "article" and self._article(payload.get("id")) is None:
            return None
        if action == "settings-save" and not _valid_settings_payload(payload, allowed):
            return None
        if action == "settings-tab" and (set(payload) != allowed or payload["tab"] not in TABS):
            return None
        if action == "key-typed" and (set(payload) != allowed or not _is_key(payload["key"])):
            return None
        return payload


def _is_key(value):
    return isinstance(value, str) and len(value) <= MAX_KEY_CHARS and "\x00" not in value


def _valid_settings_payload(payload, allowed):
    """Every field present and of the right kind. A key is only ever sent with mode set."""
    if set(payload) != allowed:
        return False
    try:
        cfgmod.check_switches_and_voice(payload)
    except cfgmod.ConfigError:
        return False
    key = payload["key"]
    if not isinstance(key, dict) or set(key) != {"mode", "value"} or key["mode"] not in KEY_MODES:
        return False
    return _is_key(key["value"]) and (key["mode"] == "set") == bool(key["value"])
