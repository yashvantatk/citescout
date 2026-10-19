"""Offline tests for the Browserbase publisher-page tool (mocked HTTP, no key or network needed)."""
import asyncio

import httpx

import net
import publisher

PAYWALL_HTML = ('<html><head><meta name="citation_title" content="Machine learning: Trends, perspectives, and prospects">'
                '</head><body><a>Access through your institution</a> <a>Buy article</a></body></html>')


def test_browserbase_fetch_records_paywall_evidence(monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["key"] = str(req.url), req.headers.get("X-BB-API-Key")
        return httpx.Response(200, json={"id": "f_1", "statusCode": 200, "content": PAYWALL_HTML,
                                         "contentType": "text/html", "encoding": "utf-8", "headers": {}})

    async def run():
        net._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        net._client_loop = asyncio.get_running_loop()
        return await publisher.inspect_publisher("10.1126/science.aaa8415")

    monkeypatch.setenv("BROWSERBASE_API_KEY", "test-key")
    monkeypatch.setenv("BROWSERBASE_SESSIONS", "0")
    publisher._CACHE.clear()
    net.reset_metrics()
    ev = asyncio.run(run())
    assert seen["url"] == publisher.BB_URL and seen["key"] == "test-key"
    assert ev["via"] == "browserbase" and ev["verdict"] == "paywall_confirmed"
    assert "access through your institution" in ev["paywall_markers"]
    assert net.STATS["api.browserbase.com"]["calls"] == 1
    assert all("test-key" not in e["url"] for e in net.EVIDENCE)  # key never logged


def test_signals_open_access():
    s = publisher._signals('<a href="https://creativecommons.org/licenses/by/4.0/">CC BY</a>')
    assert s["verdict"] == "oa_signals"