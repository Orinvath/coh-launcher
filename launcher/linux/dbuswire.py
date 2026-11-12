"""A small blocking D-Bus client over the message bus socket, for the Linux password store piece.

Qt's D-Bus classes in PySide6 can send the structures the Secret Service wants but cannot read the
structure it answers with, so this module speaks the bus protocol itself. It needs nothing installed.
Only what the password store piece uses is supported: strings, object paths, signatures, booleans,
bytes, arrays, dictionaries, structures and variants (little-endian, as every Linux desktop sends).
"""

import os
import socket
import struct
import time
import urllib.parse

METHOD_CALL, METHOD_RETURN, ERROR, SIGNAL = 1, 2, 3, 4
_FIELD_PATH, _FIELD_INTERFACE, _FIELD_MEMBER, _FIELD_ERROR, _FIELD_REPLY, _FIELD_DEST, _FIELD_SENDER, _FIELD_SIG = (
    1, 2, 3, 4, 5, 6, 7, 8,
)
_ALIGN = {"y": 1, "b": 4, "u": 4, "s": 4, "o": 4, "g": 1, "a": 4, "(": 8, "{": 8, "v": 1}
BUS_NAME = "org.freedesktop.DBus"


class DBusError(Exception):
    """The bus cannot be reached, or a call was answered with an error. `name` is the D-Bus error name."""

    def __init__(self, name, message=""):
        super().__init__(f"{name}: {message}")
        self.name = name


def _type_end(sig, i):
    """Index just after the single complete type that starts at sig[i]."""
    char = sig[i]
    if char == "a":
        return _type_end(sig, i + 1)
    if char in "({":
        close = ")" if char == "(" else "}"
        i += 1
        while sig[i] != close:
            i = _type_end(sig, i)
        return i + 1
    return i + 1


def _split(sig):
    parts, i = [], 0
    while i < len(sig):
        end = _type_end(sig, i)
        parts.append(sig[i:end])
        i = end
    return parts


def _pad(buf, n):
    buf.extend(b"\0" * (-len(buf) % n))


def _put(buf, sig, value):
    char = sig[0]
    _pad(buf, _ALIGN[char])
    if char in "so":
        data = value.encode()
        buf += struct.pack("<I", len(data)) + data + b"\0"
    elif char == "g":
        data = value.encode()
        buf += bytes([len(data)]) + data + b"\0"
    elif char == "b":
        buf += struct.pack("<I", 1 if value else 0)
    elif char == "u":
        buf += struct.pack("<I", value)
    elif char == "y":
        buf.append(value)
    elif char == "v":
        inner, content = value
        _put(buf, "g", inner)
        _put(buf, inner, content)
    elif char == "a":
        element = sig[1:]
        length_at = len(buf)
        buf += b"\0\0\0\0"
        _pad(buf, _ALIGN[element[0]])
        start = len(buf)
        if element == "y":
            buf += bytes(value)
        else:
            items = value.items() if isinstance(value, dict) else value
            for item in items:
                _put(buf, element, item)
        struct.pack_into("<I", buf, length_at, len(buf) - start)
    elif char in "({":
        inner = _split(sig[1:-1])
        for part, item in zip(inner, value):
            _put(buf, part, item)
    else:
        raise ValueError(f"unsupported type {sig}")


def _get(data, pos, sig):
    char = sig[0]
    pos += -pos % _ALIGN[char]
    if char in "so":
        (length,) = struct.unpack_from("<I", data, pos)
        pos += 4
        return data[pos:pos + length].decode(), pos + length + 1
    if char == "g":
        length = data[pos]
        return data[pos + 1:pos + 1 + length].decode(), pos + length + 2
    if char == "b":
        return bool(struct.unpack_from("<I", data, pos)[0]), pos + 4
    if char == "u":
        return struct.unpack_from("<I", data, pos)[0], pos + 4
    if char == "y":
        return data[pos], pos + 1
    if char == "v":
        inner, pos = _get(data, pos, "g")
        return _get(data, pos, inner)
    if char == "a":
        element = sig[1:]
        (length,) = struct.unpack_from("<I", data, pos)
        pos += 4
        pos += -pos % _ALIGN[element[0]]
        end = pos + length
        if element == "y":
            return bytes(data[pos:end]), end
        items = []
        while pos < end:
            item, pos = _get(data, pos, element)
            items.append(item)
        if element[0] == "{":
            return dict(items), end
        return items, end
    if char in "({":
        values = []
        for part in _split(sig[1:-1]):
            item, pos = _get(data, pos, part)
            values.append(item)
        return tuple(values), pos
    raise ValueError(f"unsupported type {sig}")


def _addresses(text):
    for entry in text.split(";"):
        transport, _, options = entry.partition(":")
        if transport != "unix":
            continue
        fields = dict(part.split("=", 1) for part in options.split(",") if "=" in part)
        if "path" in fields:
            yield urllib.parse.unquote(fields["path"])
        elif "abstract" in fields:
            yield "\0" + urllib.parse.unquote(fields["abstract"])


class Bus:
    """One connection to a message bus. Calls block, with a time limit."""

    def __init__(self, address=None, timeout=15.0):
        self._serial = 0
        self._buffer = bytearray()
        self._timeout = timeout
        self._sock = self._connect(address or os.environ.get("DBUS_SESSION_BUS_ADDRESS")
                                   or f"unix:path=/run/user/{os.getuid()}/bus")
        self._sock.sendall(b"\0AUTH EXTERNAL " + str(os.getuid()).encode().hex().encode() + b"\r\n")
        if not self._read_line().startswith(b"OK"):
            raise DBusError("auth", "The bus refused the connection.")
        self._sock.sendall(b"BEGIN\r\n")
        self.call(BUS_NAME, "/org/freedesktop/DBus", BUS_NAME, "Hello")

    def close(self):
        self._sock.close()

    def _connect(self, address):
        last = None
        for path in _addresses(address):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self._timeout)
            try:
                sock.connect(path.encode() if path.startswith("\0") else path)
                return sock
            except OSError as exc:
                sock.close()
                last = exc
        raise DBusError("no-bus", str(last or "no usable bus address"))

    def _read_line(self):
        while b"\r\n" not in self._buffer:
            self._fill()
        line, _, rest = bytes(self._buffer).partition(b"\r\n")
        self._buffer[:] = rest
        return line

    def _fill(self, deadline=None):
        if deadline is not None:
            self._sock.settimeout(max(0.01, deadline - time.monotonic()))
        try:
            chunk = self._sock.recv(65536)
        except TimeoutError:
            raise
        except OSError as exc:
            raise DBusError("io", str(exc)) from exc
        if not chunk:
            raise DBusError("io", "The bus closed the connection.")
        self._buffer += chunk

    def _send(self, kind, fields, sig, args):
        body = bytearray()
        for part, value in zip(_split(sig), args):
            _put(body, part, value)
        self._serial += 1
        message = bytearray(struct.pack("<BBBBII", ord("l"), kind, 0, 1, len(body), self._serial))
        _put(message, "a(yv)", fields + ([(_FIELD_SIG, ("g", sig))] if sig else []))
        _pad(message, 8)
        message += body
        self._sock.sendall(bytes(message))
        return self._serial

    def _read_message(self, deadline):
        while len(self._buffer) < 16:
            self._fill(deadline)
        _, kind, _, _, body_len, _, fields_len = struct.unpack_from("<BBBBIII", self._buffer, 0)
        header_len = 16 + fields_len
        total = header_len + -header_len % 8 + body_len
        while len(self._buffer) < total:
            self._fill(deadline)
        data = bytes(self._buffer[:total])
        del self._buffer[:total]
        raw, _ = _get(data, 12, "a(yv)")
        fields = dict(raw)
        body = data[total - body_len:]
        sig = fields.get(_FIELD_SIG, "")
        values, pos = [], 0
        for part in _split(sig):
            value, pos = _get(body, pos, part)
            values.append(value)
        return kind, fields, values

    def call(self, dest, path, interface, member, sig="", args=(), timeout=None):
        """Call a method and return its answer values. An error answer raises DBusError."""
        fields = [(_FIELD_PATH, ("o", path)), (_FIELD_DEST, ("s", dest)), (_FIELD_MEMBER, ("s", member))]
        if interface:
            fields.append((_FIELD_INTERFACE, ("s", interface)))
        serial = self._send(METHOD_CALL, fields, sig, args)
        deadline = time.monotonic() + (timeout or self._timeout)
        while True:
            try:
                kind, got, values = self._read_message(deadline)
            except TimeoutError as exc:
                raise DBusError("timeout", "The service did not answer in time.") from exc
            if got.get(_FIELD_REPLY) != serial:
                continue
            if kind == METHOD_RETURN:
                return values
            if kind == ERROR:
                raise DBusError(got.get(_FIELD_ERROR, "error"), values[0] if values else "")

    def wait_signal(self, path, interface, member, timeout):
        """Wait for one signal. Returns its values, or None when the time ran out."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                kind, got, values = self._read_message(deadline)
            except TimeoutError:
                return None
            if kind == SIGNAL and got.get(_FIELD_PATH) == path and got.get(_FIELD_INTERFACE) == interface \
                    and got.get(_FIELD_MEMBER) == member:
                return values
