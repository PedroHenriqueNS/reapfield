# AGENTS.md

Guidance for AI coding agents working in the reapfield repository.

**Everything you need is in [CONTRIBUTING.md](CONTRIBUTING.md).** Read it before
changing anything. It is self-contained.

## The short version

```bash
uv sync                    # install
uv run pytest              # offline: no network, no API key needed
uv run ruff check .        # lint
uv run pyright reapfield   # types
```

## Two rules that override anything else you infer

1. **No code path may special-case a field name.** No `KNOWN_FIELDS` table, no
   per-field heuristics. `test_field_names_are_arbitrary` enforces it.
2. **Never add anti-bot evasion.** No fingerprint spoofing, CAPTCHA solving,
   header randomisation, or proxy rotation. When a site refuses, reapfield stops.

## Commits

Conventional Commits are mandatory — release-please generates the changelog from
them. See CONTRIBUTING.md for the full bump table.

---

*Using reapfield as a tool rather than editing it? The MCP server sends the
contribution protocol in its `instructions` at connection time.*
