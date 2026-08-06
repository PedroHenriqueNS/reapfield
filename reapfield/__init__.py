"""reapfield -- a URL and a field spec in, structured JSON out.

`scrape()` is the whole public API and the single core both entry points sit on.
cli.py and mcp_server.py are thin adapters over it; any behaviour that exists in
only one of them is a bug.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from . import adapters
from . import fetch as fetch_mod
from .cache import ResponseCache, SelectorCache
from .config import Config, load
from .extract import DeriveFn, Extraction, extract
from .spec import Field, build_model, parse_fields

__all__ = ["Config", "Extraction", "Field", "build_model", "load", "parse_fields", "scrape"]

try:
    __version__ = _pkg_version("reapfield")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0.0.0+unknown"


def _default_deriver(cfg: Config) -> DeriveFn:
    from .extract.derive import make_deriver  # imports anthropic; keep it lazy

    return make_deriver(cfg.model)


def _next_page(html: str, url: str, selector: str | None) -> str | None:
    if not selector:
        return None
    node = LexborHTMLParser(html).css_first(selector)
    href = node.attributes.get("href") if node is not None else None
    return urljoin(url, href) if href else None


async def scrape(
    url: str,
    fields: str | list[Field],
    cfg: Config | None = None,
    *,
    cache: SelectorCache | None = None,
    derive: DeriveFn | None = None,
) -> Extraction:
    """Fetch and extract. Partial results are results -- see `Extraction.misses`."""
    cfg = cfg or load()
    parsed = parse_fields(fields) if isinstance(fields, str) else list(fields)
    cache = cache or SelectorCache()
    derive = derive or _default_deriver(cfg)

    # 1. An official API beats scraping every time.
    adapters.guard(url)
    if adapter := adapters.lookup(url):
        detected = cfg.mode if cfg.mode != "auto" else "many"
        return Extraction(await adapter(url, parsed), detected)

    responses = ResponseCache(ttl=cfg.cache_ttl, enabled=cfg.use_cache)
    resp = await fetch_mod.fetch(url, cfg, responses)
    result = await extract(resp.html, url, parsed, cfg, cache, derive)

    # Nothing came back from static HTML -- the page probably renders under JS.
    found = any(v is not None for rec in result.records for v in rec.values())
    if not found and fetch_mod.needs_browser(resp, cfg, found_fields=False):
        resp = await fetch_mod.escalate(url, cfg, responses)
        result = await extract(resp.html, url, parsed, cfg, cache, derive)

    if cfg.paginate and result.mode == "many":
        result = await _paginate(url, resp.html, parsed, cfg, cache, derive, responses, result)

    return result


async def _paginate(url, html, parsed, cfg, cache, derive, responses, result) -> Extraction:
    """Follow the configured next-page link, within this one --many run only."""
    selector = cfg.for_domain(urlsplit(url).netloc).pagination
    records, calls, page_url = list(result.records), result.llm_calls, url

    for _ in range(cfg.paginate):
        nxt = _next_page(html, page_url, selector)
        if not nxt:
            break
        resp = await fetch_mod.fetch(nxt, cfg, responses)
        page = await extract(resp.html, nxt, parsed, cfg, cache, derive)
        if not page.records:
            break
        records.extend(page.records)
        calls += page.llm_calls
        html, page_url = resp.html, nxt

    return Extraction(records, result.mode, result.misses, calls)
