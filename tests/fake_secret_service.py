#!/usr/bin/env python3
"""Stand-in for the desktop's Secret Service, used only by the tests on a private message bus.

Usage: fake_secret_service.py <store.json> <mode-file>
The store file holds the stored secrets, so a test can read what the launcher kept.
The mode file holds one word, read at every call: ok, locked (needs a prompt that is refused),
unlock-ok (needs a prompt that is accepted) or error.
"""

import json
import sys
from pathlib import Path

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

STORE = Path(sys.argv[1])
MODE = Path(sys.argv[2])
SERVICE = "org.freedesktop.secrets"
ROOT = "/org/freedesktop/secrets"
COLLECTION = ROOT + "/collection/login"
ITEM = COLLECTION + "/1"
PROMPT = ROOT + "/prompt/p1"


def mode():
    return MODE.read_text().strip() if MODE.exists() else "ok"


def stored():
    return json.loads(STORE.read_text()) if STORE.exists() else {}


def keep(items):
    STORE.write_text(json.dumps(items))


def check():
    if mode() == "error":
        raise dbus.exceptions.DBusException("test failure", name="org.freedesktop.DBus.Error.Failed")


def unlocked():
    return mode() in ("ok", "unlocked")


class Prompt(dbus.service.Object):
    def __init__(self, bus):
        super().__init__(bus, PROMPT)

    @dbus.service.method("org.freedesktop.Secret.Prompt", in_signature="s")
    def Prompt(self, window):
        accepted = mode() == "unlock-ok"
        if accepted:
            MODE.write_text("ok")
        GLib.idle_add(lambda: self.Completed(not accepted, dbus.String("")) and False)

    @dbus.service.signal("org.freedesktop.Secret.Prompt", signature="bv")
    def Completed(self, dismissed, result):
        return None


class Item(dbus.service.Object):
    @dbus.service.method("org.freedesktop.Secret.Item", in_signature="o", out_signature="(oayays)")
    def GetSecret(self, session):
        check()
        value = stored().get("value", "")
        return (dbus.ObjectPath(session), dbus.ByteArray(b""), dbus.ByteArray(value.encode()), "text/plain")

    @dbus.service.method("org.freedesktop.Secret.Item", in_signature="(oayays)")
    def SetSecret(self, secret):
        check()
        keep({"value": bytes(secret[2]).decode()})

    @dbus.service.method("org.freedesktop.Secret.Item", out_signature="o")
    def Delete(self):
        check()
        keep({})
        return dbus.ObjectPath("/")


class Collection(dbus.service.Object):
    @dbus.service.method("org.freedesktop.Secret.Collection", in_signature="a{sv}(oayays)b", out_signature="oo")
    def CreateItem(self, properties, secret, replace):
        check()
        if not unlocked():
            return dbus.ObjectPath("/"), dbus.ObjectPath(PROMPT)
        keep({"value": bytes(secret[2]).decode()})
        return dbus.ObjectPath(ITEM), dbus.ObjectPath("/")


class Service(dbus.service.Object):
    @dbus.service.method("org.freedesktop.Secret.Service", in_signature="sv", out_signature="vo")
    def OpenSession(self, algorithm, value):
        check()
        return dbus.String(""), dbus.ObjectPath(ROOT + "/session/s1")

    @dbus.service.method("org.freedesktop.Secret.Service", in_signature="a{ss}", out_signature="aoao")
    def SearchItems(self, attributes):
        check()
        if "value" not in stored():
            return dbus.Array([], signature="o"), dbus.Array([], signature="o")
        found = dbus.Array([dbus.ObjectPath(ITEM)], signature="o")
        empty = dbus.Array([], signature="o")
        return (found, empty) if unlocked() else (empty, found)

    @dbus.service.method("org.freedesktop.Secret.Service", in_signature="ao", out_signature="aoo")
    def Unlock(self, objects):
        check()
        if unlocked():
            return dbus.Array(objects, signature="o"), dbus.ObjectPath("/")
        return dbus.Array([], signature="o"), dbus.ObjectPath(PROMPT)

    @dbus.service.method("org.freedesktop.Secret.Service", in_signature="s", out_signature="o")
    def ReadAlias(self, name):
        check()
        return dbus.ObjectPath(COLLECTION)


def main():
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    name = dbus.service.BusName(SERVICE, bus)
    Service(bus, ROOT)
    Collection(bus, COLLECTION)
    Item(bus, ITEM)
    Prompt(bus)
    print("READY", flush=True)
    GLib.MainLoop().run()
    return name


if __name__ == "__main__":
    main()
