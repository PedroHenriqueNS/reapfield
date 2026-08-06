"""json / jsonl / csv writers. stdlib `csv`, because pandas for this is absurd."""

from __future__ import annotations

import csv
import json
import sys
from typing import IO

from .spec import Field


def write(
    records: list[dict],
    fields: list[Field],
    fmt: str,
    mode: str,
    stream: IO[str] | None = None,
) -> None:
    out = stream or sys.stdout
    names = [f.name for f in fields]

    if fmt == "jsonl":
        for rec in records:
            out.write(json.dumps({k: rec.get(k) for k in names}, ensure_ascii=False) + "\n")
        return

    if fmt == "csv":
        writer = csv.DictWriter(out, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({k: rec.get(k) for k in names} for rec in records)
        return

    # json: a single record for mode="one" is an object, not a one-item array.
    shaped: object
    if mode == "one":
        shaped = {k: records[0].get(k) for k in names} if records else {k: None for k in names}
    else:
        shaped = [{k: rec.get(k) for k in names} for rec in records]
    out.write(json.dumps(shaped, indent=2, ensure_ascii=False) + "\n")
