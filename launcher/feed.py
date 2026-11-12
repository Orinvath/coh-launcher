"""Live news feed: strict normalizer, atomic cache and the bundled fallback.

The feed text is plain JSON from the official feed page. Every item is checked
before it can reach the UI. Items that fail a check are skipped, and a feed
with no valid item is a failed refresh. Links must pass the official URL rule
in homecoming.py. Nothing from the feed is ever rendered as HTML.
"""

import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path

from . import homecoming as hc

FEED_URL = "https://newsfeed.homecomingservers.com/live"
FEED_TIMEOUT_MS = 20_000
FEED_INTERVAL_MS = 15 * 60 * 1000
MAX_FEED_BYTES = 1024 * 1024
MAX_ITEMS = 20
MAX_HEADLINE_CHARS = 300
DESCRIPTION_LIMIT = 220
CACHE_SCHEMA = 1
CACHE_NAME = "news-feed.json"
FALLBACK_PATH = Path(__file__).resolve().parent / "data" / "news-fallback.json"
DATE_FORMAT = "%B %d, %Y"
LEAD_IMAGE_KEY = "background"
DEFAULT_CATEGORY = ("NEWS", "feature1")
# Checked in order, case-insensitive, against the headline.
CATEGORY_RULES = (
    ("halloween", "EVENT", "feature1"),
    ("event", "EVENT", "feature1"),
    ("dev diary", "DEV DIARY", "feature3"),
    ("open beta", "OPEN BETA", "feature2"),
    ("patch", "PATCH NOTES", "feature4"),
    ("issue", "PATCH NOTES", "feature4"),
)
CACHE_FIELDS = ("id", "title", "subtitle", "headline", "url", "date", "description", "category", "imageKey")


class FeedError(Exception):
    """The feed text was unreadable, too large, or held no valid item."""


def _collapse(text):
    return " ".join(text.split())


def _clip(text):
    """Cut at the last whole word that fits in the limit, then add an ellipsis."""
    if len(text) <= DESCRIPTION_LIMIT:
        return text
    cut = text[: DESCRIPTION_LIMIT - 1]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "…"


def _category(headline):
    lowered = headline.lower()
    for needle, category, image_key in CATEGORY_RULES:
        if needle in lowered:
            return category, image_key
    return DEFAULT_CATEGORY


def _split_headline(headline):
    """Hero shape: 'ISSUE 28: LEGACY, PAGE 4 IS NOW LIVE!' becomes title and subtitle. No comma means no subtitle."""
    upper = headline.upper()
    if ", " in upper:
        title, _, subtitle = upper.partition(", ")
        return title, subtitle
    return upper, ""


def _parse_date(value):
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.strptime(_collapse(value), DATE_FORMAT).date().isoformat()
    except ValueError:
        return None


def _valid_link(item):
    """The headline link first, then the first valid nested content link. Official hosts only."""
    candidates = [item.get("headlinelink")]
    for block in item.get("content", []):
        if isinstance(block, dict):
            candidates.append(block.get("link"))
    for candidate in candidates:
        if hc.valid_official_url(candidate):
            return candidate
    return None


def _description(content):
    """The first non-empty plain text, whitespace collapsed and clipped. Empty is allowed."""
    for block in content:
        text = block if isinstance(block, str) else block.get("text") if isinstance(block, dict) else None
        if isinstance(text, str) and text.strip():
            return _clip(_collapse(text))
    return ""


def _article(index, item):
    """Return one normalized article, or None when the item fails any check."""
    if not isinstance(item, dict):
        return None
    headline = item.get("headline")
    content = item.get("content")
    if not isinstance(headline, str) or not isinstance(content, list):
        return None
    headline = _collapse(headline)
    if not headline or len(headline) > MAX_HEADLINE_CHARS:
        return None
    date = _parse_date(item.get("dateline"))
    url = _valid_link(item)
    if date is None or url is None:
        return None
    category, image_key = _category(headline)
    title, subtitle = _split_headline(headline)
    return {
        "id": "news-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
        "title": title,
        "subtitle": subtitle,
        "headline": headline.upper(),
        "url": url,
        "date": date,
        "description": _description(content),
        "category": category,
        # The lead item keeps the rooftop background. Other items use the category's skin image.
        "imageKey": LEAD_IMAGE_KEY if index == 0 else image_key,
    }


def normalize_feed(text):
    """Return the valid, de-duplicated articles in feed order (at most MAX_ITEMS). Raise FeedError if none."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_FEED_BYTES:
        raise FeedError("The news feed was too large or unreadable.")
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise FeedError("The news feed was not valid JSON.") from exc
    if not isinstance(raw, list):
        raise FeedError("The news feed was not a list.")
    articles, seen = [], set()
    for item in raw[:MAX_ITEMS]:
        article = _article(len(articles), item)
        if article is None or article["id"] in seen:
            continue
        seen.add(article["id"])
        articles.append(article)
    if not articles:
        raise FeedError("The news feed held no valid item.")
    return articles


def bundled_articles():
    """The bundled snapshot, normalized by the same adapter. It is labelled OFFLINE, never LIVE."""
    return normalize_feed(FALLBACK_PATH.read_text(encoding="utf-8"))


def format_saved_date(iso):
    """'2026-10-06T12:00:00+00:00' becomes 'OCTOBER 6, 2026'."""
    when = dt.datetime.fromisoformat(iso)
    return f"{when.strftime('%B')} {when.day}, {when.year}".upper()


def _valid_cached_article(article):
    if not isinstance(article, dict) or any(not isinstance(article.get(key), str) for key in CACHE_FIELDS):
        return False
    return hc.valid_official_url(article["url"]) and article["imageKey"] in (LEAD_IMAGE_KEY, "feature1", "feature2", "feature3", "feature4")


def load_cache(path):
    """Return (articles, fetchedAtUTC) from the last good feed, or None. A broken cache is ignored."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != CACHE_SCHEMA:
        return None
    fetched = data.get("fetchedAtUTC")
    articles = data.get("articles")
    if not isinstance(fetched, str) or not isinstance(articles, list) or not articles:
        return None
    try:
        dt.datetime.fromisoformat(fetched)
    except ValueError:
        return None
    if not all(_valid_cached_article(article) for article in articles[:MAX_ITEMS]):
        return None
    return articles[:MAX_ITEMS], fetched


def save_cache(path, articles, fetched_at):
    """Write the cache atomically: a temporary file in the same folder, then replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"schema": CACHE_SCHEMA, "fetchedAtUTC": fetched_at, "articles": articles}, ensure_ascii=False)
    fd, temp = tempfile.mkstemp(prefix=".news-feed-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.replace(temp, path)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
