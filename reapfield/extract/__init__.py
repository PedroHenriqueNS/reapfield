"""The orchestrator: cheapest path first, then at most one LLM call, then a guard.

Order is deliberate -- every step only handles fields the previous ones missed:
structured data (free) -> config pins (authoritative) -> cached selectors (free)
-> derivation (costs money) -> coercion guard.

The coercion guard is the *entire* invalidation mechanism. There is no TTL on a
selector; a selector is wrong only when it stops producing a value of the
declared type, and that is exactly what `_coerce` detects.

`derive` is injected rather than imported, which is what lets the whole suite run
offline with a stub and no API key.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Literal, cast
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

from ..cache import SelectorCache, SelectorEntry
from ..config import Config, DomainConfig
from ..errors import ModeConflict
from ..reduce import reduce_dom
from ..spec import Field, coerce_text
from . import selectors, structured
from .selectors import ROW_KEY

DeriveFn = Callable[[str, list[Field], str], Awaitable[dict[str, SelectorEntry]]]

NOT_FOUND = "no selector produced a value"
BAD_TYPE = "value did not match its declared type"
NO_LLM = "not on the page and derivation is disabled"
NO_BUDGET = "not on the page and the LLM call budget is spent"


@dataclass
class Extraction:
    records: list[dict]  # always a list; cli.py unwraps when mode == "one"
    mode: Literal["one", "many"] = "one"
    misses: dict[str, str] = dc_field(default_factory=dict)
    llm_calls: int = 0


# --- pieces -----------------------------------------------------------------


def _pin(raw: str, row: str | None) -> SelectorEntry:
    """A config pin is CSS, optionally `selector@attribute`."""
    sel, _, attr = raw.partition("@")
    return SelectorEntry(
        selector=sel.strip(),
        attr=f"@{attr.strip()}" if attr else "text",
        row=row,
        source="config",
    )


def _pinned(dcfg: DomainConfig, fields: list[Field]) -> dict[str, SelectorEntry]:
    row = dcfg.selectors.get(ROW_KEY)
    return {
        f.name: _pin(dcfg.selectors[f.name], row) for f in fields if f.name in dcfg.selectors
    }


def _cached(
    cache: SelectorCache, domain: str, fields: list[Field], mode: str
) -> dict[str, SelectorEntry]:
    found = {}
    for f in fields:
        if (entry := cache.get(domain, f, mode)) is not None:
            found[f.name] = entry
    return found


def _row_of(entries: dict[str, SelectorEntry]) -> str | None:
    return next((e.row for e in entries.values() if e.row), None)


def _apply(tree: LexborHTMLParser, entries: dict[str, SelectorEntry], mode: str) -> list[dict]:
    """Replay selectors. Values come back as raw strings; typing happens later."""
    scope = tree.body or tree.root
    if scope is None or not entries:
        return []

    if mode == "many":
        row = _row_of(entries)
        if not row:
            return []
        out = []
        for node in selectors.rows(scope, row):
            rec = {name: selectors.pick(node, e) for name, e in entries.items()}
            if any(v is not None for v in rec.values()):
                out.append(rec)
        return out

    return [{name: selectors.pick(scope, e) for name, e in entries.items()}]


def _coerce(records: list[dict], fields: list[Field]) -> tuple[list[dict], set[str]]:
    """Type every value. A field whose *every* value fails is a bad selector.

    One row out of twenty failing is dirty data -- that row gets None. All of
    them failing means the selector is pointing at the wrong node, which is the
    signal that re-derivation exists for.
    """
    out: list[dict] = [{f.name: None for f in fields} for _ in records]
    bad: set[str] = set()

    for f in fields:
        tried = failed = 0
        for i, rec in enumerate(records):
            raw = rec.get(f.name)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                continue
            tried += 1
            try:
                out[i][f.name] = coerce_text(str(raw), f.type)
            except ValueError:
                failed += 1
        if tried and failed == tried:
            bad.add(f.name)

    return out, bad


def _merge(base: list[dict], extra: list[dict]) -> list[dict]:
    """Index-aligned merge. Mismatched lengths mean the two paths disagree about
    the page, so the deterministic one wins and the rest become misses."""
    if not extra:
        return base
    if not base:
        return extra
    if len(base) != len(extra):
        return base
    return [
        {**b, **{k: v for k, v in e.items() if v is not None}}
        for b, e in zip(base, extra, strict=True)
    ]


# --- the selector path ------------------------------------------------------


async def _by_selector(
    html: str,
    tree: LexborHTMLParser,
    domain: str,
    fields: list[Field],
    cfg: Config,
    dcfg: DomainConfig,
    cache: SelectorCache,
    derive: DeriveFn,
    mode_hint: str | None,
    budget: int,
) -> tuple[list[dict], str, int, dict[str, str]]:
    """Config pins -> cache -> derive -> coercion guard -> maybe derive once more."""
    misses: dict[str, str] = {}
    calls = 0
    pins = _pinned(dcfg, fields)

    # Resolve the Optional once. Everything below this line uses a real mode,
    # which is what lets the cache key, _apply and _remember all agree.
    mode: str = mode_hint or "one"
    guessed = mode_hint is None

    if cfg.refresh:
        for f in fields:
            if f.name not in pins:  # --refresh must never touch a hand-pinned selector
                cache.drop(domain, f, "one")
                cache.drop(domain, f, "many")

    # Cardinality from the cache: probe `many` first, since mode is part of the key.
    entries: dict[str, SelectorEntry] = {}
    if guessed:
        many = _cached(cache, domain, fields, "many")
        row = _row_of(many)
        scope = tree.body or tree.root
        if row and scope is not None and len(selectors.rows(scope, row)) >= 2:
            mode, entries, guessed = "many", many, False

    if not entries:
        entries = _cached(cache, domain, fields, mode)
    entries.update(pins)  # a pin outranks whatever the cache learned

    missing = [f for f in fields if f.name not in entries]
    if missing:
        if cfg.no_llm:
            misses.update({f.name: NO_LLM for f in missing})
        elif calls >= budget:
            misses.update({f.name: NO_BUDGET for f in missing})
        else:
            # "auto" only when we defaulted to `one` without evidence -- then the
            # model's `row` is allowed to flip us to a listing.
            derived = await derive(reduce_dom(html), missing, "auto" if guessed else mode)
            calls += 1
            if _row_of(derived):
                if cfg.mode == "one":
                    raise ModeConflict(
                        "this page lists multiple records; --one would silently keep only the first"
                    )
                if guessed:
                    mode = "many"
            _remember(cache, domain, fields, mode, derived, entries)

    records, bad = _coerce(_apply(tree, entries, mode), fields)

    # A selector that matched nothing and one that matched the wrong node are the
    # same event -- the page moved. Both, and only these, trigger re-derivation.
    redo = _stale(fields, entries, records, bad, pins)
    if redo and not cfg.no_llm and calls < budget:
        for f in redo:
            cache.drop(domain, f, mode)
            entries.pop(f.name, None)
        derived = await derive(reduce_dom(html), redo, mode)
        calls += 1
        _remember(cache, domain, fields, mode, derived, entries)
        records, bad = _coerce(_apply(tree, entries, mode), fields)

    misses.update({name: BAD_TYPE for name in bad})
    return records, mode, calls, misses


def _stale(
    fields: list[Field],
    entries: dict[str, SelectorEntry],
    records: list[dict],
    bad: set[str],
    pins: dict[str, SelectorEntry],
) -> list[Field]:
    """Fields whose cached selector has stopped working.

    A pin is never stale -- it is a human decision, and --refresh must not touch
    it either. A field with no entry at all is not stale; it was never learned.
    """
    return [
        f
        for f in fields
        if f.name in entries
        and f.name not in pins
        and (f.name in bad or all(r.get(f.name) is None for r in records))
    ]


def _remember(
    cache: SelectorCache,
    domain: str,
    fields: list[Field],
    mode: str,
    derived: dict[str, SelectorEntry],
    entries: dict[str, SelectorEntry],
) -> None:
    by_name = {f.name: f for f in fields}
    for name, entry in derived.items():
        if name not in by_name:
            continue  # the model invented a field; ignore it
        entries[name] = entry
        cache.put(domain, by_name[name], mode, entry)


# --- entry point ------------------------------------------------------------


async def extract(
    html: str,
    url: str,
    fields: list[Field],
    cfg: Config,
    cache: SelectorCache,
    derive: DeriveFn,
) -> Extraction:
    tree = LexborHTMLParser(html)
    domain = urlsplit(url).netloc
    dcfg = cfg.for_domain(domain)

    struct_recs, struct_mode = structured.extract(html, fields)

    forced = cfg.mode if cfg.mode in ("one", "many") else None
    if forced == "one" and struct_mode == "many":
        raise ModeConflict(
            f"{url} is a listing of multiple records; --one would silently keep only the first"
        )

    filled = {f.name for f in fields if any(r.get(f.name) is not None for r in struct_recs)}
    remaining = [f for f in fields if f.name not in filled]

    if not remaining:
        records, bad = _coerce(struct_recs, fields)
        mode = forced or struct_mode or "one"
        return Extraction(records, mode, {n: BAD_TYPE for n in bad}, 0)

    sel_recs, mode, calls, misses = await _by_selector(
        html, tree, domain, remaining, cfg, dcfg, cache, derive,
        forced or struct_mode, cfg.max_llm_calls,
    )
    # sel_recs came back already coerced -- typing it twice would be wasted work.
    if forced == "one" and mode == "many":
        raise ModeConflict(
            f"{url} is a listing of multiple records; --one would silently keep only the first"
        )

    struct_typed, struct_bad = _coerce(struct_recs, fields)
    records = _merge(struct_typed, sel_recs) if struct_typed else sel_recs
    misses.update({n: BAD_TYPE for n in struct_bad})

    if not records:
        records = [{f.name: None for f in fields}]
    for f in fields:
        if all(r.get(f.name) is None for r in records):
            misses.setdefault(f.name, NOT_FOUND)
        else:
            misses.pop(f.name, None)

    # `mode` here is _by_selector's plain-str return (by the brief's own design --
    # threading Literal through _apply/_remember/the cache key is scope creep for
    # this task), so the checker can't see it's always "one"/"many". It is.
    return Extraction(records, cast(Literal["one", "many"], forced or mode), misses, calls)
