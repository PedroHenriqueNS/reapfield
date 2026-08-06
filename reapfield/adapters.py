"""The seam for official-API adapters. Ships the seam and zero adapters.

Scraping the logged-out HTML of a gated platform is both a terms violation and a
bad engineering trade -- the markup is hostile and the official API is right
there. So those domains fail loudly and name the API instead.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from .errors import GatedPlatform
from .spec import Field

Adapter = Callable[[str, list[Field]], Awaitable[list[dict]]]

REGISTRY: dict[str, Adapter] = {}

# Login-gated platforms. Scraping these logged out is never the right answer.
GATED = {
    "instagram.com": "the Instagram Graph API",
    "linkedin.com": "the LinkedIn Marketing/Talent APIs",
    "x.com": "the X API v2",
    "twitter.com": "the X API v2",
    "facebook.com": "the Meta Graph API",
}


def register(domain: str, fn: Adapter) -> None:
    REGISTRY[domain.lower().removeprefix("www.")] = fn


def _domains(url: str):
    """Yield host then each parent, so a registration on the apex covers www."""
    host = urlsplit(url).netloc.lower().split(":")[0]
    parts = host.split(".")
    for i in range(len(parts) - 1):
        yield ".".join(parts[i:])


def lookup(url: str) -> Adapter | None:
    for domain in _domains(url):
        if adapter := REGISTRY.get(domain):
            return adapter
    return None


def guard(url: str) -> None:
    """Raise if this is a gated platform nobody has written an adapter for."""
    if lookup(url) is not None:
        return
    for domain in _domains(url):
        if api := GATED.get(domain):
            raise GatedPlatform(
                f"{domain} requires authentication; reapfield will not scrape its "
                f"logged-out HTML. Use {api}, and register an adapter with "
                f"reapfield.adapters.register({domain!r}, ...)."
            )
