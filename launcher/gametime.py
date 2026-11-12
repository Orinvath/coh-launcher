"""Game time from the PC clock alone (Features 1 and 2). The same on every system."""

from dataclasses import dataclass

GAME_DAY_MS = 1_800_000
DAY_START_HOUR = 6
NIGHT_START_HOUR = 21  # it gets dark at 9 PM in the game (seen by the Owner, 2026-11-11)


@dataclass(frozen=True)
class GameClock:
    hour24: int
    minute: int

    @property
    def is_day(self):
        return DAY_START_HOUR <= self.hour24 < NIGHT_START_HOUR

    @property
    def digits(self):
        return f"{self.hour24 % 12 or 12}:{self.minute:02d}"

    @property
    def ampm(self):
        return "AM" if self.hour24 < 12 else "PM"

    @property
    def text(self):
        return f"{self.digits} {self.ampm}"


def game_clock(utc_seconds):
    """One game day is 1800 real seconds and game midnight falls on every real hour and half hour, UTC."""
    phase_ms = int(utc_seconds * 1000) % GAME_DAY_MS
    game_minutes = phase_ms * 4 // 5000
    return GameClock(game_minutes // 60, game_minutes % 60)
