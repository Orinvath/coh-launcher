"""Linux piece: the watch helper. The launcher starts it with Qt's X11 (xcb) platform and owns it.

It shows the watch as a bare X11 box (the window manager does not place it), the see-through ripple layer
around it, and checks which window is in front. It decides nothing about the game, the time or the alerts:
the launcher sends it what to show (one JSON line per command on standard input) and it answers with
events (one JSON line each on standard output): ready, and moved when the watch was dragged or put back.

Commands: config (spot), game (process ids of the game), view (a face or a card), ripples, quit.
"""

import json
import os
import sys
import threading
import time

os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"

from PySide6.QtCore import QObject, QPoint, QRect, QTimer, Qt, Signal  # noqa: E402
from PySide6.QtGui import QPainter, QRegion, QPainterPath  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from .. import watchface  # noqa: E402
from . import x11  # noqa: E402

START_SPOT = (20, 16)
POLL_MS = 125
CLASS_HINT = "cityofheroes.exe"
BOX_FLAGS = (
    Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.X11BypassWindowManagerHint
    | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
)


def emit(**event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


class RippleLayer(QWidget):
    """A see-through layer around the watch. Clicks pass through it."""

    def __init__(self):
        super().__init__(None, BOX_FLAGS | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(*watchface.ripple_size())
        self._started = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def play(self):
        self._started = time.monotonic()
        self.show()
        self._timer.start(30)

    def _step(self):
        if time.monotonic() - self._started >= watchface.RIPPLE_SECONDS:
            self.stop()
        else:
            self.update()

    def stop(self):
        self._timer.stop()
        self._started = None
        self.hide()

    def paintEvent(self, _event):
        if self._started is not None:
            painter = QPainter(self)
            watchface.paint_ripples(painter, time.monotonic() - self._started)


class WatchBox(QWidget):
    moved = Signal(int, int)

    def __init__(self):
        super().__init__(None, BOX_FLAGS)
        self.setWindowTitle("COH Game Watch")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(watchface.WIDTH, watchface.HEIGHT)
        shape = QPainterPath()
        shape.addRoundedRect(0, 0, watchface.WIDTH, watchface.HEIGHT, watchface.RADIUS, watchface.RADIUS)
        self.setMask(QRegion(shape.toFillPolygon().toPolygon()))
        self.view = None
        self.view_stamp = time.monotonic()
        self._grab_offset = None
        self.ripples = RippleLayer() if _compositing() else None
        self._repaint = QTimer(self)
        self._repaint.timeout.connect(self.update)
        self._repaint.start(33)

    def set_view(self, view):
        self.view, self.view_stamp = view, time.monotonic()
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        view = self.view
        if view is not None and isinstance(view, watchface.Card):
            view = watchface.Card(view.time_text, view.text, view.age + time.monotonic() - self.view_stamp)
        if view is not None:
            watchface.paint(painter, view)

    def moveEvent(self, event):
        super().moveEvent(event)
        self._follow()

    def _follow(self):
        if self.ripples is not None:
            self.ripples.move(self.pos() - QPoint(watchface.RIPPLE_REACH, watchface.RIPPLE_REACH))

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.ripples is not None:
            self.ripples.stop()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._grab_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._grab_offset is not None:
            self.move(event.globalPosition().toPoint() - self._grab_offset)

    def mouseReleaseEvent(self, _event):
        if self._grab_offset is not None:
            self._grab_offset = None
            self.move(self._clamped(self.frameGeometry()))
            self.moved.emit(self.x(), self.y())

    def _clamped(self, rect):
        """The watch's top-left corner moved back so the whole box is on the screen it overlaps most."""
        screen = _screen_for(rect)
        area = screen.geometry()
        x = min(max(rect.x(), area.left()), area.right() + 1 - watchface.WIDTH)
        y = min(max(rect.y(), area.top()), area.bottom() + 1 - watchface.HEIGHT)
        return QPoint(x, y)


def _compositing():
    display = x11.Display.open()
    if display is None:
        return False
    try:
        return display.compositing()
    finally:
        display.close()


def _screen_for(rect):
    """The screen that holds most of the rectangle, or the main screen."""
    best, best_area = QApplication.primaryScreen(), 0
    for screen in QApplication.screens():
        overlap = screen.geometry().intersected(rect)
        area = max(overlap.width(), 0) * max(overlap.height(), 0) if overlap.isValid() else 0
        if area > best_area:
            best, best_area = screen, area
    return best


def _on_a_screen(rect):
    return any(screen.geometry().contains(rect) for screen in QApplication.screens())


class Helper(QObject):
    command = Signal(object)

    def __init__(self, display):
        super().__init__()
        self._display = display
        self._box = WatchBox()
        self._box.moved.connect(lambda x, y: emit(event="moved", x=x, y=y))
        self._spot = None
        self._game_pids = []
        self._placed = False
        self._ripples_waiting = False
        self._parent_pid = os.getppid()
        self.command.connect(self._handle)
        timer = QTimer(self)
        timer.timeout.connect(self._poll)
        timer.start(POLL_MS)
        self._timer = timer

    def _handle(self, message):
        name = message.get("cmd")
        if name == "config":
            spot = message.get("spot")
            self._spot = tuple(spot) if spot else None
            self._placed = False
        elif name == "game":
            self._game_pids = message["pids"]
        elif name == "view":
            self._set_view(message)
        elif name == "ripples":
            self._ripples_waiting = True
            self._play_ripples_if_shown()
        elif name == "quit":
            self._quit()

    def _set_view(self, message):
        if message["kind"] == "card":
            view = watchface.Card(message["time"], message["text"], message["age"])
        else:
            view = watchface.Face(message["digits"], message["ampm"], message["day"])
            self._ripples_waiting = False
        self._box.set_view(view)

    def _play_ripples_if_shown(self):
        if self._ripples_waiting and self._box.isVisible() and self._box.ripples is not None:
            self._ripples_waiting = False
            self._box.ripples.play()

    def _in_front(self):
        """(game is in front, the game window) from the display. 'Cannot tell' counts as in front."""
        if self._display is None:
            return True, None
        active = self._display.active_window()
        if active is None:
            return True, None
        if active == 0:
            return False, None
        if not self._display.is_shown(active):
            return False, None
        pid = self._display.window_pid(active)
        if pid is None:
            return CLASS_HINT in self._display.window_class(active).lower(), active
        return pid in self._game_pids or pid == os.getpid(), active

    def _poll(self):
        if os.getppid() != self._parent_pid:
            self._quit()
            return
        in_front, window = self._in_front()
        if self._game_pids and in_front:
            if not self._box.isVisible():
                self._place(window)
                self._box.show()
                self._play_ripples_if_shown()
        elif self._box.isVisible():
            self._box.hide()

    def _place(self, game_window):
        """Put the box at its saved spot, or at the starting spot on the game's screen."""
        if self._placed:
            return
        self._placed = True
        if self._spot is not None:
            rect = QRect(self._spot[0], self._spot[1], watchface.WIDTH, watchface.HEIGHT)
            if _on_a_screen(rect):
                self._box.move(rect.topLeft())
                return
        screen = QApplication.primaryScreen()
        geometry = self._display.geometry(game_window) if self._display is not None and game_window else None
        if geometry:
            screen = _screen_for(QRect(*geometry))
        corner = screen.geometry().topLeft() + QPoint(*START_SPOT)
        self._box.move(corner)
        emit(event="moved", x=corner.x(), y=corner.y())

    def _quit(self):
        self._box.hide()
        QApplication.quit()


def _read_commands(helper):
    for line in sys.stdin:
        try:
            helper.command.emit(json.loads(line))
        except ValueError:
            continue
    helper.command.emit({"cmd": "quit"})


def main():
    app = QApplication([sys.argv[0], "-platform", "xcb"])
    watchface.family()
    display = x11.Display.open()
    helper = Helper(display)
    threading.Thread(target=_read_commands, args=(helper,), daemon=True).start()
    emit(event="ready")
    code = app.exec()
    if display is not None:
        display.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
