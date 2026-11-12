"""Fish Audio: the voice list (Feature 9, step 5) and speaking an alert (Feature 10). The same on every system.

The key is passed in for each call and is never stored here.
"""

import json
import urllib.parse

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import testenv

API_URL = "https://api.fish.audio"
PAGE_SIZE = 100
OWN_PAGES = 20
PUBLIC_PAGES = 5
ANSWER_SECONDS = 15
FREE_MODEL = "s2.1-pro-free"
FULL_MODEL = "s2.1-pro"
MODEL_REFUSALS = (400, 402, 403, 404, 422)

REASON_KEY = "Fish Audio did not accept your key."
REASON_UNREACHABLE = "Fish Audio could not be reached. Check your internet connection."
REASON_BUSY = "Fish Audio says there are too many requests. Try again later."
REASON_DOWN = "Fish Audio is having problems right now."
REASON_SLOW = f"Fish Audio did not answer within {ANSWER_SECONDS} seconds."


def refusal_reason(status):
    if status == 401:
        return REASON_KEY
    if status == 429:
        return REASON_BUSY
    if status >= 500:
        return REASON_DOWN
    return f"Fish Audio refused the request (error {status})."


class _Call(QObject):
    """One request with a time limit. done(status, body); status is None when Fish Audio was not reached."""

    done = Signal(object, object)
    timed_out = Signal()

    def __init__(self, manager, request, body, parent=None):
        super().__init__(parent)
        self._expired = False
        self._reply = manager.post(request, body) if body is not None else manager.get(request)
        self._reply.finished.connect(self._finished)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._expire)
        self._timer.start(ANSWER_SECONDS * 1000)

    def _expire(self):
        self._expired = True
        self._reply.abort()

    def _finished(self):
        self._timer.stop()
        reply = self._reply
        if self._expired:
            self.timed_out.emit()
        elif reply.error() == QNetworkReply.NetworkError.OperationCanceledError:
            return
        else:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            self.done.emit(status, bytes(reply.readAll()))
        reply.deleteLater()

    def cancel(self):
        self._timer.stop()
        self._reply.finished.disconnect(self._finished)
        self._reply.abort()
        self._reply.deleteLater()


def _request(url, key):
    request = QNetworkRequest(QUrl(url))
    request.setRawHeader(b"Authorization", f"Bearer {key}".encode())
    return request


class VoiceListLoader(QObject):
    """Loads the user's own voices, then the top public ones. loaded(groups) or failed(reason)."""

    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._call = None
        self._lists = []
        self._plan = []
        self._key = ""

    def load(self, key):
        self.cancel()
        self._key = key
        self._lists = []
        # Each entry: heading, asked for with self=true, most pages, next page number, voices so far.
        self._plan = [["My voices", True, OWN_PAGES, 1, []], ["Public voices", False, PUBLIC_PAGES, 1, []]]
        self._next_page()

    def cancel(self):
        if self._call is not None:
            self._call.cancel()
            self._call = None
        self._key = ""

    def _next_page(self):
        if not self._plan:
            self._finish()
            return
        _label, own, _pages, number, _voices = self._plan[0]
        query = {"page_size": PAGE_SIZE, "page_number": number, "sort_by": "score"}
        if own:
            query["self"] = "true"
        url = f"{testenv.fish_base_url(API_URL)}/model?{urllib.parse.urlencode(query)}"
        self._call = _Call(self._manager, _request(url, self._key), None, self)
        self._call.done.connect(self._page_done)
        self._call.timed_out.connect(lambda: self._fail(REASON_SLOW))

    def _page_done(self, status, body):
        self._call.deleteLater()
        self._call = None
        if status is None:
            self._fail(REASON_UNREACHABLE)
            return
        if status != 200:
            self._fail(refusal_reason(status))
            return
        try:
            answer = json.loads(body)
            items = answer["items"]
            if not isinstance(items, list):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            self._fail(refusal_reason(status))
            return
        entry = self._plan[0]
        entry[4].extend(item for item in items if isinstance(item, dict))
        entry[3] += 1
        if not items or answer.get("has_more") is False or entry[3] > entry[2]:
            self._lists.append((entry[0], entry[4]))
            self._plan.pop(0)
        self._next_page()

    def _fail(self, reason):
        self.cancel()
        self.failed.emit(reason)

    def _finish(self):
        seen = set()
        groups = []
        for label, items in self._lists:
            voices = []
            for item in items:
                voice_id = item.get("_id")
                if not _speakable(item) or not isinstance(voice_id, str) or voice_id in seen:
                    continue
                seen.add(voice_id)
                voices.append({"id": voice_id, "title": item.get("title") or voice_id})
            if voices:
                groups.append({"label": label, "voices": voices})
        self._key = ""
        self.loaded.emit(groups)


def _speakable(item):
    """Only trained voices for speaking that are not taken down."""
    return (
        item.get("type") == "tts"
        and item.get("state") == "trained"
        and item.get("dmca_taken_down") is not True
        and not item.get("takedown_category")
    )


class SpeechCall(QObject):
    """One spoken alert being fetched. done(sound or None, reason or None)."""

    done = Signal(object, object)

    def __init__(self, client, key, text, voice_id, parent=None):
        super().__init__(parent)
        self._client = client
        self._key = key
        self._payload = json.dumps({"text": text, "reference_id": voice_id, "format": "mp3"}).encode()
        self._call = None
        self._send()

    def _send(self):
        request = _request(f"{testenv.fish_base_url(API_URL)}/v1/tts", self._key)
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setRawHeader(b"model", self._client.model.encode())
        self._call = _Call(self._client.manager, request, QByteArray(self._payload), self)
        self._call.done.connect(self._answered)
        self._call.timed_out.connect(lambda: self._end(None, REASON_SLOW))

    def _answered(self, status, body):
        self._call.deleteLater()
        self._call = None
        if status is None:
            self._end(None, REASON_UNREACHABLE)
        elif status == 200:
            self._end(body, None)
        elif self._model_refused(status, body):
            self._client.model = FULL_MODEL
            self._send()
        else:
            self._end(None, refusal_reason(status))

    def _model_refused(self, status, body):
        text = body.decode("utf-8", "replace").lower()
        mentions_model = "model" in text or FREE_MODEL in text
        return status in MODEL_REFUSALS and mentions_model and self._client.model == FREE_MODEL

    def _end(self, sound, reason):
        self._key = ""
        self._payload = b""
        self.done.emit(sound, reason)

    def cancel(self):
        if self._call is not None:
            self._call.cancel()
            self._call = None
        self._key = ""


class SpeechClient(QObject):
    """Starts spoken-alert calls and remembers which model works for the rest of the session."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.model = FREE_MODEL

    def speak(self, key, text, voice_id):
        return SpeechCall(self, key, text, voice_id, self)
