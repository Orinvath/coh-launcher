"""The only place that reads the COH_TEST_* variables.

They let the tests swap one outside thing for a stand-in. When a variable is not set,
nothing here has any effect. No setting, settings file or document of the launcher mentions them.
"""

import datetime as dt
import os
import time

_PREFIX = "COH_TEST_"
DEFAULT_HELPER_MINUTE = 60.0


def _value(name):
    return os.environ.get(_PREFIX + name) or None


_clock = (None, 0.0)


def _clock_offset():
    """Seconds to add to the PC clock so the launcher sees COH_TEST_NOW (a UTC time) when it first looks, and then keeps counting."""
    global _clock
    text = _value("NOW")
    if text is None:
        return 0.0
    if _clock[0] != text:
        target = dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc).timestamp()
        _clock = (text, target - time.time())
    return _clock[1]


def utc_now():
    """Seconds since 1970 in UTC."""
    return time.time() + _clock_offset()


def secrets_bus_address():
    """Address of a private message bus that holds a stand-in password store, or None for the desktop's own bus."""
    return _value("SECRETS_BUS")


def fish_base_url(default):
    return _value("FISH_URL") or default


def sound_log_path():
    """File where a recording stand-in sound player writes its requests, or None for the real sound player."""
    return _value("SOUND_LOG")


def helper_minute():
    """Length in seconds of the 'minute' in the watch helper restart rule."""
    text = _value("HELPER_MINUTE")
    return float(text) if text else DEFAULT_HELPER_MINUTE
