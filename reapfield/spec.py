"""The `--fields` string -> typed Field list -> pydantic model.

Field names are arbitrary and unbounded. Nothing here knows what "price" means;
it only knows the declared *type*. See SPEC.md, "Invariant".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, create_model

TYPES: dict[str, type] = {"str": str, "float": float, "int": int, "bool": bool}


@dataclass(frozen=True)
class Field:
    name: str  # normalized snake_case
    type: type
    raw: str  # as typed, for error messages


def _snake(s: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s.strip())
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    return re.sub(r"_+", "_", s).strip("_").lower()


def parse_fields(spec: str) -> list[Field]:
    """'title, price:float, in-stock:bool' -> [Field(...), ...]"""
    fields: list[Field] = []
    seen: set[str] = set()
    for part in spec.split(","):
        raw = part.strip()
        if not raw:
            continue
        name_part, _, type_part = raw.partition(":")
        type_part = type_part.strip().lower()
        if type_part and type_part not in TYPES:
            raise ValueError(
                f"unknown type {type_part!r} in {raw!r}; use one of {', '.join(TYPES)}"
            )
        name = _snake(name_part)
        if not name:
            raise ValueError(f"empty field name in {raw!r}")
        if name in seen:
            raise ValueError(f"duplicate field {name!r}")
        seen.add(name)
        fields.append(Field(name=name, type=TYPES.get(type_part, str), raw=raw))
    if not fields:
        raise ValueError("--fields is empty")
    return fields


def build_model(fields: list[Field]) -> type[BaseModel]:
    """Every field Optional with default None: a miss is data, not a validation error."""
    return create_model(
        "Extracted",
        **{f.name: (f.type | None, None) for f in fields},  # type: ignore[call-overload]
    )


# --- text -> declared type -------------------------------------------------
# Web text is not clean input. "£51.77" must reach a float:float field, or the
# whole tool is useless on real pages. These rules are per-*type*, never
# per-field-name, so the invariant holds.

_NUM = re.compile(r"-?\d[\d,  ]*(?:\.\d+)?")
# Negations checked first: "In stock (22 available)" must not match on "available".
_FALSE = re.compile(
    r"(out of stock|sold out|not available|unavailable|not in stock|\bno\b|\bfalse\b|\boff\b)",
    re.I,
)
_TRUE = re.compile(r"(in stock|available|\byes\b|\btrue\b|\bon\b)", re.I)


def coerce_text(text: str, typ: type) -> Any:
    """Raise ValueError if `text` cannot be read as `typ`.

    A raised ValueError is what the extractor treats as a cache miss -- this
    function is therefore half of the invalidation mechanism.
    """
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        raise ValueError("empty value")

    if typ is str:
        return text

    if typ is bool:
        if _FALSE.search(text):
            return False
        if _TRUE.search(text):
            return True
        raise ValueError(f"cannot read {text!r} as bool")

    m = _NUM.search(text.replace(" ", " "))
    if not m:
        raise ValueError(f"no number in {text!r}")
    cleaned = re.sub(r"[,  ]", "", m.group(0))
    try:
        return int(cleaned) if typ is int else float(cleaned)
    except ValueError as exc:
        raise ValueError(f"cannot read {text!r} as {typ.__name__}") from exc
