"""Official Homecoming integration: CLI argv, output rules, shard status and links.

Everything that talks to the official launcher lives here. Commands and
profiles come from fixed allowlists, and argv is built as a list so no shell
ever parses it. Exit code 0 alone is never treated as success.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PROFILE_NAMES = {"live": "Homecoming", "beta": "Open Beta"}
CLI_COMMANDS = ("list", "install", "update", "launch")
TIMEOUT_MS = 90_000
INACTIVITY_MS = 600_000
TIMEOUT_COPY = "The official launcher did not finish. Open the official launcher to check its status."
NEWS_URL = "https://forums.homecomingservers.com/forum/11-announcements/"
FORUMS_URL = "https://forums.homecomingservers.com/"
SUPPORT_URL = "https://forums.homecomingservers.com/forum/12-help-amp-support/"
DISCORD_URL = "https://discord.com/invite/cityofheroes"
EXTERNAL_URLS = {"news": NEWS_URL, "forums": FORUMS_URL, "support": SUPPORT_URL, "discord": DISCORD_URL}
LINK_LABELS = {"news": "News", "forums": "Forums", "support": "Support", "discord": "Discord"}
STATUS_URL = "https://api.homecomingservers.com/shardstatus/live"
MAX_STATUS_BYTES = 1024 * 1024
SHARD_ORDER = ("Torchbearer", "Excelsior", "Indomitable", "Everlasting", "Victory", "Reunion")
STATUS_LABELS = {
    "low": "Online",
    "medium": "Online",
    "high": "Online",
    "offline": "Offline",
    "starting": "Starting",
    "maintenance": "Maintenance",
}
FAILURE_MARKERS = ("failed", "unknown profile", "not installed", "not in a runnable state")
REDACT_RE = re.compile(r"(?i)\b(token|password|authorization)\b(\s*[:=]\s*)\S+")
OFFICIAL_HOSTS = frozenset({"forums.homecomingservers.com", "homecomingservers.com"})
MAX_URL_CHARS = 2048


def valid_official_url(url):
    """True only for https links on the exact official hosts, with no userinfo, port or control characters."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_CHARS:
        return False
    if any(ord(char) < 0x20 or ord(char) == 0x7F or char.isspace() for char in url):
        return False
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and parts.netloc in OFFICIAL_HOSTS


def is_allowed_link(url):
    """The one URL rule for Chrome: fixed launcher links, or a validated official article link."""
    return url in EXTERNAL_URLS.values() or valid_official_url(url)


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str | None
    status: str  # success | launch_requested | failed | timeout
    data: dict | None = None  # parsed profiles for list


PROTON_STRIPPED_VARS = ("QT_QPA_PLATFORM", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_PLUGIN_PATH")


def proton_environment(config, inherited=None):
    """Full environment for Proton and official child processes.

    The three Linux Qt variables are removed: an inherited Qt platform breaks the
    Windows official launcher. The two Steam compatibility variables are set.
    The native UI environment is not changed, and os.environ is not mutated.
    """
    env = dict(os.environ if inherited is None else inherited)
    for key in PROTON_STRIPPED_VARS:
        env.pop(key, None)
    env["STEAM_COMPAT_DATA_PATH"] = config["compatPrefix"]
    env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = config["compatPrefix"]
    return env


def cli_command(config, command, profile=None):
    """Return (argv, cwd, env) for one allowlisted official CLI command."""
    if command not in CLI_COMMANDS:
        raise ValueError("Unknown official command.")
    if command == "list":
        if profile is not None:
            raise ValueError("list takes no profile.")
    elif profile not in PROFILE_NAMES:
        raise ValueError("Unknown profile.")
    game = Path(config["gameRoot"])
    # Direct official executable in text mode: the short-lived CLI wrapper lost its output (eval-2).
    args = [config["protonPath"], "runinprefix", str(game / "bin/win64/launcher.exe"), "-text", command]
    if profile is not None:
        args.append(profile)
    return [sys.executable, *args], str(game / "bin/win64"), proton_environment(config)


def open_official_argv(config):
    """Return (argv, cwd, env) that opens the official launcher window with no extra arguments."""
    game = Path(config["gameRoot"])
    argv = [sys.executable, config["protonPath"], "runinprefix", str(game / "bin/win64/launcher.exe")]
    return argv, str(game / "bin/win64"), proton_environment(config)


def redact(text):
    return REDACT_RE.sub(r"\1\2[redacted]", text or "")


PROFILE_STATES = {
    # state word -> (installed, updateAvailable), as printed by the official list command
    "installed": (True, False),
    "needsupdate": (True, True),
    "notinstalled": (False, False),
}


def parse_profile_list(text):
    """Read rows of the official table: ID, Name, Version, State.

    An unknown state for an allowlisted profile is kept with installed=None so the
    caller reports it. It is never treated as an absent installation.
    """
    profiles = {}
    for line in (text or "").splitlines():
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 3 or parts[0] not in PROFILE_NAMES:
            continue
        state = parts[-1]
        version = parts[2] if len(parts) == 4 else ""
        installed, update_available = PROFILE_STATES.get(state.lower(), (None, False))
        profiles[parts[0]] = {
            "id": parts[0],
            "name": PROFILE_NAMES[parts[0]],
            "installed": installed,
            "updateAvailable": update_available,
            "version": version or None,
            "state": state,
        }
    return profiles


def _first_failure_line(text):
    for line in text.splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in FAILURE_MARKERS):
            return line.strip()
    return None


def _clip(text, limit=200):
    text = redact(text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def classify_output(kind, text, exit_code, timed_out=False):
    """Decide what one finished official CLI run means. Exit 0 alone is not success."""
    text = text or ""
    if timed_out:
        return Outcome(False, TIMEOUT_COPY, "timeout")
    if kind == "list":
        if exit_code != 0:
            return Outcome(False, f"The official launcher stopped with exit code {exit_code}.", "failed")
        profiles = parse_profile_list(text)
        if not profiles:
            return Outcome(False, "The official launcher returned no profile list.", "failed")
        for row in profiles.values():
            if row["installed"] is None:
                return Outcome(False, _clip(f"Unknown state for {row['id']}: {row['state']}"), "failed")
        return Outcome(True, None, "success", profiles)
    failure = _first_failure_line(text)
    if failure:
        return Outcome(False, _clip(failure), "failed")
    if exit_code != 0:
        return Outcome(False, f"The official launcher stopped with exit code {exit_code}.", "failed")
    if kind == "launch":
        return Outcome(True, None, "launch_requested")
    if not text.strip():
        return Outcome(False, "The official launcher returned no output.", "failed")
    lowered = text.lower()
    if kind == "update" and ("is up to date" in lowered or "update succeeded" in lowered):
        return Outcome(True, None, "success")
    if kind == "install" and ("install succeeded" in lowered or "is already installed" in lowered):
        return Outcome(True, None, "success")
    return Outcome(False, "The official launcher did not confirm success.", "failed")


class ShardError(Exception):
    """The shard status response was missing, too large or malformed."""


def map_status(raw):
    return STATUS_LABELS.get(raw.strip().lower(), "Unknown")


def fetch_shard_status(url=STATUS_URL, timeout=10, max_bytes=MAX_STATUS_BYTES):
    """Fetch public shard status over HTTPS and return [{name, status}]."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if not response.geturl().startswith("https://api.homecomingservers.com/"):
            raise ShardError("The status address redirected to an unexpected site.")
        body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ShardError("The status response was too large.")
    try:
        data = json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise ShardError("The status response was not valid JSON.") from exc
    if not isinstance(data, list):
        raise ShardError("The status response was not a list.")
    rows = []
    for item in data[:100]:
        if not isinstance(item, dict):
            continue
        name, status = item.get("name"), item.get("status")
        if isinstance(name, str) and isinstance(status, str) and 0 < len(name.strip()) <= 64:
            rows.append({"name": name.strip(), "status": map_status(status)})
    return rows


def merge_shards(rows):
    """Keep reference order for known shards, append newly discovered ones."""
    by_name = {}
    for row in rows:
        by_name.setdefault(row["name"].casefold(), row)
    merged, used = [], set()
    for name in SHARD_ORDER:
        row = by_name.get(name.casefold())
        merged.append({"name": name, "status": row["status"] if row else "Unknown"})
        used.add(name.casefold())
    for row in rows:
        if row["name"].casefold() not in used:
            merged.append(dict(row))
            used.add(row["name"].casefold())
    return merged
