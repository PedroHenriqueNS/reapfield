# SPEC — `reapfield`

## Context

A general-purpose web scraping CLI: give it a URL and a natural-language field spec, get structured
JSON back, including on pages that only render under JavaScript.

The problem this solves is that every one-off scraper is written twice — once to find the selectors
by hand, once again when the site changes. The design here pays an LLM once per field per domain to
discover selectors, then caches them and replays them deterministically forever after, re-invoking
the LLM only when a cached selector actually stops working. Steady-state cost is zero LLM calls.

Explicit non-goal: this tool does not defeat anti-bot systems. When a site refuses automated
access, it says so and stops.

The repo currently contains this spec and nothing else. Implementation happens in a fresh session
against this document.

## Decisions locked during the design interview

| Area | Decision |
|---|---|
| Field types | Optional inline annotations (`price:float`), unannotated → `str \| None`, coercion failure → `None` + warning |
| Cache key | Per-**field**, not per-spec — so `"title,price"` and `"title,price,rating"` share cache |
| Invalidation | Lazy only. No TTL. Selector matches nothing **or** value fails its declared type → miss → re-derive that field |
| Partial results | Emit with `null`s + per-field reason on stderr. Exit 0 if ≥1 field, 1 if none, 1 if `--strict` and any miss |
| LLM | Anthropic SDK direct, no provider abstraction. Haiku default. ≤2 derivation calls/run. `--no-llm` disables |
| Derivation batching | One call derives all missing fields — the reduced DOM dominates the payload |
| Config | `~/.config/reapfield/config.toml` + optional `./reapfield.toml`, local wins per-key |
| Cache vs config | Separate stores. `--refresh` must never touch a hand-pinned selector |
| Cardinality | Auto-detected per page, with `--one` / `--many` to force; conflict is an error, not a silent truncation |
| MCP | Exposed as a stdio MCP server at Claude Code `local` scope, sharing one core with the CLI |
| Naming | Package, import name and CLI command are all `reapfield`; MCP entry point is `reapfield-mcp` |

---

## Module layout

```
reapfield/
  __init__.py        public API — scrape()
  cli.py             argparse entry point, exit codes, stderr warnings
  spec.py            "--fields" string → list[Field] → pydantic model
  config.py          tomllib load + two-layer merge
  fetch.py           httpx → Playwright escalation, robots, rate limit, retries
  reduce.py          DOM reduction for the LLM payload
  cache.py           SelectorCache (no TTL) + ResponseCache (TTL)
  adapters.py        official-API adapter seam; ships zero adapters
  mcp_server.py      stdio MCP server — thin adapter over the same core as cli.py
  output.py          json / jsonl / csv writers
  errors.py          BlockedError, RobotsDisallowed, TerminalHTTPError, BudgetExceeded
  extract/
    __init__.py      orchestrator + cardinality detection
    structured.py    JSON-LD, OpenGraph/meta, __NEXT_DATA__, __NUXT__, inline application/json
    selectors.py     apply cached CSS selectors via selectolax
    derive.py        the one LLM call
tests/
  fixtures/          saved HTML, robots.txt — no network in the suite
  test_*.py
docs/adapters.md     which platforms need credentials, which expose public endpoints
```

## Core interfaces

```python
# spec.py
@dataclass(frozen=True)
class Field:
    name: str            # normalized to snake_case: "in-stock" → "in_stock"
    type: type           # str | float | int | bool
    raw: str             # as the user typed it, for error messages

def parse_fields(s: str) -> list[Field]
def build_model(fields: list[Field]) -> type[BaseModel]   # pydantic create_model, all Optional
```

> **Invariant — field names are arbitrary and unbounded.** `title`, `price`, `rating`, `in_stock`
> are *examples only*, used here and in the tests because the books.toscrape.com fixtures have them.
> No field name carries special meaning anywhere in the codebase. There is no enum, no
> `KNOWN_FIELDS` table, no per-field heuristic, no vocabulary of "supported" fields. A user asking
> for `--fields "reactor_id, coolant_temp_c:float"` must hit exactly the same code path as
> `--fields "title, price:float"`. The only per-field inputs to behaviour are the parsed `name` and
> `type` from `Field`. Any test that would pass for `price` but fail for `coolant_temp_c` is a bug
> in the implementation, not in the test.

```python
# fetch.py
@dataclass
class Response:
    url: str
    status: int
    html: str
    via: Literal["http", "browser", "cache"]

async def fetch(url: str, cfg: Config) -> Response
```

```python
# cache.py
@dataclass
class SelectorEntry:
    selector: str                                  # CSS
    attr: str                                      # "text" | "@href" | "@content" | ...
    row: str | None                                # repeating container, list mode only
    derived_at: str                                # ISO8601, informational — NOT a TTL
    source: Literal["llm", "config"]

class SelectorCache:                               # ~/.cache/reapfield/selectors/<domain>.json
    def get(self, domain: str, field: Field, mode: str) -> SelectorEntry | None
    def put(self, domain: str, field: Field, mode: str, entry: SelectorEntry) -> None
    def drop(self, domain: str, field: Field, mode: str) -> None
```

Key: `sha256(domain \x00 field.name \x00 field.type.__name__ \x00 mode)[:16]`.
`mode` (`"one"` / `"many"`) is part of the key because a detail-page selector and a
listing-row selector for the same field are different selectors.

```python
# extract/__init__.py
DeriveFn = Callable[[str, list[Field], str], Awaitable[dict[str, SelectorEntry]]]
#            reduced_dom, missing_fields, mode  → per-field entries

@dataclass
class Extraction:
    records: list[dict]              # always a list internally; cli.py unwraps for mode="one"
    mode: Literal["one", "many"]
    misses: dict[str, str]           # field name → human-readable reason
    llm_calls: int

async def extract(html, url, fields, cfg, cache, derive: DeriveFn) -> Extraction
```

`derive` is **injected**, not imported. That is what lets the whole test suite run offline with a
stub and no `ANTHROPIC_API_KEY`.

---

## Extraction pipeline

Cheapest path first. Each step only handles fields still missing.

1. **Adapter check.** `adapters.lookup(url)` — if a domain has a registered official-API adapter,
   it handles the URL end to end and the rest of the pipeline is skipped.
2. **Deterministic structured data** (`extract/structured.py`), in order: JSON-LD `<script
   type="application/ld+json">`, OpenGraph/`<meta>`, `__NEXT_DATA__`, `window.__NUXT__`, other
   inline `application/json`. Matching is a **generic rule, not a lookup table**: flatten the parsed
   object to dotted key paths, normalize both sides (lowercase, strip `-`/`_`/spaces), and match a
   field when it equals the final path segment, preferring the shallowest path on ties. So
   `offers.price` matches a field named `price` by the same rule that `spec.coolant_temp_c` matches
   `coolant_temp_c` — no field-specific knowledge is involved.
3. **Config-pinned selectors** from `[domains."x".selectors]`. Authoritative — never re-derived,
   never overwritten by `--refresh`.
4. **Cached selectors** (`extract/selectors.py`), applied with selectolax.
5. **Derivation** (`extract/derive.py`) — one LLM call for everything still missing.
6. **Coercion guard.** Every value is run through the pydantic model. A field whose value fails its
   declared type is treated as a **miss**, its cache entry is dropped, and step 5 runs once more for
   just that field. This is the entire invalidation mechanism — it is what catches "the selector
   still matches, but the site reshuffled and it now grabs the wrong node".
7. Anything still missing is `null` with a reason in `Extraction.misses`.

### Cardinality detection

The LLM decides cardinality **at derivation time**, and the decision is cached — so replays are
deterministic and the shape only flips when a re-derivation happens.

0. If `--one` / `--many` was passed, that *is* the mode — look up that key only, skip 1–4.
1. JSON-LD `ItemList`, or a top-level array of same-`@type` objects → `many`.
2. Otherwise the cache is probed **`many` first, then `one`** (mode is part of the key, so both are
   tried). If the `many` entries carry a `row` container matching ≥2 nodes each yielding ≥1 field →
   `many`. If it matches 0 or 1 nodes, fall through to the `one` entries.
3. Otherwise the derivation call returns `row: str | null` alongside the field selectors — non-null
   means `many`, with every field selector relative to `row`.
4. Default is `one`.
5. `--one` / `--many` override detection. `--one` against a page detected as `many` is an **error**
   (exit 1), never a silent first-row pick. The detected mode is logged to stderr on every run so a
   flip is visible in logs.

### DOM reduction (`reduce.py`)

Runs before every derivation call. Hard output cap **30 KB**.

- Drop `<script>` (JSON-LD and `__NEXT_DATA__` already went to step 2), `<style>`, `<svg>`,
  `<noscript>`, `<iframe>`, comments.
- Strip all attributes except `id`, `class`, `itemprop`, `data-testid`, `href`, `src` (truncated to
  60 chars), `alt`, `title`.
- Truncate every text node to 120 chars.
- **Collapse repeated siblings** — when a parent has >3 children sharing a tag+class signature, keep
  the first 2 and replace the rest with `<!-- +N more identical -->`. This is the single biggest
  reduction on listing pages, and it is also what makes the row container obvious to the model.
- Drop subtrees with no text and no kept attributes.
- If still over cap after all that, drop the deepest/least-textual subtrees until under.

---

## Fetch layer (`fetch.py`)

**Escalation.** httpx first (`follow_redirects=True`, 20 s timeout). Escalate to Playwright when
config says `fetcher = "browser"`, or `<body>` text is under 200 chars, or steps 2–4 above extracted
zero fields from the httpx HTML.

**Playwright.** Chromium headless. `await page.goto(url, wait_until="domcontentloaded")`, then an
optional config `wait_for` selector, then `page.content()`.
Do **not** use `wait_until="networkidle"` — current Playwright docs mark it discouraged; it hangs on
pages with long-poll or analytics beacons. Block heavy assets:
`await page.route("**/*.{png,jpg,jpeg,gif,webp,woff,woff2,mp4}", lambda r: r.abort())` — faster and
lighter on the target. `--many` mode supports `--scroll N` (scroll to bottom, wait for row count to
stabilize, up to N times) and `--paginate N` (follow the config `pagination` selector up to N pages).

**Retries.** 429 / 500 / 502 / 503 / 504 / timeouts are retryable: exponential backoff, base 1 s,
factor 2, jitter ±25 %, max 3 attempts, `Retry-After` honored when present. 404 / 410 / 401 / 403
are terminal — no retry.

**Rate limiting.** Per-domain token bucket, default 1 req/s. The effective delay is the *slowest* of
the default, the config `rate_limit`, and robots.txt `Crawl-delay`. Global concurrency cap via
`asyncio.Semaphore`, default 4.

**robots.txt.** stdlib `urllib.robotparser.RobotFileParser`. `can_fetch()` false → `RobotsDisallowed`,
exit 1, nothing is fetched. `crawl_delay()` feeds the limiter. There is deliberately no
`--ignore-robots` flag.

**User-Agent.** `reapfield/0.1 (+https://github.com/<owner>/reapfield)` — honest, identifiable,
overridable in config, never randomized.

**Block detection.** A 403 or challenge page carrying Cloudflare / DataDome / PerimeterX markers
raises `BlockedError`, which names the site and the detected system and exits 1. No workaround is
attempted, and none should be added.

**Response cache.** `~/.cache/reapfield/responses/<sha256(url)>.json`. Unlike the selector cache
this one *does* have a TTL — default 1 h, `--cache-ttl`, `--no-cache` — because page content
genuinely changes while a working selector does not.

---

## Config (`config.py`)

`~/.config/reapfield/config.toml`, then `./reapfield.toml` overriding it per-key.
`XDG_CONFIG_HOME` / `XDG_CACHE_HOME` honored when set. Read with stdlib `tomllib`; nothing ever
writes config.

```toml
concurrency = 4
user_agent  = "reapfield/0.1 (+https://github.com/me/reapfield)"

[domains."books.toscrape.com"]
fetcher    = "http"          # auto | http | browser
rate_limit = 2.0             # req/sec
wait_for   = ".product_main" # browser mode only
pagination = "li.next > a"

[domains."books.toscrape.com".selectors]
price = ".price_color"       # pinned: never derived, never refreshed
```

Environment variables: `ANTHROPIC_API_KEY`, `REAPFIELD_LLM_MODEL` (default
`claude-haiku-4-5-20251001`), `REAPFIELD_MCP_ALLOW_PRIVATE`.

## Adapter seam (`adapters.py`)

```python
Adapter = Callable[[str, list[Field]], Awaitable[list[dict]]]
REGISTRY: dict[str, Adapter] = {}
def register(domain: str, fn: Adapter) -> None
def lookup(url: str) -> Adapter | None
```

**Ships zero adapters** — the seam plus `docs/adapters.md`, which lists each platform, its official
API, and whether credentials are required. Domains on a known-gated list (`instagram.com`,
`linkedin.com`, `x.com`, `facebook.com`) with no registered adapter fail immediately with a message
naming the official API to use. The logged-out HTML of those sites is never scraped.

## CLI (`cli.py`, stdlib argparse — no click/typer)

```
reapfield <url> --fields "title, price:float, in_stock:bool"
                [--format json|jsonl|csv]   [--one | --many]
                [--strict] [--refresh] [--no-llm] [--max-llm-calls N]
                [--no-cache] [--cache-ttl SECONDS]
                [--scroll N] [--paginate N] [-v]
```

Exit codes: `0` ≥1 field extracted · `1` zero fields, or `--strict` with any miss, or blocked, or
robots-disallowed, or `--one`/`--many` conflict · `2` usage error.

---

## MCP server (`mcp_server.py`)

Exposes the tool to LLM clients over **stdio**, registered at Claude Code's `local` scope. No port,
no daemon, no network listener — the client spawns it as a subprocess, so this does not violate the
locked "no API server".

```python
from mcp.server import MCPServer     # NB: SDK v2 renamed FastMCP → MCPServer

mcp = MCPServer("reapfield")

@mcp.tool()
async def scrape(url: str, fields: str,
                 mode: Literal["auto", "one", "many"] = "auto",
                 refresh: bool = False) -> ScrapeResult: ...

@mcp.tool()
async def list_cached_selectors(domain: str) -> list[SelectorEntry]: ...

@mcp.tool()
async def refresh_selectors(domain: str, fields: str) -> list[SelectorEntry]: ...

def main() -> None:
    mcp.run()                        # stdio is the default transport
```

Registration:

```bash
claude mcp add reapfield --scope local -- uv run reapfield-mcp
```

`pyproject.toml` ships two entry points over one core:
`reapfield = "reapfield.cli:main"` and `reapfield-mcp = "reapfield.mcp_server:main"`.

**Rules this server must follow:**

- **No duplicated logic.** `cli.py` and `mcp_server.py` are both thin adapters over the same
  `reapfield.scrape()`. Any behaviour that exists in only one of them is a bug.
- **Structured output comes free.** `ScrapeResult` is a pydantic model, so the SDK derives the
  output schema from it. Do not hand-write JSON schemas.
- **Partial extraction is a result, not an error** — return the record with `misses` populated so
  the calling model can see which fields failed and why, and decide what to do. Only
  `BlockedError`, `RobotsDisallowed`, `TerminalHTTPError` and `BudgetExceeded` raise.
- **No `format` parameter.** The client receives structured JSON; handing an LLM a CSV *string* to
  re-parse is strictly worse and adds a code path. `--format` stays CLI-only.
- **Shared fetch layer**, so per-domain rate limits, the concurrency cap, robots.txt and the LLM
  call budget all apply identically. A chatty model cannot stampede a target.

**Trust boundary — this is new and the CLI does not have it.** CLI URLs come from you; MCP URLs come
from a model, which may be acting on text it read from a web page. Therefore `mcp_server.py`
validates before fetching, and rejects: non-`http(s)` schemes (`file://`, `gopher://`, `data:`), and
hosts resolving to loopback, link-local, or RFC1918 private ranges — `169.254.169.254` is the one
that matters. Resolve the host and check the resolved IP, not the string, or DNS rebinding walks
straight past it. `REAPFIELD_MCP_ALLOW_PRIVATE=1` opts out for local development.

## Dependencies — each justified

| Package | Why | Why not stdlib / existing |
|---|---|---|
| `httpx` | async HTTP, connection pooling, redirects, HTTP/2 | `urllib` is sync-only, no pooling |
| `playwright` | JS rendering | no stdlib equivalent |
| `selectolax` | CSS selector engine (Lexbor) | chosen over `lxml`, which needs a *second* package (`cssselect`) for CSS — one dep instead of two |
| `pydantic` | coercion + validation of extracted values; the coercion guard *is* the invalidation rule | hand-rolled per-type coercion is exactly where the bugs would live |
| `anthropic` | LLM client | raw httpx POSTs would work; SDK is kept for typed errors and retry handling |
| `mcp` | official MCP Python SDK — stdio server, tool registration, schema derived from pydantic | hand-rolling the JSON-RPC framing and the MCP handshake is a protocol implementation, not a feature |
| `pytest`, `pytest-asyncio` | tests | — |

**Deliberately not used:** `click`/`typer` (argparse), `platformdirs` (~4 lines of stdlib),
`tomli` (stdlib `tomllib` on 3.11+), `beautifulsoup4`, `requests`, `reppy` (stdlib `robotparser`),
`respx` (httpx ships `MockTransport`), `pandas` (stdlib `csv`).

**Install-time script flag:** none of these run install hooks. `selectolax` is compiled — wheels
exist for common platforms, but an sdist fallback needs a C toolchain. `playwright` requires a
separate explicit `playwright install chromium` (~150 MB); that is a command, not an install hook.
All versions pinned exactly in `uv.lock`.

---

## Out of scope

Anti-bot evasion of every kind — fingerprint spoofing, CAPTCHA-solving services, proxy rotation to
defeat blocks, header randomization to look human. Also: web UI, HTTP API server, job scheduler,
database, login/session handling for gated platforms, distributed crawling, whole-site crawls
(pagination and scroll only apply within a single `--many` run), and concrete platform adapters.

## Verification

**Offline suite** — `uv run pytest`, no network, no `ANTHROPIC_API_KEY`, stubbed `DeriveFn`:

| Test | Asserts |
|---|---|
| `test_structured.py::test_jsonld_hit` | JSON-LD product fixture fills all fields, `llm_calls == 0` |
| `test_selectors.py::test_cached_selector_hit` | pre-seeded cache extracts, `llm_calls == 0` |
| `test_selectors.py::test_cached_miss_rederives` | selector matching 0 nodes → stub derive called once, cache overwritten |
| `test_selectors.py::test_coercion_failure_is_a_miss` | `price:float` yielding `"Add to basket"` drops the entry and re-derives |
| `test_fetch.py::test_429_backoff` | `httpx.MockTransport` returning 429 twice then 200 → 3 attempts, delays increase, `Retry-After` honored |
| `test_fetch.py::test_robots_disallow` | disallowed URL raises `RobotsDisallowed`, transport never called |
| `test_extract.py::test_partial_extraction` | 4 fields / 2 found → 2 values + 2 `null` + 2 entries in `misses`, exit 0; `--strict` → exit 1 |
| `test_extract.py::test_cardinality_detection` | listing fixture → `many` with 20 records; detail fixture → `one`; `--one` on the listing → exit 1 |
| `test_reduce.py::test_reduction` | output under 30 KB and repeated siblings collapsed |
| `test_spec.py::test_field_parsing` | `"title, price:float, in-stock:bool"` → correct names, types, snake_case |
| `test_extract.py::test_field_names_are_arbitrary` | guards the invariant: a fixture whose JSON-LD uses `coolant_temp_c` / `reactor_id` extracts through the identical code path, with no field-specific branch anywhere. Fails if anyone adds a `KNOWN_FIELDS` table or special-cases a name |
| `test_mcp.py::test_tool_wraps_core` | the `scrape` tool returns the same `ScrapeResult` the CLI path produces for the same fixture — catches logic drifting into one adapter |
| `test_mcp.py::test_partial_is_not_an_error` | 2-of-4 extraction returns a result with `misses` populated, does not raise |
| `test_mcp.py::test_url_validation` | `file:///etc/passwd`, `http://127.0.0.1/`, `http://169.254.169.254/` and a hostname *resolving* to a private IP are all rejected before any fetch; `REAPFIELD_MCP_ALLOW_PRIVATE=1` permits them |

Fixtures: saved detail + listing HTML from books.toscrape.com, one JSON-LD product page, one
robots.txt.

**Live smoke run** — must be executed and its *real* output pasted into the PR/session, not asserted:

```bash
uv run reapfield https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html \
  --fields "title, price:float, in_stock:bool"

uv run reapfield https://books.toscrape.com/ \
  --fields "title, price:float" --many --format csv
```

Then re-run the first command and confirm stderr shows a cache hit with zero LLM calls — that is the
whole design working.

**MCP smoke run** — register at local scope and call the tool from Claude Code, pasting the real
result:

```bash
claude mcp add reapfield --scope local -- uv run reapfield-mcp
claude mcp list          # must show reapfield: connected
```

Then, in a Claude Code session, call `scrape` with the same books.toscrape.com detail URL and
confirm the returned object matches the CLI output byte-for-byte on the field values.
