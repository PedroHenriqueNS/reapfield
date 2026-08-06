"""argparse entry point. Exit codes and stderr warnings are the contract here."""

from __future__ import annotations

import argparse
import asyncio
import sys

from . import __version__, output, scrape
from .config import load
from .errors import ReapfieldError
from .spec import parse_fields

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reapfield",
        description="Scrape a URL into structured JSON from a natural-language field spec.",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"reapfield {__version__}",
    )
    p.add_argument("url")
    p.add_argument(
        "--fields",
        required=True,
        help='comma-separated, optionally typed: "title, price:float, in_stock:bool"',
    )
    p.add_argument("--format", choices=("json", "jsonl", "csv"), default="json")

    card = p.add_mutually_exclusive_group()
    card.add_argument("--one", action="store_true", help="force a single record")
    card.add_argument("--many", action="store_true", help="force a list of records")

    p.add_argument("--strict", action="store_true", help="exit 1 if any field misses")
    p.add_argument("--refresh", action="store_true", help="re-derive cached selectors")
    p.add_argument("--no-llm", action="store_true", help="cache and structured data only")
    p.add_argument("--max-llm-calls", type=int, default=2, metavar="N")
    p.add_argument("--no-cache", action="store_true", help="ignore the response cache")
    p.add_argument("--cache-ttl", type=int, default=3600, metavar="SECONDS")
    p.add_argument("--scroll", type=int, default=0, metavar="N")
    p.add_argument("--paginate", type=int, default=0, metavar="N")
    p.add_argument(
        "--allow-private",
        action="store_true",
        help="DISABLES SSRF PROTECTION: permits fetching private, loopback and "
        "link-local addresses, including redirects to them. For scraping "
        "localhost or a LAN host during development.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        fields = parse_fields(args.fields)
    except ValueError as exc:
        print(f"reapfield: {exc}", file=sys.stderr)
        return EXIT_USAGE

    cfg = load(
        mode="one" if args.one else "many" if args.many else "auto",
        no_llm=args.no_llm,
        max_llm_calls=args.max_llm_calls,
        refresh=args.refresh,
        use_cache=not args.no_cache,
        cache_ttl=args.cache_ttl,
        scroll=args.scroll,
        paginate=args.paginate,
        block_private=not args.allow_private,
    )

    try:
        result = asyncio.run(scrape(args.url, fields, cfg))
    except ReapfieldError as exc:
        print(f"reapfield: {exc}", file=sys.stderr)
        return EXIT_FAIL
    except KeyboardInterrupt:
        return EXIT_FAIL
    except Exception as exc:  # unexpected means a bug in reapfield
        print(
            f"reapfield: unexpected {type(exc).__name__}: {exc}\n"
            "reapfield: this is a bug. Please report it at "
            "https://github.com/PedroHenriqueNS/reapfield/issues",
            file=sys.stderr,
        )
        return EXIT_FAIL

    # The detected mode goes to stderr on every run, so a cardinality flip is
    # visible in logs rather than showing up as a mysteriously reshaped payload.
    print(
        f"reapfield: mode={result.mode} records={len(result.records)} "
        f"llm_calls={result.llm_calls}",
        file=sys.stderr,
    )
    for name, reason in sorted(result.misses.items()):
        print(f"reapfield: {name}: {reason}", file=sys.stderr)

    output.write(result.records, fields, args.format, result.mode)

    extracted = any(v is not None for rec in result.records for v in rec.values())
    if not extracted:
        return EXIT_FAIL
    if args.strict and result.misses:
        return EXIT_FAIL
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
