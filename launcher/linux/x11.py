"""Linux piece: asks the X11 display which window is in front (Feature 4, steps 7 and 8). ctypes over libX11, nothing to install.

If libX11 cannot be loaded, or the display cannot be opened, the answer is "cannot tell".
"""

import ctypes
import ctypes.util
import os

ACTIVE, HIDDEN_STATE = b"_NET_ACTIVE_WINDOW", b"_NET_WM_STATE_HIDDEN"
IS_VIEWABLE = 2
XA_WINDOW, XA_CARDINAL, XA_ATOM, XA_STRING = 33, 6, 4, 31
_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


class _Attributes(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int) for name in (
        "x", "y", "width", "height", "border_width", "depth")] + [
        ("visual", ctypes.c_void_p), ("root", ctypes.c_ulong), ("klass", ctypes.c_int),
        ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
        ("colormap", ctypes.c_ulong), ("map_installed", ctypes.c_int), ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long), ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long), ("override_redirect", ctypes.c_int), ("screen", ctypes.c_void_p)]


def _load():
    name = ctypes.util.find_library("X11")
    if name is None:
        return None
    try:
        lib = ctypes.CDLL(name)
    except OSError:
        return None
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XDefaultRootWindow.restype = ctypes.c_ulong
    lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    lib.XInternAtom.restype = ctypes.c_ulong
    lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    lib.XSetErrorHandler.argtypes = [_ERROR_HANDLER]
    lib.XGetWindowProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]
    lib.XFree.argtypes = [ctypes.c_void_p]
    lib.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_Attributes)]
    lib.XTranslateCoordinates.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong)]
    lib.XGetSelectionOwner.restype = ctypes.c_ulong
    lib.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return lib


_LIB = _load()
# A window can vanish between two questions. This keeps Xlib's default handler from ending the process.
_QUIET = _ERROR_HANDLER(lambda _display, _event: 0)


def display_reachable():
    """True when a display can be opened (DISPLAY is set and the server answers)."""
    if _LIB is None or not os.environ.get("DISPLAY"):
        return False
    display = _LIB.XOpenDisplay(None)
    if not display:
        return False
    _LIB.XCloseDisplay(display)
    return True


class Display:
    """One connection to the display. Use open() and close()."""

    def __init__(self, display):
        self._d = display
        self._root = _LIB.XDefaultRootWindow(display)
        _LIB.XSetErrorHandler(_QUIET)

    @classmethod
    def open(cls):
        if _LIB is None or not os.environ.get("DISPLAY"):
            return None
        display = _LIB.XOpenDisplay(None)
        return cls(display) if display else None

    def close(self):
        _LIB.XCloseDisplay(self._d)

    def _property(self, window, name, kind):
        """The 32-bit values of a window property, or None when it is missing or cannot be read."""
        actual, fmt, count, after = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong()
        data = ctypes.c_void_p()
        atom = _LIB.XInternAtom(self._d, name, 0)
        status = _LIB.XGetWindowProperty(self._d, window, atom, 0, 1024, 0, kind, ctypes.byref(actual),
                                         ctypes.byref(fmt), ctypes.byref(count), ctypes.byref(after), ctypes.byref(data))
        _LIB.XSync(self._d, 0)
        if status != 0 or not data.value:
            return None
        try:
            if fmt.value == 32 and count.value:
                return list(ctypes.cast(data, ctypes.POINTER(ctypes.c_long))[:count.value])
            if fmt.value == 8 and count.value:
                return ctypes.string_at(data.value, count.value)
            return None
        finally:
            _LIB.XFree(data)

    def active_window(self):
        """The window the window manager says is in front: its id, 0 for none, or None when it cannot be read."""
        values = self._property(self._root, ACTIVE, XA_WINDOW)
        return None if values is None else values[0]

    def window_pid(self, window):
        values = self._property(window, b"_NET_WM_PID", XA_CARDINAL)
        return None if values is None else values[0]

    def window_class(self, window):
        raw = self._property(window, b"WM_CLASS", XA_STRING)
        return "" if raw is None else raw.decode("latin-1").replace("\0", " ").strip()

    def is_shown(self, window):
        """True when the window is on screen (mapped and not marked hidden)."""
        attributes = _Attributes()
        if not _LIB.XGetWindowAttributes(self._d, window, ctypes.byref(attributes)):
            return False
        states = self._property(window, b"_NET_WM_STATE", XA_ATOM) or []
        hidden = _LIB.XInternAtom(self._d, HIDDEN_STATE, 0)
        return attributes.map_state == IS_VIEWABLE and hidden not in states

    def geometry(self, window):
        """(x, y, width, height) on the whole display, or None."""
        attributes = _Attributes()
        if not _LIB.XGetWindowAttributes(self._d, window, ctypes.byref(attributes)):
            return None
        x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        _LIB.XTranslateCoordinates(self._d, window, self._root, 0, 0, ctypes.byref(x), ctypes.byref(y), ctypes.byref(child))
        return x.value, y.value, attributes.width, attributes.height

    def compositing(self):
        """True when a compositing manager runs, so see-through windows work."""
        atom = _LIB.XInternAtom(self._d, b"_NET_WM_CM_S0", 0)
        return bool(_LIB.XGetSelectionOwner(self._d, atom))
