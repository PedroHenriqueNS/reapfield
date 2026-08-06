# reapfield

[![CI](https://github.com/PedroHenriqueNS/reapfield/actions/workflows/ci.yml/badge.svg)](https://github.com/PedroHenriqueNS/reapfield/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/reapfield)](https://pypi.org/project/reapfield/)
[![Python](https://img.shields.io/pypi/pyversions/reapfield)](https://pypi.org/project/reapfield/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Give it a URL and a plain-language field spec, get structured JSON back — including on
pages that only render under JavaScript.

```console
$ reapfield https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html \
    --fields "title, price:float, in_stock:bool"
reapfield: mode=one records=1 llm_calls=0
{
  "title": "A Light in the Attic",
  "price": 51.77,
  "in_stock": true
}
```

## The idea

Every one-off scraper gets written twice: once to find the selectors by hand, and again
when the site changes.

`reapfield` pays an LLM **once per field per domain** to discover the CSS selectors, caches
them, and replays them deterministically forever after. It goes back to the LLM only when a
cached selector actually stops working. Steady-state cost is zero LLM calls — that
`llm_calls=0` above is the whole design in one line.

Before it ever considers an LLM it tries the free paths in order: JSON-LD, OpenGraph and
`<meta>`, `__NEXT_DATA__`, `__NUXT__`, inline JSON, then config-pinned selectors, then the
cache. Plenty of pages never reach the paid step at all.

**Not a goal:** defeating anti-bot systems. When a site refuses automated access,
`reapfield` names the system and stops.

## Install

```console
uv tool install reapfield      # or: pip install reapfield
uvx reapfield --version        # no install at all
uv run playwright install chromium   # only needed for JS-rendered pages
export ANTHROPIC_API_KEY=sk-...      # only needed to learn new selectors
```

## Usage

```
reapfield <url> --fields "title, price:float, in_stock:bool"
                [--format json|jsonl|csv]  [--one | --many]
                [--strict] [--refresh] [--no-llm] [--max-llm-calls N]
                [--no-cache] [--cache-ttl SECONDS]
                [--scroll N] [--paginate N] [-v]
```

Types are optional and inline: `price:float`, `in_stock:bool`, `count:int`. Unannotated
fields are strings. **Field names are arbitrary** — `--fields "reactor_id, coolant_temp_c:float"`
takes exactly the same code path as `title, price`.

Exit codes: `0` at least one field extracted · `1` nothing extracted, or `--strict` with any
miss, or blocked, or robots-disallowed, or an `--one`/`--many` conflict · `2` usage error.

A field that could not be found comes back as `null`, with the reason on stderr. That is a
result, not a crash.

## How invalidation works

There is no TTL on a selector. A selector that still works is still correct, and expiring
it just buys LLM calls.

A cached selector is dropped and re-derived when it **matches nothing**, or when the value
it returns **fails the declared type** — `price:float` suddenly yielding `"Add to basket"`
means the page moved under us. That second case is the interesting one: nothing 404'd and
nothing expired, the selector simply started pointing at the wrong node.

## MCP server

Same core as the CLI, exposed over stdio:

```console
claude mcp add reapfield --scope local -- uv run reapfield-mcp
```

Tools: `scrape`, `list_cached_selectors`, `refresh_selectors`.

The MCP path adds one thing the CLI does not have. CLI URLs come from you; MCP URLs come
from a model that may be acting on text it read off a web page. So the server rejects
non-`http(s)` schemes and any host whose **resolved IP** is loopback, link-local or private
— `169.254.169.254` above all. Checking the resolved address rather than the string is what
stops DNS rebinding. `REAPFIELD_MCP_ALLOW_PRIVATE=1` opts out for local development.

## Config

`~/.config/reapfield/config.toml`, then `./reapfield.toml` overriding it per key.

```toml
concurrency = 4
user_agent  = "reapfield/0.1 (+https://github.com/PedroHenriqueNS/reapfield)"

[domains."books.toscrape.com"]
fetcher    = "http"           # auto | http | browser
rate_limit = 2.0              # req/sec; robots.txt Crawl-delay is still a floor
wait_for   = ".product_main"  # browser mode only
pagination = "li.next > a"

[domains."books.toscrape.com".selectors]
price = ".price_color"        # pinned: never derived, never touched by --refresh
_row  = "article.product_pod" # the repeating container, for --many
```

Pinned selectors live in a separate store from the cache, so `--refresh` can never
overwrite a decision you made by hand. Append `@attribute` to read one: `"h3 a@title"`.

## Manners

robots.txt is obeyed, with no override flag. Every domain is rate limited, and a
`Crawl-delay` in robots.txt always wins over the config. The user agent is honest and
identifiable, never randomized.

## Development

```console
uv run pytest        # offline: no network, no API key
```

The suite stubs the derivation function, so nothing in it can reach Anthropic or the
network. See `SPEC.md` for the full design and `docs/adapters.md` for the adapter seam.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues go through
[SECURITY.md](SECURITY.md) — please do not open public issues for them.
Release history is in [CHANGELOG.md](CHANGELOG.md).
