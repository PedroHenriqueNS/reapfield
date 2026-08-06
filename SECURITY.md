# Security Policy

## Supported Versions

Only the latest minor version receives security updates.

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

## Trust Model

`reapfield` fetches pages you do not control and hands their content to a language model.
Understanding what that does and does not permit is most of this project's security surface.

**Scraped pages are untrusted input.** The reduced DOM of a fetched page is sent to the
Anthropic API to derive CSS selectors. A hostile page can therefore attempt indirect prompt
injection against that call. The blast radius is deliberately narrow: the model's reply is
parsed as JSON and used only as CSS selectors, applied through `selectolax`. Nothing is
executed, no code path evaluates model output, and a malformed selector is treated as a cache
miss. The realistic worst case is wrong or missing data — not code execution.

**The selector cache is disk state derived from untrusted pages.** A hostile page can
influence what gets cached under `~/.cache/reapfield/selectors/`. The type-coercion guard is
what limits this: a cached selector that stops producing a value of the declared type is
dropped and re-derived, so poisoned selectors do not persist silently.

**The response cache stores fetched HTML in plaintext** under `~/.cache/reapfield/responses/`.
If you scrape pages containing sensitive content, that content is on disk until the TTL
expires. Use `--no-cache` for anything you would not want written there.

**Private, loopback and link-local addresses are blocked by default — on the CLI, not only
over MCP.** CLI URLs come from the person at the keyboard, but a URL you typed can still
*redirect* somewhere you did not type — a redirect target is chosen by the server you asked,
not by you. So both the CLI and the MCP server reject non-`http(s)` schemes and any host
whose **resolved** IP is loopback, link-local, or private — checking the resolved address
rather than the hostname string, since a name that resolves to `127.0.0.1` defeats any string
blocklist. `169.254.169.254`, the cloud metadata endpoint, is the case that matters most.

**Every hop of a redirect is re-checked, not just the URL you gave it.** Redirects are
followed by hand rather than left to the HTTP client, capped at 5 hops. Each hop re-runs the
private-address check (when enabled), robots.txt, the gated-platform guard, and rate limiting
before the request is made — a single check against the original URL would let a public page
302 straight to an internal address and walk past every one of those gates.

> `--allow-private` (CLI) and `REAPFIELD_MCP_ALLOW_PRIVATE=1` (MCP) disable the private-address
> check. Both exist for local development — scraping `localhost` or a LAN host. Setting either
> in an environment that fetches URLs it does not fully control (redirects, or MCP URLs
> supplied by a model) re-opens SSRF against your own network.

**Credentials.** `ANTHROPIC_API_KEY` is read from the environment. It is never written to the
cache, never logged, and never included in a prepared issue report.

**Agent-prepared issue reports never submit themselves.** The `prepare_issue_report` MCP tool
renders a report and returns a link; a human authorizes it. This is intentional — an agent
acting on a hostile page must not have a write path to the issue tracker.

## Not vulnerabilities

These are design decisions. Reports about them will be closed with a link here.

- **`reapfield` does not evade anti-bot systems.** Cloudflare, DataDome and PerimeterX are
  detected, named, and obeyed by stopping. Fingerprint spoofing, CAPTCHA solving, header
  randomisation and proxy rotation are out of scope permanently.
- **`reapfield` obeys `robots.txt` and has no override flag.** That it will not fetch a
  disallowed path is the intended behaviour.
- **Scraping a site whose terms forbid it** is the operator's responsibility, not a flaw in
  the tool.

A genuine `robots.txt` **bypass** — a URL that gets fetched despite being disallowed — *is* a
vulnerability. Please report it.

## Reporting a Vulnerability

**Do not open public GitHub issues for security vulnerabilities.**

Report privately through
[GitHub Security Advisories](https://github.com/PedroHenriqueNS/reapfield/security/advisories/new),
including:

- A description of the issue
- Steps to reproduce
- Affected version (`reapfield --version`, or `pip show reapfield`)
- Impact assessment if known

You can expect an acknowledgment within 7 days. We will work with you to understand and
resolve the issue, and credit you in the release notes if desired.
