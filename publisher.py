"""Publisher-page inspection (the "publisher pages" source) via the Browserbase Fetch API.

Why Browserbase: publisher sites (Science, Elsevier, Wiley...) often block plain scripted
requests (403 / bot walls). Browserbase fetches the real landing page so we can record
*evidence* of the access situation for paywalled papers.

Two Browserbase tiers:
1. Fetch API: one fast call, no JavaScript.
2. If the page is a JS bot check ("Just a moment..."), a real Browserbase browser session
   (Playwright over CDP) loads the page and waits for the check to clear on its own. We never
   solve CAPTCHAs. The session is recorded, and its replay link is kept as evidence.

Policy (enforced in code):
- Evidence only. A publisher page can never turn a paywalled paper into a cited/downloaded one;
  Unpaywall/OpenAlex stay the source of truth for legal OA copies.
- We never follow PDF links found on a publisher page and never try to bypass access controls.
- Without BROWSERBASE_API_KEY we fall back to a plain doi.org fetch (often blocked: that is the point).
"""
from __future__ import annotations

import os
import re
import time
from urllib.parse import urlparse

import net

BB_URL = "https://api.browserbase.com/v1/fetch"
BB_SESSIONS = "https://api.browserbase.com/v1/sessions"
_CACHE: dict[str, dict] = {}

PAYWALL_MARKERS = [
    "access through your institution", "purchase this article", "buy article", "buy this article",
    "rent this article", "get access", "check access", "subscribe to", "log in to view", "sign in to access",
    "purchase access", "institutional login", "pay per view", "access options",
]
OA_MARKERS = ["creativecommons.org/licenses", "this article is open access", "open access article",
              "cc by 4.0", "cc-by"]


def enabled() -> bool:
    return bool(os.getenv("BROWSERBASE_API_KEY"))


def _signals(html: str) -> dict:
    low = (html or "").lower()
    title = re.search(r'<meta[^>]+name=["\']citation_title["\'][^>]+content=["\']([^"\']+)', html or "", re.I) \
        or re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    pay = [m for m in PAYWALL_MARKERS if m in low]
    oa = [m for m in OA_MARKERS if m in low]
    if oa and not pay:
        verdict = "oa_signals"
    elif pay and not oa:
        verdict = "paywall_confirmed"
    elif pay and oa:
        verdict = "mixed"
    else:
        verdict = "inconclusive"
    if not pay and not oa and ("just a moment" in low[:3000] or "client challenge" in low[:3000] or "cf-challenge" in low
                                  or "captcha" in low[:5000]):
        verdict = "blocked"  # bot wall; we do not try to solve it
    return {"page_title": re.sub(r"\s+", " ", title.group(1)).strip()[:200] if title else "",
            "paywall_markers": pay[:5], "oa_markers": oa[:3],
            "has_citation_pdf_meta": "citation_pdf_url" in low, "verdict": verdict}


async def _via_browserbase(url: str) -> dict:
    host = "api.browserbase.com"
    st = net.STATS.setdefault(host, {"calls": 0, "secs": 0.0, "retries": 0})
    st["calls"] += 1
    t0 = time.monotonic()
    try:
        r = await net.client().post(BB_URL, timeout=45.0,
                                    headers={"X-BB-API-Key": os.environ["BROWSERBASE_API_KEY"],
                                             "Content-Type": "application/json"},
                                    json={"url": url, "allowRedirects": True,
                                          "proxies": os.getenv("BROWSERBASE_PROXIES", "0") == "1"})
    finally:
        st["secs"] += time.monotonic() - t0
    net.EVIDENCE.append({"ts": round(time.time(), 2), "host": host, "status": r.status_code,
                         "cached": False, "url": f"{BB_URL} -> {url}"})
    if r.status_code != 200:
        return {"via": "browserbase", "error": f"Browserbase Fetch returned {r.status_code}: {r.text[:200]}"}
    d = r.json()
    content = d.get("content") if isinstance(d.get("content"), str) else ""
    final = (d.get("headers") or {}).get("location") or url
    return {"via": "browserbase", "fetch_id": d.get("id"), "status": d.get("statusCode"),
            "content_type": d.get("contentType"), "final_url": final, **_signals(content)}


def _sessions_enabled() -> bool:
    if os.getenv("BROWSERBASE_SESSIONS", "1") != "1":
        return False
    try:
        import playwright.async_api  # noqa: F401
        return True
    except ImportError:
        return False


async def _via_session(url: str) -> dict:
    """Real cloud browser: lets a JS bot check finish by itself (no CAPTCHA solving), then reads the page."""
    from playwright.async_api import async_playwright
    host = "api.browserbase.com"
    st = net.STATS.setdefault(host, {"calls": 0, "secs": 0.0, "retries": 0})
    st["calls"] += 1
    t0 = time.monotonic()
    body: dict = {"timeout": 90, "browserSettings": {"recordSession": True, "solveCaptchas": False}}
    if pid := os.getenv("BROWSERBASE_PROJECT_ID"):
        body["projectId"] = pid
    try:
        r = await net.client().post(BB_SESSIONS, timeout=45.0, json=body,
                                    headers={"X-BB-API-Key": os.environ["BROWSERBASE_API_KEY"],
                                             "Content-Type": "application/json"})
        net.EVIDENCE.append({"ts": round(time.time(), 2), "host": host, "status": r.status_code,
                             "cached": False, "url": f"{BB_SESSIONS} (browser session) -> {url}"})
        if r.status_code not in (200, 201):
            return {"via": "browserbase-session", "error": f"session create {r.status_code}: {r.text[:200]}"}
        sess = r.json()
        sid = sess["id"]
        html, status, final, title = "", None, url, ""
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(sess["connectUrl"])
            try:
                ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                status = resp.status if resp else None
                for _ in range(15):  # up to ~15 s for a JS check to clear by itself
                    title = (await page.title()).lower()
                    if not any(w in title for w in ("just a moment", "attention required", "client challenge")):
                        break
                    await page.wait_for_timeout(1000)
                await page.wait_for_timeout(1500)
                html, final = await page.content(), page.url
            finally:
                await browser.close()
    finally:
        st["secs"] += time.monotonic() - t0
    return {"via": "browserbase-session", "session_id": sid,
            "replay_url": f"https://www.browserbase.com/sessions/{sid}",
            "status": status, "final_url": final, **_signals(html)}


async def _direct(url: str) -> dict:
    r = await net.fetch(url, headers={"Accept": "text/html"})
    out = {"via": "direct", "status": r.status, "final_url": r.url, **_signals(r.text)}
    if r.status in (401, 403, 429):
        out["verdict"] = "blocked"
        out["note"] = "publisher blocked a plain scripted request; set BROWSERBASE_API_KEY for real page evidence"
    return out


async def inspect_publisher(doi: str) -> dict:
    """Return evidence about a DOI's publisher landing page. Never changes any decision."""
    doi = (doi or "").strip().lower().removeprefix("https://doi.org/")
    if not doi:
        return {"error": "no DOI given"}
    if doi in _CACHE:
        return {**_CACHE[doi], "cached": True}
    url = f"https://doi.org/{doi}"
    try:
        ev = await _via_browserbase(url) if enabled() else await _direct(url)
        if enabled() and _sessions_enabled() and ("error" in ev or ev.get("verdict") in ("blocked", "inconclusive")):
            try:
                sess = await _via_session(url)
            except Exception as e:
                sess = {"error": f"{type(e).__name__}: {e}"}
            if "error" not in sess:
                ev = {**sess, "fetch_verdict": ev.get("verdict") or ev.get("error")}
            else:
                ev["session_error"] = sess["error"]
        if "error" in ev and enabled():
            ev = {**(await _direct(url)), "browserbase_error": ev["error"]}
    except Exception as e:  # evidence is best-effort; never break the run
        ev = {"via": "browserbase" if enabled() else "direct", "error": f"{type(e).__name__}: {e}"}
    ev.update({"doi": doi, "publisher_host": urlparse(ev.get("final_url") or url).netloc,
               "policy": "evidence only; Unpaywall/OpenAlex decide OA; no paywall bypass"})
    _CACHE[doi] = ev
    return ev