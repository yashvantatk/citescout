"""Shared async HTTP layer: per-host rate limits, retries, and a SQLite response cache.

Every outbound call in CiteScout goes through `fetch()` so we get:
- polite per-host pacing (arXiv asks for ~1 req / 3 s; S2 keyless is a shared pool)
- tenacity retries with exponential backoff on 429 / 5xx / network errors
- a disk cache keyed by (method, url, accept header) so re-runs and tests are fast
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

try:  # load .env before anything reads os.getenv
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

CONTACT = os.getenv("CONTACT_EMAIL", "citescout@example.com")
USER_AGENT = f"CiteScout/0.1 (literature scout; mailto:{CONTACT})"
CACHE_PATH = os.getenv("HTTP_CACHE_PATH", ".cache/http_cache.sqlite")
CACHE_TTL = int(os.getenv("HTTP_CACHE_TTL", str(7 * 24 * 3600)))

# minimum seconds between requests to the same host
HOST_INTERVAL = {
    "export.arxiv.org": 3.0,
    "api.semanticscholar.org": 1.1 if os.getenv("S2_API_KEY") else 3.0,
    "api.openalex.org": 0.2,
    "api.crossref.org": 0.2,
    "api.unpaywall.org": 0.2,
    "doi.org": 0.2,
}


STATS: dict[str, dict] = {}  # per-host live calls / seconds / retries (for the metrics panel)
EVIDENCE: list[dict] = []    # every source consulted: url, status, cache hit (the "source evidence" log)


def _evidence(url: str, status: int, cached: bool, host: str):
    import re as _re
    EVIDENCE.append({"ts": round(time.time(), 2), "host": host, "status": status, "cached": cached,
                     "url": _re.sub(r"([?&])(api_key|key)=[^&]+&?", r"\1", url).rstrip("?&")})


def reset_metrics():
    STATS.clear()
    EVIDENCE.clear()


class RetryableHTTP(Exception):
    pass


@dataclass
class Resp:
    status: int
    url: str
    text: str
    headers: dict
    from_cache: bool = False

    def json(self):
        return json.loads(self.text)


class _Cache:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, ts REAL, status INT, url TEXT, body TEXT, headers TEXT)"
        )

    def get(self, k):
        row = self.db.execute("SELECT ts,status,url,body,headers FROM cache WHERE k=?", (k,)).fetchone()
        if row and time.time() - row[0] < CACHE_TTL:
            return Resp(row[1], row[2], row[3], json.loads(row[4]), from_cache=True)
        return None

    def put(self, k, r: Resp):
        self.db.execute(
            "INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?,?)",
            (k, time.time(), r.status, r.url, r.text, json.dumps(r.headers)),
        )
        self.db.commit()


_cache = _Cache(CACHE_PATH)
_locks: dict[str, asyncio.Lock] = {}
_last: dict[str, float] = {}
_client: httpx.AsyncClient | None = None


_client_loop = None


def client() -> httpx.AsyncClient:
    """One client per event loop (Streamlit calls asyncio.run per click)."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client_loop = loop
        _locks.clear()
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
    return _client


async def _pace(host: str):
    lock = _locks.setdefault(host, asyncio.Lock())
    async with lock:
        gap = HOST_INTERVAL.get(host, 0.3)
        wait = _last.get(host, 0) + gap - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last[host] = time.monotonic()


async def fetch(url: str, *, params: dict | None = None, headers: dict | None = None,
                use_cache: bool = True, attempts: int = 3) -> Resp:
    """GET with pacing, retry and caching. Returns Resp for any final status (incl. 404)."""
    headers = headers or {}
    if "api.openalex.org" in url and (oak := os.getenv("OPENALEX_API_KEY")):
        params = {**(params or {}), "api_key": oak}
    req = client().build_request("GET", url, params=params, headers=headers)
    key = hashlib.sha256(f"GET|{req.url}|{headers.get('Accept','')}".encode()).hexdigest()
    host = urlparse(str(req.url)).netloc
    if use_cache and (hit := _cache.get(key)):
        _evidence(str(req.url), hit.status, True, host)
        STATS.setdefault(host, {"calls": 0, "secs": 0.0, "retries": 0}).setdefault("cache_hits", 0)
        STATS[host]["cache_hits"] += 1
        return hit

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential_jitter(initial=1, max=8),
        retry=retry_if_exception_type((RetryableHTTP, httpx.TransportError)),
        reraise=True,
    ):
        with attempt:
            await _pace(host)
            t0 = time.monotonic()
            st = STATS.setdefault(host, {"calls": 0, "secs": 0.0, "retries": 0})
            st["calls"] += 1
            st["retries"] += attempt.retry_state.attempt_number > 1
            try:
                r = await client().send(req)
            finally:
                st["secs"] += time.monotonic() - t0
            if r.status_code == 429 or r.status_code >= 500:
                raise RetryableHTTP(f"{r.status_code} from {host}")
    resp = Resp(r.status_code, str(r.url), r.text, dict(r.headers))
    _evidence(str(req.url), resp.status, False, host)
    if use_cache and resp.status in (200, 404):
        _cache.put(key, resp)
    return resp