"""httpx first; Playwright only when the page genuinely needs a browser.

This module is also where the tool's manners live: robots.txt is obeyed with no
opt-out flag, every domain is rate limited, and a page that says "you are a bot"
ends the run instead of starting an arms race.
"""

from __future__ import annotations

import asyncio
import random
import time
import urllib.robotparser
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

from .cache import ResponseCache
from .config import Config
from .errors import BlockedError, RobotsDisallowed, TerminalHTTPError

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
TERMINAL_STATUS = frozenset({401, 403, 404, 410})
MAX_ATTEMPTS = 3
BACKOFF_BASE = 1.0
THIN_BODY = 200  # under this much body text, suspect the page needs JS
ASSET_ROUTE = "**/*.{png,jpg,jpeg,gif,webp,woff,woff2,mp4}"

# Lowercased needles -> the system they identify. Detection only; never evasion.
BLOCK_MARKERS = {
    "cloudflare": ("cf-browser-verification", "cf-chl-", "checking your browser", "cf_chl_opt"),
    "DataDome": ("datadome", "dd_cookie_test"),
    "PerimeterX": ("perimeterx", "px-captcha", "_pxhd"),
}

# ponytail: process-global, because one CLI run is one process. Per-run state
# would mean threading a Fetcher through every call site for no gain.
_last_hit: dict[str, float] = {}
_domain_locks: dict[str, asyncio.Lock] = {}
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_gate: asyncio.Semaphore | None = None

# Test seam: the suite installs an httpx.MockTransport here so no test touches
# the network. None is httpx's own default, so production takes no branch.
TRANSPORT: httpx.AsyncBaseTransport | None = None


def reset() -> None:
    """Clear the per-process rate-limit, robots and concurrency state."""
    global _gate
    _last_hit.clear()
    _domain_locks.clear()
    _robots.clear()
    _gate = None


@dataclass
class Response:
    url: str
    status: int
    html: str
    via: Literal["http", "browser", "cache"]


def _domain(url: str) -> str:
    return urlsplit(url).netloc


def _concurrency_gate(cfg: Config) -> asyncio.Semaphore:
    global _gate
    if _gate is None:
        _gate = asyncio.Semaphore(cfg.concurrency)
    return _gate


def detect_block(status: int, html: str) -> str | None:
    """The name of the anti-bot system, or None. Detection is where this stops."""
    if status not in (403, 429, 503):
        return None
    low = html.lower()
    for system, needles in BLOCK_MARKERS.items():
        if any(n in low for n in needles):
            return system
    return None


# --- robots.txt -------------------------------------------------------------


async def _robots_for(
    client: httpx.AsyncClient, url: str, ua: str
) -> urllib.robotparser.RobotFileParser | None:
    """None means we could not read robots.txt, which is not a prohibition."""
    domain = _domain(url)
    if domain in _robots:
        return _robots[domain]

    parts = urlsplit(url)
    robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    parser: urllib.robotparser.RobotFileParser | None = None
    try:
        resp = await client.get(robots_url, headers={"User-Agent": ua}, timeout=10.0)
        if resp.status_code < 400:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(resp.text.splitlines())
    except httpx.HTTPError:
        parser = None  # unreachable robots.txt is not a disallow

    _robots[domain] = parser
    return parser


# --- rate limiting ----------------------------------------------------------


async def _wait_turn(url: str, cfg: Config, crawl_delay: float | None) -> None:
    """Config replaces the default; robots.txt is a floor neither can go under.

    SPEC says "the slowest of the default, the config rate_limit, and
    Crawl-delay", but taken literally that makes its own `rate_limit = 2.0`
    example dead config -- 2 req/s is faster than the 1 req/s default, so the
    default would always win. An explicit per-domain rate is a decision and
    overrides the default; Crawl-delay stays a hard floor, which is the part
    that actually protects the target.
    """
    domain = _domain(url)
    dcfg = cfg.for_domain(domain)
    delay = 1.0 / dcfg.rate_limit if dcfg.rate_limit else 1.0
    if crawl_delay:
        delay = max(delay, crawl_delay)

    lock = _domain_locks.setdefault(domain, asyncio.Lock())
    async with lock:
        gap = time.monotonic() - _last_hit.get(domain, 0.0)
        if gap < delay:
            await asyncio.sleep(delay - gap)
        _last_hit[domain] = time.monotonic()


def _backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass  # HTTP-date form; the computed backoff is a fine substitute
    base = BACKOFF_BASE * (2**attempt)
    return base * random.uniform(0.75, 1.25)


# --- the two fetchers -------------------------------------------------------


async def _via_http(client: httpx.AsyncClient, url: str, cfg: Config) -> Response:
    last: httpx.Response | None = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            last = await client.get(
                url, headers={"User-Agent": cfg.user_agent}, timeout=cfg.timeout
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == MAX_ATTEMPTS - 1:
                raise TerminalHTTPError(f"{url}: {exc}") from exc
            await asyncio.sleep(_backoff(attempt, None))
            continue

        if system := detect_block(last.status_code, last.text):
            raise BlockedError(
                f"{_domain(url)} refused automated access ({system}). "
                "reapfield does not work around anti-bot systems."
            )
        if last.status_code in TERMINAL_STATUS:
            raise TerminalHTTPError(f"{url} returned {last.status_code}")
        if last.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS - 1:
            await asyncio.sleep(_backoff(attempt, last.headers.get("Retry-After")))
            continue
        break

    assert last is not None
    return Response(url=str(last.url), status=last.status_code, html=last.text, via="http")


async def _via_browser(url: str, cfg: Config) -> Response:
    from playwright.async_api import async_playwright  # heavy; only when needed

    dcfg = cfg.for_domain(_domain(url))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page(user_agent=cfg.user_agent)

            async def _drop_asset(route) -> None:
                await route.abort()

            # Lighter on us and on the target -- we never look at pixels.
            await page.route(ASSET_ROUTE, _drop_asset)
            # NOT networkidle: the docs discourage it and it hangs on long-poll pages.
            resp = await page.goto(
                url, wait_until="domcontentloaded", timeout=cfg.timeout * 1000
            )
            if dcfg.wait_for:
                try:  # noqa: SIM105 - contextlib.suppress would blur why this is expected
                    await page.wait_for_selector(dcfg.wait_for, timeout=cfg.timeout * 1000)
                except Exception:  # the selector never appearing is a miss, not a crash
                    pass

            for _ in range(cfg.scroll):
                before = await page.evaluate("document.body.scrollHeight")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(700)
                if await page.evaluate("document.body.scrollHeight") == before:
                    break  # height stopped growing; nothing more is coming

            html = await page.content()
            status = resp.status if resp else 200
        finally:
            await browser.close()

    if system := detect_block(status, html):
        raise BlockedError(f"{_domain(url)} refused automated access ({system}).")
    return Response(url=url, status=status, html=html, via="browser")


def needs_browser(resp: Response, cfg: Config, found_fields: bool = True) -> bool:
    """Escalate when the HTML is suspiciously empty or produced nothing."""
    if cfg.for_domain(_domain(resp.url)).fetcher == "browser":
        return True
    if resp.via != "http":
        return False
    from selectolax.lexbor import LexborHTMLParser

    body = LexborHTMLParser(resp.html).body
    text = body.text(strip=True) if body is not None else ""
    return len(text) < THIN_BODY or not found_fields


# --- entry point ------------------------------------------------------------


async def fetch(url: str, cfg: Config, responses: ResponseCache | None = None) -> Response:
    """robots -> rate limit -> httpx (or straight to the browser if configured)."""
    responses = responses or ResponseCache(ttl=cfg.cache_ttl, enabled=cfg.use_cache)
    if (blob := responses.get(url)) is not None:
        return Response(url=blob["url"], status=blob["status"], html=blob["html"], via="cache")

    dcfg = cfg.for_domain(_domain(url))

    async with (
        _concurrency_gate(cfg),
        httpx.AsyncClient(follow_redirects=True, transport=TRANSPORT) as client,
    ):
        parser = await _robots_for(client, url, cfg.user_agent)
        if parser is not None and not parser.can_fetch(cfg.user_agent, url):
            raise RobotsDisallowed(f"robots.txt disallows {url} -- nothing was fetched")
        crawl_delay = parser.crawl_delay(cfg.user_agent) if parser else None

        await _wait_turn(url, cfg, float(crawl_delay) if crawl_delay else None)

        if dcfg.fetcher == "browser":
            resp = await _via_browser(url, cfg)
        else:
            resp = await _via_http(client, url, cfg)

    responses.put(url, resp.status, resp.html, resp.via)
    return resp


async def escalate(url: str, cfg: Config, responses: ResponseCache | None = None) -> Response:
    """Second pass in a real browser, for when the httpx HTML yielded nothing."""
    async with _concurrency_gate(cfg):
        await _wait_turn(url, cfg, None)
        resp = await _via_browser(url, cfg)
    (responses or ResponseCache(ttl=cfg.cache_ttl, enabled=cfg.use_cache)).put(
        url, resp.status, resp.html, resp.via
    )
    return resp
