"""Qt lifecycle: frameless window, WebEngine policy, custom URL schemes, smoke test.

The page is served from homecoming-app://ui/ and skin images from
homecoming-skin://<skin>/<asset>. Both schemes read only from fixed folders.
Remote navigation, new windows, downloads and permission prompts are refused.
"""

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QBuffer, QByteArray, QIODevice, QProcess, QProcessEnvironment, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestJob,
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow

from . import config as cfgmod
from . import homecoming as hc
from . import skins as skinmod
from .bridge import Bridge, Hooks
from .jobs import SMOKE_RESPONSES, FixtureRunner, ProcessRunner

APP_NAME = "COH Launcher"
DESIGN_W = 1635
DESIGN_H = 962
MIN_W = 1100
MIN_H = 647
UI_DIR = Path(__file__).resolve().parent.parent / "ui"
APP_SCHEME = b"homecoming-app"
SKIN_SCHEME = b"homecoming-skin"
CHROME = "/usr/bin/google-chrome"
XDG_OPEN = "/usr/bin/xdg-open"
UI_MIME = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff2": "font/woff2",
}
SMOKE_TIMEOUT_MS = 30_000


def window_size_for(scale, avail_w, avail_h):
    """Return the startup size: 1635x962 times the factor, bounded by the screen and the minimum."""
    factor = min(scale, avail_w / DESIGN_W, avail_h / DESIGN_H)
    width = max(DESIGN_W * factor, min(MIN_W, avail_w))
    height = max(DESIGN_H * factor, min(MIN_H, avail_h))
    return int(round(width)), int(round(height))


def _started(result):
    """PySide6 returns (ok, pid) from QProcess.startDetached; older bindings return a bool."""
    return result[0] if isinstance(result, tuple) else bool(result)


def make_open_url(chrome=CHROME):
    """Return the Chrome opener. Production uses the real Chrome path; tests pass their own executable boundary."""

    def open_url(url):
        if not hc.is_allowed_link(url):
            raise OSError("That link is not on the launcher's allowlist.")
        if not os.path.exists(chrome):
            raise OSError(f"Google Chrome was not found at {chrome}.")
        # --new-window gives the user a visible window; --new-tab can hide in an existing session.
        if not _started(QProcess.startDetached(chrome, ["--new-window", url])):
            raise OSError("Google Chrome did not start.")

    return open_url


def _open_folder(path):
    if not os.path.isdir(path):
        raise OSError("No screenshots folder was found in this installation.")
    if not os.path.exists(XDG_OPEN):
        raise OSError(f"The file manager was not found at {XDG_OPEN}.")
    if not _started(QProcess.startDetached(XDG_OPEN, [path])):
        raise OSError("The file manager did not start.")


def _start_detached(argv, cwd, env):
    """Open the official launcher detached, with the filtered Proton environment and no -text."""
    process = QProcess()
    environment = QProcessEnvironment()
    for key, value in env.items():
        environment.insert(key, value)
    process.setProcessEnvironment(environment)
    process.setProgram(argv[0])
    process.setArguments(list(argv[1:]))
    process.setWorkingDirectory(cwd)
    if not _started(process.startDetached()):
        raise OSError("The official launcher did not start. Check the Proton path and game folder.")


class LauncherPage(QWebEnginePage):
    """Only the launcher page may navigate. Console errors are kept for the smoke test."""

    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        self.console_errors = []

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return url.scheme() == APP_SCHEME.decode() and url.host() == "ui"

    def createWindow(self, window_type):
        return None

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        text = f"{source_id}:{line_number} {message}"
        print(f"[page] {text}", file=sys.stderr)
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            self.console_errors.append(text)


class UiSchemeHandler(QWebEngineUrlSchemeHandler):
    """Serves files from ui/ only. Traversal, other types and missing files fail."""

    def requestStarted(self, job):
        url = job.requestUrl()
        relative = url.path().lstrip("/") or "index.html"
        target = (UI_DIR / relative).resolve()
        mime = UI_MIME.get(target.suffix.lower())
        if url.host() != "ui" or mime is None or not target.is_file() or not target.is_relative_to(UI_DIR.resolve()):
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        self._reply(job, mime, target.read_bytes())

    def _reply(self, job, mime, data):
        buffer = QBuffer(job)
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(QByteArray(mime.encode()), buffer)


class SkinSchemeHandler(QWebEngineUrlSchemeHandler):
    """Serves validated skin assets by skin id and asset key. Nothing else is reachable."""

    def __init__(self, bridge_getter, parent=None):
        super().__init__(parent)
        self._bridge_getter = bridge_getter

    def requestStarted(self, job):
        url = job.requestUrl()
        bridge = self._bridge_getter()
        path = bridge.skin_asset(url.host(), url.path().lstrip("/")) if bridge else None
        if path is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        buffer = QBuffer(job)
        buffer.setData(QByteArray(path.read_bytes()))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(QByteArray(skinmod.image_mime(path).encode()), buffer)


def register_schemes():
    """Register both custom schemes. Must run before QApplication is created."""
    for name in (APP_SCHEME, SKIN_SCHEME):
        scheme = QWebEngineUrlScheme(name)
        scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
        scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme | QWebEngineUrlScheme.Flag.CorsEnabled)
        QWebEngineUrlScheme.registerScheme(scheme)


class MainWindow(QMainWindow):
    def __init__(self, smoke=False):
        super().__init__()
        self._bridge = None
        self._quitting = False
        self._smoke = smoke
        self.setWindowTitle(APP_NAME)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self._profile = QWebEngineProfile(self)
        self._profile.downloadRequested.connect(lambda request: request.cancel())
        self._profile.installUrlSchemeHandler(APP_SCHEME, UiSchemeHandler(self._profile))
        self._page = LauncherPage(self._profile, self)
        self._view = QWebEngineView(self)
        self._view.setPage(self._page)
        self._page.featurePermissionRequested.connect(
            lambda origin, feature: self._page.setFeaturePermission(
                origin, feature, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
            )
        )
        settings = self._page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, False)
        self._profile.installUrlSchemeHandler(SKIN_SCHEME, SkinSchemeHandler(lambda: self._bridge, self._profile))
        self.setCentralWidget(self._view)

    def attach(self, bridge):
        self._bridge = bridge
        channel = QWebChannel(self._page)
        channel.registerObject("launcher", bridge)
        self._page.setWebChannel(channel)
        url = QUrl(f"{APP_SCHEME.decode()}://ui/index.html")
        if self._smoke:
            url.setQuery("test=1")
        self._view.load(url)

    def page(self):
        return self._page

    def hide_window(self):
        """Close during an operation: hide the window, keep the app and the official process running."""
        self.hide()

    def quit_app(self):
        self._quitting = True
        self.close()
        QApplication.quit()

    def closeEvent(self, event):
        if not self._quitting and self._bridge is not None and self._bridge.busy():
            event.ignore()
            self._bridge.request_close_confirm()
            return
        event.accept()
        QApplication.quit()

    def start_drag(self):
        handle = self.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    def pick_path(self, field, current):
        start = current if os.path.isabs(current) else str(Path.home())
        if field == "protonPath":
            path, _ = QFileDialog.getOpenFileName(self, "Choose the Proton executable", start)
        else:
            title = "Choose the Homecoming game folder" if field == "gameRoot" else "Choose the compatibility prefix"
            path = QFileDialog.getExistingDirectory(self, title, start)
        return path or None

    def resize_for_scale(self, scale):
        available = QGuiApplication.primaryScreen().availableGeometry()
        width, height = window_size_for(scale, available.width(), available.height())
        self.setMinimumSize(min(MIN_W, available.width()), min(MIN_H, available.height()))
        self.resize(width, height)


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="launcher.py", description=APP_NAME)
    parser.add_argument("--smoke-test", action="store_true", help="fixture adapters; READY and screenshot, then exit")
    parser.add_argument("--screenshot", help="project-local PNG path for --smoke-test")
    parser.add_argument("--config-dir", help="project-local settings folder")
    args = parser.parse_args(argv)
    if args.smoke_test and not args.screenshot:
        parser.error("--smoke-test needs --screenshot PATH")
    return args


def _inside_project(value):
    path = Path(value).resolve()
    return path.is_relative_to(cfgmod.PROJECT_ROOT) and path


def create(config_dir, smoke=False):
    """Build the real window and its bridge. Returns (app, window, bridge); nothing is shown or started yet."""
    register_schemes()
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("coh-launcher")
    app.setWindowIcon(QIcon(str(UI_DIR / "icons" / "coh-launcher.png")))
    window = MainWindow(smoke=smoke)
    hooks = Hooks(
        open_url=make_open_url(),
        open_folder=_open_folder,
        start_detached=_start_detached,
        pick_path=window.pick_path,
        minimize=window.showMinimized,
        drag=window.start_drag,
        quit_app=window.quit_app,
        resize_for_scale=window.resize_for_scale,
        hide_window=window.hide_window,
    )
    if smoke:
        runner = FixtureRunner(SMOKE_RESPONSES, parent=app)
        bridge = Bridge(
            hooks, runner, shard_fetcher=_smoke_shards, config_dir=config_dir, parent=app, feed_fetcher=_smoke_feed,
        )
    else:
        runner = ProcessRunner(parent=app)
        bridge = Bridge(hooks, runner, config_dir=config_dir, parent=app)
    app.aboutToQuit.connect(bridge.shutdown)
    window.attach(bridge)
    window.resize_for_scale(bridge.snapshot()["scale"])
    return app, window, bridge


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    config_dir = cfgmod.DEFAULT_CONFIG_DIR
    screenshot = None
    if args.smoke_test:
        screenshot = _inside_project(args.screenshot)
        if not screenshot or screenshot.suffix.lower() != ".png":
            print("--screenshot must be a .png path inside the project folder.", file=sys.stderr)
            return 2
    if args.config_dir:
        config_dir = _inside_project(args.config_dir)
        if not config_dir:
            print("--config-dir must be inside the project folder.", file=sys.stderr)
            return 2

    app, window, bridge = create(config_dir, smoke=args.smoke_test)
    window.show()
    bridge.start()

    if args.smoke_test:
        return _run_smoke(app, window, screenshot)
    # Hidden-window close keeps the event loop alive until the running operation ends (no kill, no forgotten child).
    app.setQuitOnLastWindowClosed(False)
    return app.exec()


def _smoke_feed(callback):
    """Smoke runs never reach the internet. The bundled or saved news is shown and labelled as such."""
    callback(None, "The smoke test does not fetch the live feed.")


def _smoke_shards():
    return [
        {"name": "Torchbearer", "status": "Online"},
        {"name": "Excelsior", "status": "Online"},
        {"name": "Indomitable", "status": "Online"},
        {"name": "Everlasting", "status": "Online"},
        {"name": "Victory", "status": "Online"},
        {"name": "Reunion", "status": "Online"},
    ]


def _run_smoke(app, window, screenshot):
    """Wait for the page to render, save one screenshot, print READY and exit."""
    deadline = [SMOKE_TIMEOUT_MS]
    timer = QTimer(app)
    timer.setInterval(250)

    def check():
        deadline[0] -= 250
        if deadline[0] <= 0:
            print("Smoke test timed out waiting for the page.", file=sys.stderr)
            app.exit(1)
            return
        window.page().runJavaScript(
            "document.body && document.body.dataset.ready === '1'",
            lambda ready: ready and _finish(),
        )

    def _finish():
        timer.stop()
        QTimer.singleShot(300, lambda: _capture())

    def _capture():
        window.grab().save(str(screenshot), "PNG")
        errors = window.page().console_errors
        if errors:
            print("Smoke test saw page errors:\n" + "\n".join(errors), file=sys.stderr)
            app.exit(1)
            return
        print("READY", flush=True)
        app.exit(0)

    timer.timeout.connect(check)
    timer.start()
    return app.exec()
