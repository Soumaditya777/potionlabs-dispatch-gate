"""Load and normalise the gate register.

The input is whatever a plant's register export looks like: a list of rows plus
optional column metadata.  Only the configured column names are read, so the
same loader works for the next factory if the config maps its columns.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config, ConfigError

TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
PLATE_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Movement:
    row: int
    date: dt.date
    vehicle: str
    party: str
    in_minutes: int
    out_minutes: int | None
    kind: str
    invoice_no: str | None
    chit_no: str | None
    guard: str
    raw: dict

    @property
    def dwell_minutes(self) -> int | None:
        if self.out_minutes is None:
            return None
        return self.out_minutes - self.in_minutes

    @property
    def reference(self) -> str | None:
        return self.invoice_no or self.chit_no


@dataclass(frozen=True)
class Register:
    source: str
    columns: tuple[str, ...]
    movements: tuple[Movement, ...]

    @property
    def is_empty(self) -> bool:
        return not self.movements


def parse_clock(value: object) -> int | None:
    """Return minutes after midnight for 'HH:MM' / 'HH:MM:SS'."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = TIME_RE.match(text)
    if not match:
        raise ConfigError(f"unparseable time {value!r}")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ConfigError(f"out-of-range time {value!r}")
    return hour * 60 + minute


def normalise_plate(value: object) -> str:
    text = str(value or "").strip().upper().replace("-", " ")
    return PLATE_SPACE_RE.sub(" ", text)


def normalise_ref(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_register(path: str | Path, cfg: Config) -> Register:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    # Fixtures served by the assessment gate wrap payloads in {"key","data"}.
    if isinstance(raw, dict) and isinstance(raw.get("data"), dict) and "rows" in raw["data"]:
        raw = raw["data"]
    if not isinstance(raw, dict) or not isinstance(raw.get("rows"), list):
        raise ConfigError(f"{path}: expected an object with a 'rows' array")

    cols = cfg.register.columns
    movements: list[Movement] = []
    for index, row in enumerate(raw["rows"], start=1):
        try:
            date = dt.date.fromisoformat(str(row[cols["date"]]).strip())
            in_minutes = parse_clock(row.get(cols["in_time"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"{path}: row {index} has no usable date/in_time: {exc}") from exc
        if in_minutes is None:
            raise ConfigError(f"{path}: row {index} has an empty in_time")

        out_minutes = parse_clock(row.get(cols["out_time"])) if "out_time" in cols else None
        movements.append(
            Movement(
                row=index,
                date=date,
                vehicle=normalise_plate(row.get(cols["vehicle"])),
                party=str(row.get(cols.get("party", ""), "") or "").strip() or "unknown",
                in_minutes=in_minutes,
                out_minutes=out_minutes,
                kind=str(row.get(cols.get("kind", ""), "") or "unknown").strip().lower(),
                invoice_no=normalise_ref(row.get(cols["invoice_no"])) if "invoice_no" in cols else None,
                chit_no=normalise_ref(row.get(cols["chit_no"])) if "chit_no" in cols else None,
                guard=str(row.get(cols.get("guard", ""), "") or "unknown").strip(),
                raw=row,
            )
        )

    return Register(
        source=str(raw.get("source", "")),
        columns=tuple(str(c) for c in raw.get("columns", [])),
        movements=tuple(movements),
    )
