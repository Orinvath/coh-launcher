"""Spoken alerts kept on disk so the same text in the same voice is not asked for twice (Feature 10, steps 9 to 12)."""

import hashlib
import os
import re
from pathlib import Path

MAX_BYTES = 50 * 1024 * 1024
NAME_RE = re.compile(r"^[0-9a-f]{64}\.mp3(\.part)?$")


def file_name(voice_id, text):
    """SHA-256 of the voice id, one zero byte and the text, as 64 hex digits, then .mp3."""
    digest = hashlib.sha256(voice_id.encode() + b"\0" + text.encode()).hexdigest()
    return digest + ".mp3"


class VoiceCache:
    def __init__(self, folder):
        self._folder = Path(folder)

    def path(self, voice_id, text):
        return self._folder / file_name(voice_id, text)

    def get(self, voice_id, text):
        """The saved sound, or None. Reading it counts as playing it, for the trimming order."""
        path = self.path(voice_id, text)
        try:
            data = path.read_bytes()
            os.utime(path)
        except OSError:
            return None
        return data

    def discard(self, voice_id, text):
        self.path(voice_id, text).unlink(missing_ok=True)

    def put(self, voice_id, text, data):
        """Save a sound under a temporary name first. A folder that cannot be written is not an error."""
        path = self.path(voice_id, text)
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            for leftover in self._folder.glob("*.part"):
                if NAME_RE.match(leftover.name):
                    leftover.unlink(missing_ok=True)
            part = path.with_name(path.name + ".part")
            part.write_bytes(data)
            os.replace(part, path)
            self._trim()
        except OSError:
            return

    def _trim(self):
        """Delete the least recently played sounds until the folder is under the limit. Other files stay."""
        sounds = []
        for entry in self._folder.iterdir():
            if NAME_RE.match(entry.name) and entry.is_file():
                info = entry.stat()
                sounds.append((info.st_mtime_ns, entry, info.st_size))
        total = sum(size for _, _, size in sounds)
        for _, entry, size in sorted(sounds, key=lambda row: row[0]):
            if total < MAX_BYTES:
                break
            entry.unlink(missing_ok=True)
            total -= size
