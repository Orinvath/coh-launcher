"""Which system-specific pieces this system has. A piece that does not exist yet is None.

The rest of the launcher asks here and never imports a piece itself, so another system can be added beside Linux.
"""

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Pieces:
    find_start_paths: object = None  # (environ) -> {"gameRoot", "protonPath", "compatPrefix"}
    find_game: object = None  # (game folder) -> [process ids]
    watch_helper: object = None  # module with check() and WatchHelper
    secret_store: object = None  # class SecretStore
    sound_player: object = None  # class SoundPlayer


def load():
    if sys.platform.startswith("linux"):
        from .linux import gameprocess, helper, secrets, sound, startpaths

        return Pieces(startpaths.find_start_paths, gameprocess.find_game, helper, secrets.SecretStore, sound.SoundPlayer)
    return Pieces()
