"""Follows the game's chat log while the game runs (Feature 5). Reads only, and only the new part. The same on every system."""

import datetime as dt
import re
from pathlib import Path

LINE_RE = re.compile(r"^(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d) (.*)$")
LOG_PATTERN = "accounts/*/Logs/chatlog *.txt"
BOM = b"\xef\xbb\xbf"


def parse_line(raw):
    """Return (log time, message) for one log line, or None when the line cannot be used."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    match = LINE_RE.match(text)
    if match is None:
        return None
    try:
        stamp = dt.datetime(*(int(part) for part in match.groups()[:6]))
    except ValueError:
        return None
    return stamp, match.group(7)


class ChatLogFollower:
    def __init__(self, game_root):
        self._root = Path(game_root)
        self._offsets = {path: _size(path) for path in self._files()}
        self._current = None

    def _files(self):
        return [path for path in self._root.glob(LOG_PATTERN) if path.is_file()]

    def poll(self):
        """Return (lines, found): the usable lines written since the last poll, and whether any log exists."""
        files = self._files()
        if not files:
            return [], False
        newest = max(files, key=lambda path: (path.stat().st_mtime_ns, str(path)))
        if newest != self._current:
            self._current = newest
        return self._read_new(newest), True

    def _read_new(self, path):
        offset = self._offsets.get(path, 0)
        try:
            size = path.stat().st_size
            if size < offset:
                offset = 0
            with path.open("rb") as handle:
                handle.seek(offset)
                data = handle.read(size - offset)
        except OSError:
            return []
        end = data.rfind(b"\n") + 1
        self._offsets[path] = offset + end
        complete = data[:end]
        if offset == 0 and complete.startswith(BOM):
            complete = complete[len(BOM):]
        lines = []
        for raw in complete.split(b"\n")[:-1]:
            parsed = parse_line(raw[:-1] if raw.endswith(b"\r") else raw)
            if parsed is not None:
                lines.append(parsed)
        return lines


def _size(path):
    try:
        return path.stat().st_size
    except OSError:
        return 0
