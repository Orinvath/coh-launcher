"""Linux piece: finds the running game program (Feature 4, step 5).

Looks at every process of this user, reading its program link and the first word of its command line
from the system's process list.
"""

import os
from pathlib import Path

PROFILES = ("live", "beta")
PROGRAM = "cityofheroes.exe"


def _windows_forms(game_root):
    """The C: path (when the folder lies in a Wine prefix) and the Z: path of a program, as lowercase text with /."""
    forms = []
    parts = Path(game_root).parts
    if "drive_c" in parts:
        rest = parts[parts.index("drive_c") + 1:]
        forms.append(("c:/" + "/".join(rest)).lower().rstrip("/"))
    forms.append(("z:" + str(Path(game_root))).lower().rstrip("/"))
    return forms


def program_names(game_root):
    """All the ways the game program may be named for this game folder: (linux paths, windows paths)."""
    linux, windows = set(), set()
    windows_roots = _windows_forms(game_root)
    for profile in PROFILES:
        relative = f"bin/win64/{profile}/{PROGRAM}"
        linux.add(os.path.join(os.path.normpath(game_root), relative))
        windows.update(f"{root}/{relative}" for root in windows_roots)
    return linux, windows


def _first_word(pid_dir):
    try:
        return (pid_dir / "cmdline").read_bytes().split(b"\0")[0].decode("utf-8", "replace")
    except OSError:
        return ""


def _program_link(pid_dir):
    try:
        return os.readlink(pid_dir / "exe")
    except OSError:
        return ""


def _works_in(pid_dir, game_root):
    """True when the process's working folder is the game folder or inside it."""
    try:
        cwd = os.path.realpath(os.readlink(pid_dir / "cwd"))
    except OSError:
        return False
    root = os.path.realpath(game_root)
    return cwd == root or cwd.startswith(root + os.sep)


def find_game(game_root, proc="/proc"):
    """Process ids of the running game program for this game folder.

    The game may be named by its full path, or (as the official launcher starts it under Wine) by its bare
    name only; a bare name counts when the process works inside the game folder.
    """
    linux, windows = program_names(game_root)
    linux = {os.path.realpath(path) for path in linux}
    found = []
    for pid_dir in Path(proc).iterdir():
        if not pid_dir.name.isdigit():
            continue
        for detail in (_program_link(pid_dir), _first_word(pid_dir)):
            is_linux_path = detail.startswith("/") and os.path.realpath(detail) in linux
            named = detail.replace("\\", "/").lower()
            bare = named == PROGRAM and _works_in(pid_dir, game_root)
            if is_linux_path or named in windows or bare:
                found.append(int(pid_dir.name))
                break
    return found
