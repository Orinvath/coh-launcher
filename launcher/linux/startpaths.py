"""Linux piece: the usual Homecoming-on-Proton places in the home folder (Feature 3, step 9)."""

import os
import re
from pathlib import Path

from .. import config as cfgmod

PROTON_DIR = ".local/share/Steam/steamapps/common"
PROTON_NAME_RE = re.compile(r"^Proton (\d+(?:\.\d+)*)")


def _home(environ):
    """HOME of this process and nothing else. No usable HOME means no home folder."""
    value = environ.get("HOME", "")
    if value.startswith("/") and os.path.isdir(value):
        return Path(value)
    return None


def _version_key(name):
    """(version numbers without trailing zeros, full name), or None when the name has no version number."""
    match = PROTON_NAME_RE.match(name)
    if match is None:
        return None
    numbers = [int(part) for part in match.group(1).split(".")]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers), name


def _newest_proton(common):
    ranked = []
    if common.is_dir():
        for folder in common.iterdir():
            key = _version_key(folder.name)
            if key is not None and (folder / "proton").is_file():
                ranked.append((key, folder))
    return str(max(ranked)[1] / "proton") if ranked else ""


def find_start_paths(environ=None):
    """Return the starting game folder, Proton file and prefix. A place that does not exist gives an empty value."""
    home = _home(os.environ if environ is None else environ)
    found = {"gameRoot": "", "protonPath": "", "compatPrefix": ""}
    if home is None:
        return found
    game = home / "Games/Homecoming/pfx/drive_c/Games/Homecoming"
    if cfgmod.is_homecoming_install(game):
        found["gameRoot"] = str(game)
    prefix = home / "Games/Homecoming"
    if (prefix / "pfx/drive_c").is_dir():
        found["compatPrefix"] = str(prefix)
    found["protonPath"] = _newest_proton(home / PROTON_DIR)
    return found
