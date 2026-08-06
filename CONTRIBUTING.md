# Contributing to reapfield

Thanks for looking at the code. This document is self-contained — everything you need to set
up, test, and submit a change is here.

## Setup

```console
uv sync
uv run playwright install chromium   # only needed if you're touching JS-rendered pages
export ANTHROPIC_API_KEY=sk-...      # only needed to learn new selectors
```

## Tests

```console
uv run pytest              # offline: no network, no API key required
uv run ruff check .        # lint
uv run pyright reapfield   # types
```

`pyright` currently reports exactly 2 errors, both in `reapfield/reduce.py` — both are false
positives from `selectolax`'s type stubs, which declare `node.tag` as `str | None` when it is
never `None` in practice. 2 errors is the expected clean state; more than 2 means your change
introduced something.

The test suite stubs the selector-derivation function, so nothing in it can reach the
Anthropic API or the network. If you add a test that needs either, you've probably found the
wrong layer to test at.

### Debugging a failing call for real

The CLI wraps every unexpected exception in a "this is a bug, please report it" message
(`reapfield/cli.py`) so end users never see a raw traceback. That's the wrong behavior when
*you* are the one debugging reapfield itself. Bypass the CLI and call the library function
directly to get the real traceback back:

```console
uv run python -c "
import asyncio
from reapfield import scrape
from reapfield.spec import parse_fields
from reapfield.config import load

fields = parse_fields('title')
cfg = load(mode='auto', no_llm=True, max_llm_calls=0, refresh=False,
           use_cache=False, cache_ttl=3600, scroll=0, paginate=0)
print(asyncio.run(scrape('https://example.com', fields, cfg)))
"
```

Any exception raised inside `scrape()` propagates uncaught here, with the full stack. `python
-m pdb` works the same way if you want to drop into an interactive debugger instead.

## Two rules that are not negotiable

**Field names are arbitrary.** No code path may special-case a field name. There is no
`KNOWN_FIELDS` table, no per-field heuristic, no vocabulary of supported fields. A user
asking for `--fields "reactor_id, coolant_temp_c:float"` must hit exactly the same code path
as `--fields "title, price:float"`. `test_field_names_are_arbitrary` enforces this. A patch
that fails it is wrong, not the test.

**reapfield does not defeat anti-bot systems.** Pull requests adding fingerprint spoofing,
CAPTCHA solving, header randomisation, or proxy rotation to defeat blocks will be closed.
When a site refuses automated access, reapfield names the system and stops. This is
permanent, and stating it here is kinder than arguing it in your rejected PR.

## Conventional Commits

Commit messages must follow [Conventional Commits](https://www.conventionalcommits.org/).
This isn't a style preference — `release-please` parses the commit history to decide whether
a release happens at all, and if so, what version it gets.

| Commit type | Effect |
|---|---|
| `fix:` | patch release |
| `feat:` | minor release |
| `feat!:` or a `BREAKING CHANGE:` footer | minor release while the project is below 1.0.0 (patch/minor conflate pre-1.0; this will become a major release once we cut 1.0.0) |
| `docs:`, `chore:`, `refactor:`, `test:` | no release |

A docs-only or chore-only push produces no release PR — that's expected, not a bug in the
pipeline.

## Architecture orientation

reapfield tries a chain of free extraction paths first — JSON-LD, OpenGraph/`<meta>`,
framework data islands, config-pinned selectors, then the on-disk cache — and only calls an
LLM to derive new CSS selectors when all of those miss. Once a selector is learned it's
cached and replayed deterministically; the LLM is never on the steady-state path. See
[SPEC.md](SPEC.md) for the full design, including the type-coercion guard that drops a
cached selector when the page has moved under it.

## PR process

1. Branch off `main`, named `<type>/<short-kebab-slug>` (e.g. `fix/pagination-off-by-one`),
   using the same type as your primary commit.
2. Open a pull request against `main`.
3. CI must be green — tests, lint, and types all run there. A red check blocks merge.
