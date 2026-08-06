"""stdio MCP server -- a thin adapter over the same scrape() the CLI calls.

The one thing here that the CLI does not have is a trust boundary. CLI URLs come
from the person at the keyboard. MCP URLs come from a model, which may be acting
on text it just read off a web page, so "fetch this URL" is attacker-influenced
input and the server is sitting inside someone's network.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server import MCPServer  # SDK v2 renamed FastMCP -> MCPServer
from pydantic import BaseModel
from pydantic import Field as PField

from . import scrape as scrape_core
from .cache import SelectorCache, SelectorEntry
from .config import Config, load
from .fetch import check_url
from .report import IssueReport, prepare
from .spec import parse_fields

PROTOCOL = """\
## Contributing back to reapfield

If you hit a genuine bug in reapfield itself while doing real work, and you
worked out a fix, you may offer it upstream. Follow this exactly:

1. Only report what you actually hit and actually fixed. No speculative reports.
2. Call `prepare_issue_report` first. It checks whether the issue has already
   been reported; if it returns `duplicate: true`, stop -- do not report again.
3. Stop and ask the user for authorization before anything is submitted. You
   cannot submit; the tool returns a link and a human opens it.
4. The report must contain: Summary, How to reproduce, Cause, Suggested fix.
   The tool collects the environment itself -- do not guess it.
5. Never put credentials, private URLs, or personal data in a report. Scraper
   reproduction steps often contain both.
"""

OPTED_OUT = """\
## Contributing back to reapfield

The user has opted out of issue reports. Do not ask them, and do not call
`prepare_issue_report` -- it will refuse.
"""


def build_instructions(cfg: Config) -> str:
    body = OPTED_OUT if cfg.contribute_reports == "never" else PROTOCOL
    return (
        "reapfield extracts structured data from web pages. Give it a URL and a "
        "field spec; it returns JSON. Field names are arbitrary.\n\n"
        "Partial extraction is a normal result: missing fields come back as null "
        "with an entry in `misses` explaining why. That is not an error.\n\n"
        f"{body}"
    )


mcp = MCPServer("reapfield", instructions=build_instructions(load()))


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
#
# check_url lives in fetch.py, because that is the layer that knows which URLs
# are really about to be requested -- a redirect target included. It is called
# here too, eagerly, so an obviously unsafe URL is refused before any setup; and
# `block_private=True` below is what makes the fetch layer enforce it per hop.


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
    cfg = load(mode=mode, refresh=refresh, block_private=True)
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


@mcp.tool()
async def prepare_issue_report(
    summary: str,
    how_to_reproduce: str,
    cause: str,
    suggested_fix: str,
) -> IssueReport:
    """Prepare a bug report about reapfield itself. Does NOT submit it.

    Call this only when you hit a real bug in reapfield during real work and
    have verified a fix. It searches existing issues first; if `duplicate` is
    true, stop. Otherwise show the user `body` and ask for authorization -- they
    open `submit_url` themselves.

    Never include credentials, private URLs, or personal data.
    """
    return await prepare(summary, how_to_reproduce, cause, suggested_fix)


def main() -> None:
    mcp.run()  # stdio is the default transport


if __name__ == "__main__":
    main()
