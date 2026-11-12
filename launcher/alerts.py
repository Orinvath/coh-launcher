"""City alerts: which chat lines count, repeats, and how long a card stays (Feature 6). The same on every system."""

import datetime as dt
import re

TAG_RE = re.compile(r"^\[([^\]]+)\] ")
ALLOWED_TAG = "Vanguard"
ALERT_PREFIXES = ("WARNING:", "DANGER!:", "ALERT CANCELED:", "ALERT CANCELLED:")
APPEARED_RE = re.compile(r"^.+ has appeared in .+\.$")
REPEAT_WINDOW = dt.timedelta(seconds=5)
HOLD_SECONDS = 2.0
SCROLL_PX_PER_SECOND = 15.0
END_READ_SECONDS = 3.0
MIN_CARD_SECONDS = 10.0


def alert_text(message):
    """The text of a city alert, or None when the message is not one. Chat channels never count."""
    tag = TAG_RE.match(message)
    if tag is not None:
        if tag.group(1) != ALLOWED_TAG:
            return None
        message = message[tag.end():]
    if message.startswith(ALERT_PREFIXES) or APPEARED_RE.match(message):
        return message
    return None


class RepeatFilter:
    """Drops a copy of an alert that comes at most 5 seconds after the copy that last made a card."""

    def __init__(self):
        self._shown = {}

    def is_new(self, text, log_time):
        last = self._shown.get(text)
        if last is not None and dt.timedelta(0) <= log_time - last <= REPEAT_WINDOW:
            return False
        self._shown[text] = log_time
        return True

    def forget_old(self, newest_log_time):
        """Forget texts that can no longer make a repeat."""
        self._shown = {
            text: shown for text, shown in self._shown.items() if newest_log_time - shown <= REPEAT_WINDOW
        }


def scroll_offset(age, hidden_px):
    """How far the text has moved up after `age` seconds: still for 2 seconds, then 15 px per second, then stopped."""
    if age <= HOLD_SECONDS:
        return 0.0
    return min(float(hidden_px), (age - HOLD_SECONDS) * SCROLL_PX_PER_SECOND)


def card_seconds(hidden_px):
    """How long a card stays: 10 seconds, or longer for text that scrolls."""
    if hidden_px <= 0:
        return MIN_CARD_SECONDS
    return max(MIN_CARD_SECONDS, HOLD_SECONDS + hidden_px / SCROLL_PX_PER_SECOND + END_READ_SECONDS)
