import asyncio
import socket

import httpx
import pytest
from conftest import fixture

from reapfield import fetch as fetch_mod
from reapfield.config import Config
from reapfield.errors import (
    BlockedError,
    GatedPlatform,
    RobotsDisallowed,
    TerminalHTTPError,
    UnsafeURL,
)

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


# --- redirects ---------------------------------------------------------------
#
# A gate that ran once on the URL the caller supplied protects nothing: the
# server picks the second URL. Each of these proves a gate survives a 302.


def _redirect_to(target: str, *, robots: str = "", seen: list | None = None):
    """A handler that 302s the first page to `target` and records every hit."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots) if robots else httpx.Response(404)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": target})
        return httpx.Response(200, text="<html><body>arrived</body></html>")

    return handler


async def test_redirect_to_private_ip_is_refused(no_sleep, monkeypatch):
    """The metadata-endpoint bypass: a public host 302s to 169.254.169.254."""
    monkeypatch.delenv("REAPFIELD_MCP_ALLOW_PRIVATE", raising=False)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *a, **kw: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34" if host == "books.toscrape.com" else "169.254.169.254", 80),
            )
        ],
    )
    _transport(_redirect_to("http://metadata.example/latest/meta-data/"))

    with pytest.raises(UnsafeURL):
        await fetch_mod.fetch(
            "https://books.toscrape.com/start", Config(use_cache=False, block_private=True)
        )


async def test_redirect_to_disallowed_host_is_refused(no_sleep):
    """The second host's robots.txt is fetched, obeyed, and its content never is."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "books.toscrape.com":
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(302, headers={"Location": "https://closed.example/secret"})
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, text="secret")

    _transport(handler)
    with pytest.raises(RobotsDisallowed):
        await fetch_mod.fetch("https://books.toscrape.com/start", Config(use_cache=False))

    assert "https://closed.example/secret" not in seen


async def test_redirect_to_gated_platform_is_refused(no_sleep):
    _transport(_redirect_to("https://instagram.com/someone"))
    with pytest.raises(GatedPlatform):
        await fetch_mod.fetch("https://books.toscrape.com/start", Config(use_cache=False))


async def test_redirect_chain_is_bounded(no_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        n = int(request.url.params.get("n", 0))
        return httpx.Response(302, headers={"Location": f"/hop?n={n + 1}"})

    _transport(handler)
    with pytest.raises(TerminalHTTPError, match="redirect"):
        await fetch_mod.fetch("https://books.toscrape.com/hop?n=0", Config(use_cache=False))


async def test_ordinary_redirect_still_works(no_sleep):
    """The gates are per hop, not a ban on redirects."""
    _transport(_redirect_to("https://books.toscrape.com/moved"))
    resp = await fetch_mod.fetch("https://books.toscrape.com/start", Config(use_cache=False))

    assert resp.status == 200
    assert resp.url.endswith("/moved")  # the final URL, not the requested one
    assert "arrived" in resp.html


def test_sixtofour_is_blocked():
    """2002::/16 tunnels an arbitrary IPv4 destination straight through."""
    import ipaddress

    assert fetch_mod._blocked(ipaddress.ip_address("2002:7f00:1::1")) is True


def test_thin_body_triggers_escalation():
    resp = fetch_mod.Response(url=URL, status=200, html="<html><body></body></html>", via="http")
    assert fetch_mod.needs_browser(resp, Config()) is True


def test_full_page_does_not_escalate():
    resp = fetch_mod.Response(url=URL, status=200, html=fixture("detail.html"), via="http")
    assert fetch_mod.needs_browser(resp, Config()) is False
