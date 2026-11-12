"""Linux piece: the place the Fish Audio key is kept, the desktop's password store.

It speaks to the standard Secret Service (org.freedesktop.secrets), which KDE Wallet and GNOME Keyring
both offer. The key goes to the store and nowhere else. Every request runs on its own short thread and
the answer comes back to the Qt thread through a callback; nothing is kept between requests.

Answers are (value, error). `error` is one of NO_STORE, LOCKED, FAILED, or None.
"""

import struct
import threading

from PySide6.QtCore import QObject, Signal

from .. import testenv
from .dbuswire import Bus, DBusError

NO_STORE, LOCKED, FAILED = "no-store", "locked", "failed"
SERVICE = "org.freedesktop.secrets"
ROOT = "/org/freedesktop/secrets"
ATTRIBUTES = {"application": "homecoming-launcher", "kind": "fish-audio-key"}
LABEL = "Homecoming Launcher: Fish Audio key"
PROMPT_WAIT = 120.0
_NO_SERVICE = {"no-bus", "io", "org.freedesktop.DBus.Error.ServiceUnknown", "org.freedesktop.DBus.Error.NameHasNoOwner"}
_REFUSED = {"org.freedesktop.Secret.Error.IsLocked", "org.freedesktop.DBus.Error.AccessDenied"}


class _Locked(Exception):
    """The store is locked and the user did not unlock it."""


def _error_code(exc):
    if isinstance(exc, _Locked) or getattr(exc, "name", None) in _REFUSED:
        return LOCKED
    if getattr(exc, "name", None) in _NO_SERVICE:
        return NO_STORE
    return FAILED


def _prompt(bus, path):
    """Run a prompt the store asked for. Raises _Locked when the user dismissed it."""
    if path == "/":
        return
    bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "AddMatch", "s",
             (f"type='signal',interface='org.freedesktop.Secret.Prompt',path='{path}'",))
    bus.call(SERVICE, path, "org.freedesktop.Secret.Prompt", "Prompt", "s", ("",))
    done = bus.wait_signal(path, "org.freedesktop.Secret.Prompt", "Completed", PROMPT_WAIT)
    if done is None or done[0]:
        raise _Locked()


def _find(bus):
    """Open a session and return (session path, item path or None). Unlocks the item when needed."""
    _, session = bus.call(SERVICE, ROOT, "org.freedesktop.Secret.Service", "OpenSession", "sv", ("plain", ("s", "")))
    for _attempt in range(2):
        unlocked, locked = bus.call(SERVICE, ROOT, "org.freedesktop.Secret.Service", "SearchItems", "a{ss}", (ATTRIBUTES,))
        if unlocked:
            return session, unlocked[0]
        if not locked:
            return session, None
        _, prompt = bus.call(SERVICE, ROOT, "org.freedesktop.Secret.Service", "Unlock", "ao", (locked,))
        _prompt(bus, prompt)
    raise _Locked()


def _secret(session, key):
    return (session, b"", key.encode(), "text/plain")


def _read(bus):
    session, item = _find(bus)
    if item is None:
        return ""
    answer = bus.call(SERVICE, item, "org.freedesktop.Secret.Item", "GetSecret", "o", (session,))[0]
    return answer[2].decode()


def _write(bus, key):
    session, item = _find(bus)
    if item is not None:
        bus.call(SERVICE, item, "org.freedesktop.Secret.Item", "SetSecret", "(oayays)", (_secret(session, key),))
        return
    collection = bus.call(SERVICE, ROOT, "org.freedesktop.Secret.Service", "ReadAlias", "s", ("default",))[0]
    if collection == "/":
        raise DBusError("no-collection", "The store has no default collection.")
    properties = {
        "org.freedesktop.Secret.Item.Label": ("s", LABEL),
        "org.freedesktop.Secret.Item.Attributes": ("a{ss}", ATTRIBUTES),
    }
    for _attempt in range(2):
        _, prompt = bus.call(SERVICE, collection, "org.freedesktop.Secret.Collection", "CreateItem",
                             "a{sv}(oayays)b", (properties, _secret(session, key), True))
        if prompt == "/":
            return
        _prompt(bus, prompt)
        session, item = _find(bus)
        if item is not None:
            return
    raise _Locked()


def _delete(bus):
    _, item = _find(bus)
    if item is not None:
        _prompt(bus, bus.call(SERVICE, item, "org.freedesktop.Secret.Item", "Delete")[0])


class SecretStore(QObject):
    _answer = Signal(object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._callbacks = {}
        self._next = 0
        self._answer.connect(self._deliver)

    def read(self, callback):
        """callback(key, error). `key` is "" when none is stored."""
        self._start(_read, callback)

    def write(self, key, callback):
        """callback(None, error)."""
        self._start(lambda bus: _write(bus, key), callback)

    def delete(self, callback):
        """callback(None, error)."""
        self._start(_delete, callback)

    def _start(self, operation, callback):
        self._next += 1
        token = self._next
        self._callbacks[token] = callback
        threading.Thread(target=self._run, args=(token, operation), daemon=True).start()

    def _run(self, token, operation):
        bus = None
        try:
            bus = Bus(testenv.secrets_bus_address())
            self._answer.emit(token, operation(bus), None)
        except (DBusError, _Locked) as exc:
            self._answer.emit(token, None, _error_code(exc))
        except (OSError, ValueError, IndexError, TypeError, struct.error):
            self._answer.emit(token, None, FAILED)
        finally:
            if bus is not None:
                bus.close()

    def _deliver(self, token, value, error):
        callback = self._callbacks.pop(token)
        callback(value, error)
