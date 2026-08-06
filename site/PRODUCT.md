# Product

<!-- impeccable:product-schema 1 -->

Durable product truth for `reapfield`, captured to keep future design work from
re-deriving or inventing it. Visual decisions do not belong here.

Located at `site/PRODUCT.md` by the maintainer's decision rather than the repo
root: the root is public and shipped, and a top-level PRODUCT.md would overlap
the private `docs/PRD.md` and risk the two drifting.

## Platform

web

## Users

**Primary: developers** who need structured data from a handful of sites and do
not want to own selector maintenance. They work in a terminal, drive the tool
with `uv run reapfield`, and are the audience future surfaces lead with.

**Secondary: AI agents** that need a scraping capability with a bounded,
predictable cost, served through the stdio MCP server. Important and explicitly
supported, but they do not lead.

The ordering was confirmed by the maintainer. It matches how README.md is
sequenced and how the tool is actually driven today.

## Product Purpose

`reapfield` turns a URL plus a plain-language field spec into structured JSON,
including on pages that only render under JavaScript.

It exists because every one-off scraper gets written twice: once to find the
selectors by hand, and again when the site changes. The second write is the
expensive one. Naive LLM scrapers solve the first write and make the economics
worse, because they send the page to a model on every run, so cost scales with
traffic rather than with change.

Success is a second run on the same domain reporting `llm_calls=0`, a site
change producing exactly one re-derivation for the affected field, and the
offline suite running with no network and no API key.

## Positioning

Pay a language model **once per field per domain** to discover a CSS selector,
cache it, replay it deterministically, and return to the model only when the
selector demonstrably stops working.

The mechanism a neighbouring product could not truthfully copy is the
invalidation rule: there is **no TTL on a selector**. A cached selector is
dropped on exactly two signals, which are the same event seen from two angles.
The selector matched nothing, or the value failed its declared type. The second
is why inline types are part of the field spec at all: `price:float` yielding
`"Add to basket"` is detectable where a bare string is not.

The consequence is that cost scales with how often a site changes, which is the
thing that actually varies, rather than with traffic.

## Operating Context

- Driven from a terminal with `uv run reapfield <url> --fields "..."`, from a
  cloned checkout. Output is `json`, `jsonl`, or `csv`.
- Every run prints a diagnostic line to **stderr**:
  `reapfield: mode={one|many} records={N} llm_calls={N}`, followed by one line
  per miss. Records go to stdout, so the two can be separated.
- Configuration layers `~/.config/reapfield/config.toml` then `./reapfield.toml`
  per key. Selectors pinned in config live in a separate store from the cache,
  so `--refresh` can never overwrite a hand-made decision.
- The MCP server runs over stdio, added to a client with
  `claude mcp add reapfield --scope local -- uv run reapfield-mcp`.
- Politeness is part of the operating context, not a setting: robots.txt is
  obeyed with no override flag, every domain is rate limited, a `Crawl-delay`
  in robots.txt outranks the config, and the user agent is honest and never
  randomised.
- The test suite is offline by construction. No network, no `ANTHROPIC_API_KEY`.

## Capabilities and Constraints

**Confirmed capabilities**

- One public API, `reapfield.scrape()`, with the CLI and MCP server as thin
  adapters over it. Behaviour present in only one of them is a bug.
- Deterministic extraction before any paid step. Verified order in
  `extract/structured.py`: JSON-LD, `__NEXT_DATA__`, `__NUXT__`, inline
  `application/json`, then OpenGraph and `<meta>` merged **last** as the
  lowest-precedence source. Then config-pinned selectors, then cached selectors,
  then at most one batched derivation.
- Four MCP tools: `scrape`, `list_cached_selectors`, `refresh_selectors`,
  `prepare_issue_report`.
- Cardinality detection with `--one` / `--many` overrides. `--one` against a
  page detected as `many` is an error, never a silent first-row pick.
- Fetch escalates httpx to Playwright when configured, when body text is under
  200 characters, or when the deterministic extractors found nothing.

**Hard constraints future work must preserve**

- **No code path may special-case a field name.** No `KNOWN_FIELDS` table, no
  per-field heuristic. `test_field_names_are_arbitrary` enforces it.
- **Never add anti-bot evasion.** No fingerprint spoofing, CAPTCHA solving,
  header randomisation, or proxy rotation. Cloudflare, DataDome and PerimeterX
  are detected, named in the error, and obeyed by stopping. This is permanent,
  not backlog.
- No robots.txt override flag, and no login or session handling for gated
  platforms. Gated platforms with no registered adapter fail immediately naming
  the official API.
- At most **2 LLM derivation calls per run**. Reduced DOM payload capped at
  30 KB. Exhaustion is a miss, not an exception.
- MCP and CLI both block private, loopback and link-local addresses by default,
  re-checked at **every** redirect hop, capped at 5 hops. The check is on the
  **resolved IP**, which is what stops DNS rebinding.
- Python >= 3.12, and `.python-version` is pinned to 3.12 deliberately: uv's
  managed CPython 3.13.12 breaks `cryptography`, which `mcp` imports at load.
- A missing field returns `null` with a reason. Partial results are results,
  never a crash.

**Explicitly undecided**

- Whether `0.1.0` ships as-is or waits for accumulated work.
- Whether any concrete platform adapter is ever shipped. The seam exists; no
  adapters do, and shipping one is a promise to maintain someone else's API.

**Known documentation defects future work must not propagate**

- `README.md` documents an `--one`/`--many` conflict as exit `1`. argparse
  exits `2`, because the flags sit in a mutually exclusive group and argparse
  handles the conflict before the code that would return `1` is reached.
- `README.md` lists OpenGraph and `<meta>` second in the extractor order. It is
  last. See Capabilities above.
- `docs/SUMMARY.md` and `docs/ROADMAP.md` state 91 tests. The suite collects
  **113**.

## Brand Commitments

- Name is `reapfield`, lowercase. Licensed MIT. Canonical repository is
  <https://github.com/PedroHenriqueNS/reapfield>.
- Voice is plain, technical and honest. It states limits directly and does not
  reach for marketing verbs. `README.md` is the copy of record for any surface
  that describes the product, and it names its own hero: the
  `llm_calls=1` to `llm_calls=0` transition is the whole design in one line.
- The project ships a deliberate public/private split. `.gitignore` is
  deny-by-default under `docs/`, with only `docs/adapters.md` un-ignored. **No
  public file may link to a private path**, because such a link resolves to
  nothing in a clean clone.
- An incumbent visual system exists at `site/src/styles/app.css` and is not yet
  recorded in a DESIGN.md. That is a documentation gap, noted here as fact only.

## Evidence on Hand

**Real, verified**

- `README.md` at the repo root: the two-run demonstration, install paths, MCP
  setup, config schema, and politeness statement.
- `113` tests collected, running offline with no network and no API key
  (verified by collection at the time of writing).
- The design is verified end to end against live traffic on
  books.toscrape.com, including the invalidation loop: a deliberately poisoned
  selector was detected by the type-coercion guard, dropped, re-derived in one
  call, and the cache repaired itself.
- `site/` contains a built and verified landing page.

**Absences future work must not fabricate**

- **Nothing is published on PyPI.** `pip install reapfield` does not work today.
- No users, customers, testimonials, case studies, press, adoption numbers, or
  third-party benchmarks exist. None may be invented or implied.
- No performance or cost benchmark exists beyond the `llm_calls` counter that
  the tool itself prints. There is no published dollar figure.
- Only one real-world domain has been exercised, and it is a sandbox with clean
  markup. No claim of broad real-world coverage is supportable.

## Product Principles

1. **Cost scales with change, not traffic.** Anything that reintroduces
   per-run model calls attacks the reason the product exists.
2. **Field names are arbitrary.** The tool has no vocabulary of supported
   fields, and never will.
3. **Blocked means named and stopped.** When a site refuses automated access,
   reapfield says which system refused and halts. It never evades.
4. **Partial results are results.** A missing field is `null` with a reason on
   stderr, never a crash and never a silent omission.
5. **One core, two thin adapters.** The CLI and the MCP server sit on the same
   `scrape()`. Drift between them is a defect, not a feature.

## Accessibility & Inclusion

No product-specific standard has been mandated by the maintainer, so none is
recorded as a requirement.

Current practice on the web surface, established during its build and measured
rather than assumed: WCAG AA contrast for all text, interactive targets at or
above 44x44, keyboard reachability for scrollable regions, and a
`prefers-reduced-motion` path that lands fully composed rather than blank.
Future work should not regress this, but whether it is a binding standard
remains an open decision.
