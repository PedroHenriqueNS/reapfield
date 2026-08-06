# reapfield

[![CI](https://github.com/PedroHenriqueNS/reapfield/actions/workflows/ci.yml/badge.svg)](https://github.com/PedroHenriqueNS/reapfield/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

*(No PyPI badge yet — nothing is published there. See [Install](#install).)*

Give it a URL and a plain-language field spec, get structured JSON back — including on
pages that only render under JavaScript.

```console
$ git clone https://github.com/PedroHenriqueNS/reapfield.git && cd reapfield
$ uv sync
$ export ANTHROPIC_API_KEY=sk-...
$ uv run reapfield https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html \
    --fields "title, price:float, in_stock:bool"
reapfield: mode=one records=1 llm_calls=1
{
  "title": "A Light in the Attic",
  "price": 51.77,
  "in_stock": true
}
```

That first run cost one LLM call — this page has no JSON-LD or other structured data, so
`reapfield` had to derive selectors. Run the identical command again and it's free:

```console
$ uv run reapfield https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html \
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
`llm_calls=1` → `llm_calls=0` transition above is the whole design in one line.

Before it ever considers an LLM it tries the free paths in order: JSON-LD, OpenGraph and
`<meta>`, `__NEXT_DATA__`, `__NUXT__`, inline JSON, then config-pinned selectors, then the
cache. Plenty of pages never reach the paid step at all.

**Not a goal:** defeating anti-bot systems. When a site refuses automated access,
`reapfield` names the system and stops.

## Install

From source — this is the path that actually works today:

```console
git clone https://github.com/PedroHenriqueNS/reapfield.git
cd reapfield
uv sync
uv run playwright install chromium   # only needed for JS-rendered pages
export ANTHROPIC_API_KEY=sk-...      # only needed the first time a domain is scraped
```

Everything below assumes you're in that cloned directory, running commands with `uv run`.

**Not yet on PyPI.** `pip install reapfield`, `uv tool install reapfield` and
`uvx reapfield` are the intended install path once a release ships, but nothing is
published there yet — the commands above are correct on first release and dead until then.

## Usage

```
reapfield <url> --fields "title, price:float, in_stock:bool"
                [--format json|jsonl|csv]  [--one | --many]
                [--strict] [--refresh] [--no-llm] [--max-llm-calls N]
                [--no-cache] [--cache-ttl SECONDS]
                [--scroll N] [--paginate N] [--allow-private]
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

Same core as the CLI, exposed over stdio. Run this from the cloned project directory so
`uv run` resolves to this checkout:

```console
claude mcp add reapfield --scope local -- uv run reapfield-mcp
```

Tools: `scrape`, `list_cached_selectors`, `refresh_selectors`, `prepare_issue_report`. The
last one drafts a bug report about reapfield itself and returns a link — it never submits;
a human opens the link.

CLI URLs come from you; MCP URLs come from a model that may be acting on text it read off a
web page. That is why **private, loopback and link-local addresses are blocked by default
on the CLI too, not only over MCP** — including on redirects. A redirect target is chosen
by the server you were pointed at, not by whoever typed the original URL, so it earns no
more trust: redirects are followed by hand, capped at 5 hops, with this check, robots.txt,
rate limiting and the gated-platform guard re-applied at **every** hop. `169.254.169.254`,
the cloud metadata endpoint, is the case that matters most. Checking the **resolved IP**
rather than the hostname string is what stops DNS rebinding.

Opt out with `--allow-private` on the CLI (for scraping `localhost` or a LAN host during
development) or `REAPFIELD_MCP_ALLOW_PRIVATE=1` for MCP.

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

[contribute]
reports = "ask"                # ask | never -- see below
```

Pinned selectors live in a separate store from the cache, so `--refresh` can never
overwrite a decision you made by hand. Append `@attribute` to read one: `"h3 a@title"`.

The MCP server can invite a connected agent to draft a bug report about your session (see
`prepare_issue_report` above). `[contribute] reports = "never"`, or the equivalent
`REAPFIELD_ISSUE_REPORTS=never` env var, turns that off entirely; the env var wins if both
are set. Default is `"ask"`.

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
