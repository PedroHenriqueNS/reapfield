"""stdio MCP server -- a thin adapter over the same scrape() the CLI calls.

The one thing here that the CLI does not have is a trust boundary. CLI URLs come
from the person at the keyboard. MCP URLs come from a model, which may be acting
on text it just read off a web page, so "fetch this URL" is attacker-influenced
input and the server is sitting inside someone's network.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Any, Literal
from urllib.parse import urlsplit

from mcp.server import MCPServer  # SDK v2 renamed FastMCP -> MCPServer
from pydantic import BaseModel
from pydantic import Field as PField

from . import scrape as scrape_core
from .cache import SelectorCache, SelectorEntry
from .config import load
from .errors import UnsafeURL
from .spec import parse_fields

mcp = MCPServer("reapfield")


class ScrapeResult(BaseModel):
    """Pydantic, so the SDK derives the output schema. No hand-written JSON schema."""

    url: str
    mode: Literal["one", "many"]
    records: list[dict[str, Any]]
    misses: dict[str, str] = PField(
        default_factory=dict,
        description="field name -> why it is null. A populated misses is a result, not an error.",
    )
    llm_calls: int = 0


# --- trust boundary ---------------------------------------------------------


def _blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # 169.254.169.254 -- the cloud metadata endpoint
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def check_url(url: str) -> None:
    """Raise UnsafeURL unless this is a public http(s) address.

    Checks the *resolved* IPs, not the hostname string -- a name that resolves to
    127.0.0.1 walks straight past any string-based blocklist, and that is exactly
    how DNS rebinding works.
    """
    if os.environ.get("REAPFIELD_MCP_ALLOW_PRIVATE") == "1":
        return

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURL(f"{parts.scheme or 'that'} URLs are not fetchable; use http or https")
    if not parts.hostname:
        raise UnsafeURL(f"no host in {url!r}")

    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 0, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURL(f"cannot resolve {parts.hostname}: {exc}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _blocked(ip):
            raise UnsafeURL(
                f"{parts.hostname} resolves to {ip}, which is a private, loopback or "
                "link-local address. Set REAPFIELD_MCP_ALLOW_PRIVATE=1 for local development."
            )


# --- tools ------------------------------------------------------------------


@mcp.tool()
async def scrape(
    url: str,
    fields: str,
    mode: Literal["auto", "one", "many"] = "auto",
    refresh: bool = False,
) -> ScrapeResult:
    """Extract structured fields from a web page.

    `fields` is comma-separated with optional inline types, e.g.
    "title, price:float, in_stock:bool". Field names are arbitrary.

    A field that could not be extracted comes back as null with an entry in
    `misses` explaining why -- that is a normal result, not a failure.
    """
    check_url(url)
    cfg = load(mode=mode, refresh=refresh)
    result = await scrape_core(url, fields, cfg)
    return ScrapeResult(
        url=url,
        mode=result.mode,
        records=result.records,
        misses=result.misses,
        llm_calls=result.llm_calls,
    )


@mcp.tool()
async def list_cached_selectors(domain: str) -> list[SelectorEntry]:
    """Show the CSS selectors already learned for a domain. Costs nothing."""
    return [SelectorEntry(**raw) for raw in SelectorCache().all_for(domain).values()]


@mcp.tool()
async def refresh_selectors(domain: str, fields: str) -> list[SelectorEntry]:
    """Forget the cached selectors for these fields so the next scrape re-derives them.

    Returns the entries that were dropped. Re-derivation is lazy by design: it
    needs a page to look at, and it happens on the next scrape of one.
    Config-pinned selectors live in a separate store and are never touched.
    """
    cache = SelectorCache()
    dropped: list[SelectorEntry] = []
    for f in parse_fields(fields):
        for mode in ("one", "many"):
            if (entry := cache.get(domain, f, mode)) is not None:
                dropped.append(entry)
                cache.drop(domain, f, mode)
    return dropped


def main() -> None:
    mcp.run()  # stdio is the default transport


if __name__ == "__main__":
    main()
