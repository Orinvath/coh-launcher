"""Launcher settings: strict validation and atomic, project-local storage.

The settings file lives in var/config.json inside the project folder and is
written with mode 600. Schema version 2 is the current one; version 1 is still read. Path problems are kept
apart from file corruption: a broken file is moved aside, while a path that
no longer exists only sends the user to the settings panel.
"""

import json
import os
import re
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "var"
CONFIG_NAME = "config.json"
VERSION = 2
SCALES = (0.75, 1, 1.25, 1.5)
PROFILE_IDS = ("live", "beta")
PATH_KEYS = ("gameRoot", "protonPath", "compatPrefix")
SKIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_PATH_LEN = 4096
MAX_VOICE_ID_LEN = 200
INVALID_COPY = "Saved settings could not be read. Review your installation paths."

# The settings every version of the file holds, then the ones version 2 added.
GENERAL_DEFAULTS = {"skinId": "homecoming", "scale": 1, "lastProfile": "live"}
WATCH_DEFAULTS = {
    "showWatch": True,
    "startAutomatically": True,
    "watchSpot": None,
    "alertChime": True,
    "speakAlerts": False,
    "voiceId": None,
}
WATCH_FLAGS = ("showWatch", "startAutomatically", "alertChime", "speakAlerts")
V1_KEYS = frozenset({"version", *PATH_KEYS, *GENERAL_DEFAULTS})
KEYS = frozenset({"version", *PATH_KEYS, *GENERAL_DEFAULTS, *WATCH_DEFAULTS})
SAVED_KEYS = ("version", *PATH_KEYS, *GENERAL_DEFAULTS, *WATCH_DEFAULTS)

FIELD_LABELS = {
    "gameRoot": "Game installation",
    "protonPath": "Proton executable",
    "compatPrefix": "Compatibility prefix",
    "skinId": "Skin",
    "scale": "Window scale",
}


def default_config(start_paths):
    """A fresh settings set. The three paths come from the platform's starting-values piece."""
    config = {"version": VERSION}
    config.update({key: start_paths.get(key, "") for key in PATH_KEYS})
    config.update(GENERAL_DEFAULTS)
    config.update(WATCH_DEFAULTS)
    return config


class ConfigError(Exception):
    """The settings file is unreadable or has the wrong shape."""


class SettingsError(Exception):
    """One settings field failed validation. `field` names the failing field."""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field
        self.message = message


def _absolute(field, value):
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > MAX_PATH_LEN:
        raise SettingsError(field, f"{FIELD_LABELS[field]}: enter a full path.")
    path = Path(os.path.normpath(value))
    if not path.is_absolute():
        raise SettingsError(field, f"{FIELD_LABELS[field]}: the path must start with /.")
    return path


def is_homecoming_install(path):
    path = Path(path)
    return (path / "bin/win64/launchercli.exe").is_file() and (path / "settings/launcher/profiles").is_dir()


def check_optional(check, value):
    """Save accepts an empty path box as "not set"; any other value must pass its own check."""
    return "" if value == "" else check(value)


def check_game_root(value):
    path = _absolute("gameRoot", value)
    if not is_homecoming_install(path):
        raise SettingsError(
            "gameRoot",
            "Game installation: this folder has no Homecoming install. "
            "It must contain bin/win64/launchercli.exe.",
        )
    return str(path)


def check_proton(value):
    path = _absolute("protonPath", value)
    if not path.is_file():
        raise SettingsError("protonPath", "Proton executable: choose the proton file from your Steam Proton folder.")
    return str(path)


def check_prefix(value):
    path = _absolute("compatPrefix", value)
    if not path.is_dir() or not (path / "pfx/drive_c").is_dir():
        raise SettingsError(
            "compatPrefix",
            "Compatibility prefix: this folder must contain pfx/drive_c.",
        )
    return str(path)


def check_scale(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.75 <= value <= 1.5:
        raise SettingsError("scale", "Window scale: choose 0.75, 1, 1.25 or 1.5.")
    return value


def path_problems(config):
    """Return settings errors for paths that no longer pass validation."""
    problems = []
    checks = (("gameRoot", check_game_root), ("protonPath", check_proton), ("compatPrefix", check_prefix))
    for field, check in checks:
        try:
            check(config[field])
        except SettingsError as exc:
            problems.append({"field": exc.field, "message": exc.message})
    return problems


def check_switches_and_voice(values):
    """The one rule for the four watch switches and the voice id, for the file and for Save. Raises ConfigError."""
    for key in WATCH_FLAGS:
        if type(values[key]) is not bool:
            raise ConfigError(key)
    voice = values["voiceId"]
    if voice is not None and not (isinstance(voice, str) and 0 < len(voice) <= MAX_VOICE_ID_LEN):
        raise ConfigError("voiceId")


def _check_watch(raw):
    check_switches_and_voice(raw)
    spot = raw["watchSpot"]
    if spot is not None and not (
        isinstance(spot, list) and len(spot) == 2 and all(type(n) is int for n in spot)
    ):
        raise ConfigError("watchSpot")


def _schema(raw):
    """Check a loaded file. Version 1 is accepted as it is and gets the starting watch settings."""
    if not isinstance(raw, dict) or type(raw.get("version")) is not int:
        raise ConfigError("unknown version")
    if raw["version"] == 1:
        if set(raw) != V1_KEYS:
            raise ConfigError("unexpected keys")
        raw = {**raw, **WATCH_DEFAULTS, "version": VERSION}
        allow_empty = False
    elif raw["version"] == VERSION:
        if set(raw) != KEYS:
            raise ConfigError("unexpected keys")
        _check_watch(raw)
        allow_empty = True
    else:
        raise ConfigError("unknown version")
    for key in PATH_KEYS:
        value = raw[key]
        if not isinstance(value, str) or "\x00" in value or len(value) > MAX_PATH_LEN:
            raise ConfigError(f"{key} must be a path")
        if not (allow_empty and value == "") and not os.path.isabs(value):
            raise ConfigError(f"{key} must be an absolute path")
    if not isinstance(raw["skinId"], str) or not SKIN_ID_RE.match(raw["skinId"]):
        raise ConfigError("skinId")
    if isinstance(raw["scale"], bool) or not isinstance(raw["scale"], (int, float)):
        raise ConfigError("scale")
    if not 0.75 <= raw["scale"] <= 1.5:
        raise ConfigError("scale range")
    if raw["lastProfile"] not in PROFILE_IDS:
        raise ConfigError("lastProfile")
    return {key: raw[key] for key in SAVED_KEYS}


def load_config(config_dir, start_paths):
    """Return (config, invalid). A corrupt file is preserved, then starting values are used."""
    directory = Path(config_dir)
    path = directory / CONFIG_NAME
    if not path.exists():
        return default_config(start_paths), False
    try:
        config = _schema(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ConfigError):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        os.replace(path, directory / f"config.invalid-{stamp}.json")
        return default_config(start_paths), True
    return config, False


def save_config(config_dir, config):
    """Write the settings atomically with mode 600."""
    directory = Path(config_dir)
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / CONFIG_NAME
    temp = directory / f".{CONFIG_NAME}.{os.getpid()}.tmp"
    data = json.dumps({key: config[key] for key in SAVED_KEYS}, indent=2) + "\n"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, final)
    except BaseException:
        if temp.exists():
            temp.unlink()
        raise
