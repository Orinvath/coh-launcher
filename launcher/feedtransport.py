"""Fetch the live news feed with the Qt engine's ordinary page loader.

The official feed refuses plain HTTP clients, so this reads the page text the
same way a browser would. It uses a separate, viewless, off-the-record profile:
no window, no launcher storage, no cookies kept. Only the feed URL may load.
Popups, downloads and permissions are refused. One request runs at a time.
"""

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile

from . import feed

FEED_TEXT_CHAR_LIMIT = feed.MAX_FEED_BYTES


class _FeedPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return is_main_frame and url.toString() == feed.FEED_URL

    def createWindow(self, window_type):
        return None


class FeedTransport(QObject):
    """Callable: fetch(callback) calls callback(text, None) on success or callback(None, message) on failure."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._profile = QWebEngineProfile(self)  # no storage name, so it is off the record
        self._profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self._profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self._profile.downloadRequested.connect(lambda request: request.cancel())
        self._page = _FeedPage(self._profile, self)
        self._page.featurePermissionRequested.connect(
            lambda origin, feature: self._page.setFeaturePermission(
                origin, feature, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
            )
        )
        self._page.loadFinished.connect(self._loaded)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timed_out)
        self._callback = None
        self._generation = 0

    def fetch(self, callback):
        if self._callback is not None:
            callback(None, "A news request is already running.")
            return
        self._callback = callback
        self._generation += 1
        self._timer.start(feed.FEED_TIMEOUT_MS)
        self._page.load(QUrl(feed.FEED_URL))

    def _loaded(self, ok):
        if self._callback is None:
            return
        if not ok:
            self._finish(None, "The news feed could not be loaded.")
            return
        if self._page.url().toString() != feed.FEED_URL:
            self._finish(None, "The news feed redirected to an unexpected address.")
            return
        generation = self._generation
        self._page.toPlainText(lambda text: self._text(generation, text))

    def _text(self, generation, text):
        if generation != self._generation or self._callback is None:
            return
        if not isinstance(text, str) or len(text.encode("utf-8")) > FEED_TEXT_CHAR_LIMIT:
            self._finish(None, "The news feed was too large or unreadable.")
            return
        self._finish(text, None)

    def _timed_out(self):
        # Stop the old load and change the generation first, so a late page result cannot reach a newer request.
        self._generation += 1
        self._page.triggerAction(QWebEnginePage.WebAction.Stop)
        self._finish(None, "The news feed did not answer in time.")

    def _finish(self, text, error):
        if self._callback is None:
            return
        callback, self._callback = self._callback, None
        self._timer.stop()
        callback(text, error)
