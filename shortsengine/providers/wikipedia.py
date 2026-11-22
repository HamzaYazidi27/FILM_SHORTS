"""Wikipedia / Wikimedia client for research and trend discovery.

Every fetched page is returned with a permanent revision URL so a claim can be traced to the
exact text that supported it."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from .. import logs

log = logs.get("wikipedia")
API = "https://en.wikipedia.org/w/api.php"
REST = "https://en.wikipedia.org/api/rest_v1"
UA = "ShortsEngine/0.1 (research tool; https://www.mediawiki.org/wiki/API:Etiquette)"


@dataclass
class Page:
    title: str
    pageid: int
    revid: int
    url: str
    permalink: str
    text: str


_last_call = [0.0]
_lock = threading.Lock()
MIN_INTERVAL = 1.0  # be polite: at most ~1 request/second from this process


def _get(url: str, params: dict | None = None) -> dict:
    last = None
    for attempt in range(6):
        with _lock:
            wait = _last_call[0] + MIN_INTERVAL - time.time()
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.time()
        try:
            r = httpx.get(url, params=params, headers={"User-Agent": UA, "Api-User-Agent": UA}, timeout=40)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
            if r.status_code == 404:
                break
            if r.status_code == 429:
                time.sleep(min(60, float(r.headers.get("retry-after", 0) or 0) or 5 * (attempt + 1)))
                continue
        except httpx.HTTPError as e:
            last = str(e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"wikipedia request failed: {last} ({url})")


def search(query: str, limit: int = 5) -> list[dict]:
    d = _get(API, {"action": "query", "list": "search", "srsearch": query, "srlimit": limit, "format": "json",
                   "srprop": "snippet|timestamp"})
    return d.get("query", {}).get("search", [])


def page(title: str, max_chars: int = 30000) -> Page | None:
    d = _get(API, {"action": "query", "prop": "extracts|revisions|info", "explaintext": 1, "titles": title,
                   "redirects": 1, "format": "json", "rvprop": "ids", "inprop": "url"})
    pages = d.get("query", {}).get("pages", {})
    for pid, p in pages.items():
        if int(pid) < 0 or "extract" not in p:
            return None
        revid = p.get("revisions", [{}])[0].get("revid", 0)
        return Page(title=p["title"], pageid=int(pid), revid=revid, url=p.get("fullurl", ""),
                    permalink=f"https://en.wikipedia.org/w/index.php?oldid={revid}", text=p["extract"][:max_chars])
    return None


def most_read(days_back: int = 1, limit: int = 40) -> list[dict]:
    """Wikipedia 'most read' articles for a recent day (trend signal)."""
    d = datetime.now(timezone.utc) - timedelta(days=days_back)
    try:
        data = _get(f"{REST}/feed/featured/{d:%Y/%m/%d}")
    except RuntimeError as e:
        log.warning("most-read feed unavailable: %s", e)
        return []
    arts = (data.get("mostread") or {}).get("articles", [])
    return [{"title": a.get("normalizedtitle") or a.get("title"), "views": a.get("views"),
             "description": a.get("description", ""), "extract": (a.get("extract") or "")[:400]}
            for a in arts[:limit]]
