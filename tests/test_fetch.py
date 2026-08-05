import asyncio

import httpx
import pytest
from conftest import fixture

from reapfield import fetch as fetch_mod
from reapfield.config import Config
from reapfield.errors import BlockedError, RobotsDisallowed, TerminalHTTPError

URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"


@pytest.fixture
def no_sleep(monkeypatch):
    """Record backoff delays instead of serving them -- asserts stay fast."""
    delays: list[float] = []
    real = asyncio.sleep

    async def fake(seconds, *a, **kw):
        delays.append(seconds)
        await real(0)

    monkeypatch.setattr(fetch_mod.asyncio, "sleep", fake)
    return delays


def _transport(handler):
    fetch_mod.TRANSPORT = httpx.MockTransport(handler)


async def test_429_backoff(no_sleep):
    """429, 429, then 200: three attempts, growing delays, Retry-After honoured."""
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts.append(request)
        if len(attempts) < 3:
            return httpx.Response(429, headers={"Retry-After": "7"}, text="slow down")
        return httpx.Response(200, text="<html><body>ok</body></html>")

    _transport(handler)
    resp = await fetch_mod.fetch(URL, Config(use_cache=False))

    assert len(attempts) == 3
    assert resp.status == 200
    backoffs = [d for d in no_sleep if d]
    assert backoffs[:2] == [7.0, 7.0]  # Retry-After wins over the computed backoff


async def test_backoff_grows_without_retry_after(no_sleep):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts.append(request)
        return httpx.Response(503, text="nope") if len(attempts) < 3 else httpx.Response(200, text="ok")

    _transport(handler)
    await fetch_mod.fetch(URL, Config(use_cache=False))

    backoffs = [d for d in no_sleep if d]
    assert backoffs[1] > backoffs[0]  # exponential, jitter included


async def test_robots_disallow(no_sleep):
    """Disallowed means nothing is fetched at all -- not fetched-then-discarded."""
    page_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=fixture("robots.txt"))
        page_requests.append(request)
        return httpx.Response(200, text="secret")

    _transport(handler)
    with pytest.raises(RobotsDisallowed):
        await fetch_mod.fetch("https://books.toscrape.com/private/x", Config(use_cache=False))

    assert page_requests == []


async def test_robots_allows_other_paths(no_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=fixture("robots.txt"))
        return httpx.Response(200, text="<html><body>fine</body></html>")

    _transport(handler)
    resp = await fetch_mod.fetch("https://books.toscrape.com/public/x", Config(use_cache=False))
    assert resp.status == 200


async def test_missing_robots_is_not_a_disallow(no_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="<html><body>fine</body></html>")

    _transport(handler)
    assert (await fetch_mod.fetch(URL, Config(use_cache=False))).status == 200


async def test_terminal_status_is_not_retried(no_sleep):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts.append(request)
        return httpx.Response(404, text="gone")

    _transport(handler)
    with pytest.raises(TerminalHTTPError):
        await fetch_mod.fetch(URL, Config(use_cache=False))
    assert len(attempts) == 1


async def test_block_detection_stops_the_run(no_sleep):
    """We name the system and stop. No workaround is attempted, ever."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(403, text="<html>Checking your browser cf-chl-  </html>")

    _transport(handler)
    with pytest.raises(BlockedError, match="cloudflare"):
        await fetch_mod.fetch(URL, Config(use_cache=False))


def test_thin_body_triggers_escalation():
    resp = fetch_mod.Response(url=URL, status=200, html="<html><body></body></html>", via="http")
    assert fetch_mod.needs_browser(resp, Config()) is True


def test_full_page_does_not_escalate():
    resp = fetch_mod.Response(url=URL, status=200, html=fixture("detail.html"), via="http")
    assert fetch_mod.needs_browser(resp, Config()) is False
