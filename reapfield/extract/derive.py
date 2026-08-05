"""The one LLM call: reduced DOM in, CSS selectors out.

All missing fields go in a single request -- the DOM dominates the payload, so
asking for four selectors costs barely more than asking for one.
"""

from __future__ import annotations

import json
import re

from ..cache import SelectorEntry
from ..errors import DerivationFailed
from ..spec import Field

SYSTEM = """You write CSS selectors for scraping. You are given reduced HTML and a list of \
fields. Reply with JSON only -- no prose, no code fences.

Schema:
{"row": "<css for the repeating container, or null>",
 "fields": {"<field_name>": {"selector": "<css>", "attr": "text|@attribute"}}}

Rules:
- "row" is non-null ONLY if the page lists multiple comparable records. When \
non-null, every field selector must be relative to one row.
- The reduced HTML may contain `<!-- +N more identical -->`; that marks repeated \
siblings that were collapsed. It is strong evidence of a listing.
- Prefer short, stable selectors (class or itemprop over long descendant chains).
- Use "attr" to read an attribute instead of text, e.g. {"attr": "@content"}.
- Omit a field entirely if the page genuinely does not contain it. Never guess."""


def _parse(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)  # model wrapped it in prose anyway
        return json.loads(m.group(0)) if m else {}


def build_prompt(reduced_dom: str, fields: list[Field], mode: str) -> str:
    listing = {
        "one": "This page is a single record. Set row to null.",
        "many": "This page lists multiple records. row must be non-null.",
        "auto": "Decide from the HTML whether this is one record or a list.",
    }[mode]
    wanted = "\n".join(f"- {f.name} (type: {f.type.__name__})" for f in fields)
    return f"{listing}\n\nFields:\n{wanted}\n\nHTML:\n{reduced_dom}"


async def derive(
    reduced_dom: str, fields: list[Field], mode: str, *, model: str
) -> dict[str, SelectorEntry]:
    """Returns {field_name: SelectorEntry}. Entry.row carries the container."""
    from anthropic import AnthropicError, AsyncAnthropic

    client = AsyncAnthropic()
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=2000,
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(reduced_dom, fields, mode)}],
        )
    except AnthropicError as exc:
        # A dead API is infrastructure, not a missing field. Say so plainly
        # rather than reporting "not on the page" and sending someone hunting
        # through HTML that was never the problem.
        raise DerivationFailed(f"could not reach the Anthropic API: {exc}") from exc

    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    try:
        data = _parse(text)
    except ValueError:
        return {}

    row = data.get("row") or None
    out: dict[str, SelectorEntry] = {}
    for f in fields:
        spec = (data.get("fields") or {}).get(f.name)
        if not isinstance(spec, dict) or not spec.get("selector"):
            continue
        out[f.name] = SelectorEntry(
            selector=str(spec["selector"]),
            attr=str(spec.get("attr") or "text"),
            row=row,
            derived_at=SelectorEntry.now(),
            source="llm",
        )
    return out


def make_deriver(model: str):
    """Bind the model, yielding the DeriveFn signature the orchestrator wants."""

    async def _fn(reduced_dom: str, fields: list[Field], mode: str):
        return await derive(reduced_dom, fields, mode, model=model)

    return _fn
