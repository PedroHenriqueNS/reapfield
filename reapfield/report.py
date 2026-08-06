"""Prepare an issue report. Never submit one.

reapfield feeds attacker-controlled page content to a language model. If an
agent acting on that content could also file issues unattended, every scraped
page would gain a write path to the tracker. So this module renders a report
and returns a link -- a human decides.
"""

from __future__ import annotations

import platform
import sys
import urllib.parse
from importlib.metadata import PackageNotFoundError, version

import httpx
from pydantic import BaseModel, Field

from .config import Config, load

REPO = "PedroHenriqueNS/reapfield"
SEARCH_URL = "https://api.github.com/search/issues"
NEW_ISSUE_URL = f"https://github.com/{REPO}/issues/new"
SEARCH_UNAVAILABLE = (
    "> **Duplicate check could not be checked.** GitHub search was unavailable, so this "
    "may already be reported. Please search the tracker before submitting."
)


class IssueReport(BaseModel):
    """The result of preparing a report. Nothing here has been submitted."""

    reports_enabled: bool = True
    duplicate: bool = False
    existing_issue_url: str | None = None
    existing_issue_title: str | None = None
    search_failed: bool = False
    title: str = ""
    body: str = ""
    submit_url: str = Field(
        default="", description="Prefilled GitHub URL. A human must open it."
    )


def _dep(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not installed"


def environment() -> dict[str, str]:
    """Collected here rather than asked of the agent, which would guess.

    Reads no environment variables, so no credential can ride along.
    """
    return {
        "reapfield": _dep("reapfield"),
        "python": f"{platform.python_version()} ({sys.implementation.name})",
        "platform": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "httpx": _dep("httpx"),
        "selectolax": _dep("selectolax"),
        "pydantic": _dep("pydantic"),
        "mcp": _dep("mcp"),
        "anthropic": _dep("anthropic"),
        "playwright": _dep("playwright"),
    }


def render_body(
    summary: str, how_to_reproduce: str, cause: str, suggested_fix: str, *, search_failed: bool
) -> str:
    env = "\n".join(f"| {k} | {v} |" for k, v in environment().items())
    warning = f"\n{SEARCH_UNAVAILABLE}\n" if search_failed else ""
    return f"""{warning}
## Summary

{summary}

## How to reproduce

{how_to_reproduce}

## Cause

{cause}

## Suggested fix

{suggested_fix}

## Environment

| Component | Version |
| --- | --- |
{env}

---

*Prepared by an AI agent that hit this while using reapfield, and verified the fix.
Submitted with human authorization.*
"""


async def _find_duplicate(
    summary: str, client: httpx.AsyncClient
) -> tuple[dict | None, bool]:
    """Returns (issue, search_failed). A failed search is never "no duplicate"."""
    query = f"repo:{REPO} is:issue {summary}"
    try:
        resp = await client.get(
            SEARCH_URL,
            params={"q": query, "per_page": 5},
            headers={"Accept": "application/vnd.github+json"},
            timeout=15.0,
        )
    except httpx.HTTPError:
        return None, True
    if resp.status_code != 200:
        return None, True  # rate limited, validation error -- we do not know
    items = resp.json().get("items") or []
    return (items[0] if items else None), False


async def prepare(
    summary: str,
    how_to_reproduce: str,
    cause: str,
    suggested_fix: str,
    *,
    cfg: Config | None = None,
    client: httpx.AsyncClient | None = None,
) -> IssueReport:
    cfg = cfg or load()
    if cfg.contribute_reports == "never":
        return IssueReport(reports_enabled=False)

    owned = client is None
    client = client or httpx.AsyncClient()
    try:
        existing, search_failed = await _find_duplicate(summary, client)
    finally:
        if owned:
            await client.aclose()

    if existing is not None:
        return IssueReport(
            duplicate=True,
            existing_issue_url=existing.get("html_url"),
            existing_issue_title=existing.get("title"),
        )

    title = summary if len(summary) <= 80 else summary[:77].rstrip() + "..."
    body = render_body(
        summary, how_to_reproduce, cause, suggested_fix, search_failed=search_failed
    )
    params = urllib.parse.urlencode(
        {"title": title, "body": body, "labels": "ai-reported", "template": "ai_agent_report.yml"}
    )
    return IssueReport(
        search_failed=search_failed,
        title=title,
        body=body,
        submit_url=f"{NEW_ISSUE_URL}?{params}",
    )
