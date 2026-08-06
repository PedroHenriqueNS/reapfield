# Adapters

`reapfield` ships the adapter seam and **zero adapters**. This page explains why, and
what to use instead for the platforms people most often ask about.

## Why no adapters ship

An adapter is a promise to keep up with someone else's API. Shipping one means owning its
auth flow, its rate limits, and its deprecations forever. The seam costs nothing; the
adapters cost maintenance we have not committed to.

## Registering one

```python
from reapfield import adapters
from reapfield.spec import Field

async def fetch_widgets(url: str, fields: list[Field]) -> list[dict]:
    ...  # call the official API, return one dict per record

adapters.register("widgets.example.com", fetch_widgets)
```

A registered adapter handles the URL **end to end** — the fetch layer and the whole
extraction pipeline are skipped. Registration on an apex domain covers its subdomains.

## Gated platforms

These refuse anonymous access, and their logged-out HTML is a consent banner rather than
content. `reapfield` fails immediately on them and names the official API instead of
scraping. There is no flag to override this.

| Domain | Use instead | Credentials |
|---|---|---|
| `instagram.com` | Instagram Graph API | Yes — Meta app + business account |
| `facebook.com` | Meta Graph API | Yes — Meta app review |
| `linkedin.com` | LinkedIn Marketing / Talent APIs | Yes — partner approval |
| `x.com`, `twitter.com` | X API v2 | Yes — paid tier for most endpoints |

If you have credentials, write an adapter and register it; the guard steps aside as soon
as one is registered for that domain.

## Platforms with genuinely public endpoints

These need no adapter and no credentials — they are listed because reaching for a scraper
here is the wrong first move.

| Site | Public endpoint |
|---|---|
| Reddit | append `.json` to any listing or comments URL |
| Hacker News | `https://hn.algolia.com/api/v1/search` |
| Wikipedia | `https://<lang>.wikipedia.org/w/api.php` |
| GitHub | `https://api.github.com` (60 req/h anonymous) |
| npm | `https://registry.npmjs.org/<package>` |
| Most e-commerce product pages | already carry JSON-LD, which step 2 reads for free |

That last row is the common case: run `reapfield` first and check `llm_calls` in the
stderr line. If it says `0`, the page handed over structured data and no adapter, no
selector and no LLM call was ever needed.
