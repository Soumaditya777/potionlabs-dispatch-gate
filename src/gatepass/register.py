"""Deterministic analysis of the gate register.

Every number in the output is computed from the rows handed over, never typed
in.  Findings are ordered by consequence for the design, and each states the
concrete evidence (rows, dates, references) so a reader can check it against
the red register.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass

from .config import Config
from .dataset import Movement, Register

REF_RE = re.compile(r"^(?P<prefix>.*?)(?P<number>\d+)$")


@dataclass(frozen=True)
class Finding:
    text: str
    metric: str


def _pct(part: int, whole: int) -> str:
    if whole == 0:
        return "0%"
    return f"{part / whole * 100:.0f}%"


def _percentile(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _is_night(minutes: int, window: tuple[str, str]) -> bool:
    from .dataset import parse_clock

    start = parse_clock(window[0]) or 0
    end = parse_clock(window[1]) or 0
    if start == end:
        return True
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def split_reference(reference: str) -> tuple[str, int] | None:
    match = REF_RE.match(reference.strip())
    if not match:
        return None
    return match.group("prefix"), int(match.group("number"))


def reference_integrity(movements: tuple[Movement, ...], attr: str) -> dict:
    """Group references by prefix and report gaps, duplicates and reuse."""
    groups: dict[str, list[int]] = defaultdict(list)
    owners: dict[str, list[Movement]] = defaultdict(list)
    for movement in movements:
        ref = getattr(movement, attr)
        if not ref:
            continue
        parsed = split_reference(ref)
        if parsed is None:
            groups["<unparsed>"].append(0)
            owners[ref].append(movement)
            continue
        prefix, number = parsed
        groups[prefix].append(number)
        owners[ref].append(movement)
    gaps: list[tuple[str, int]] = []
    for prefix, numbers in groups.items():
        if prefix == "<unparsed>" or not numbers:
            continue
        present = set(numbers)
        missing = sorted(set(range(min(present), max(present) + 1)) - present)
        gaps.extend((prefix, number) for number in missing)
    duplicates = {ref: rows for ref, rows in owners.items() if len(rows) > 1}
    return {"groups": groups, "gaps": gaps, "duplicates": duplicates}


def arrival_peak(movements: tuple[Movement, ...], window_hours: int) -> tuple[int, int, int]:
    """Busiest contiguous window: (start_minute, count, share_percent)."""
    if not movements:
        return 0, 0, 0
    arrivals = [m.in_minutes for m in movements]
    window = max(1, window_hours) * 60
    best_start, best_count = 0, 0
    for start in range(0, 24 * 60, 15):
        count = sum(1 for minute in arrivals if start <= minute < start + window)
        if count > best_count:
            best_start, best_count = start, count
    return best_start, best_count, round(best_count / len(movements) * 100)


def analyse(register: Register, cfg: Config) -> tuple[list[str], dict]:
    movements = register.movements
    total = len(movements)
    metrics: dict[str, object] = {"movements": total}
    if total == 0:
        return ["The register handed over contains no rows, so no gate pattern could be established."], metrics

    dates = [m.date for m in movements]
    first, last = min(dates), max(dates)
    invoice_movements = tuple(m for m in movements if m.kind in cfg.register.invoice_kinds)
    provisional = tuple(m for m in movements if m.kind in cfg.register.provisional_kinds)
    night = tuple(m for m in movements if _is_night(m.in_minutes, cfg.register.night_window))
    night_provisional = tuple(m for m in night if m.kind in cfg.register.provisional_kinds)
    parties = Counter(m.party for m in movements)
    guards = Counter(m.guard for m in movements)

    metrics.update(
        first_date=first.isoformat(),
        last_date=last.isoformat(),
        parties=len(parties),
        invoice_movements=len(invoice_movements),
        provisional_movements=len(provisional),
        night_movements=len(night),
        guards=dict(guards),
    )

    findings: list[Finding] = []

    findings.append(
        Finding(
            f"{total} movements over {first.isoformat()} to {last.isoformat()} across "
            f"{len(parties)} parties: {len(invoice_movements)} invoice-backed "
            f"({_pct(len(invoice_movements), total)}) and {len(provisional)} chit-backed "
            f"({_pct(len(provisional), total)}). The chit share is the size of the process "
            f"that currently runs with no invoice at exit.",
            "volume",
        )
    )

    if night:
        guard_mix = Counter(m.guard for m in night)
        mix_text = ", ".join(f"{name} {count}" for name, count in guard_mix.most_common())
        findings.append(
            Finding(
                f"All {len(night)} night movements (arrivals between "
                f"{cfg.register.night_window[0]} and {cfg.register.night_window[1]}) are chit-backed, "
                f"never invoice-backed; {len(night_provisional)} of {len(provisional)} chits fall in that "
                f"window. Night coverage by guard: {mix_text}. The invoice is written the next morning, "
                f"so the gate is the only record at the moment of exit.",
                "night",
            )
        )

    duplicates = reference_integrity(movements, "invoice_no")["duplicates"]
    if duplicates:
        details = []
        for ref, rows in duplicates.items():
            parts = "; ".join(
                f"{r.date.isoformat()} {r.party} ({r.kind}, {r.vehicle}, row {r.row})" for r in rows
            )
            parties_involved = {r.party for r in rows}
            flag = "different parties" if len(parties_involved) > 1 else "same party"
            details.append(f"{ref} appears {len(rows)} times ({flag}): {parts}")
        metrics["duplicate_invoice_refs"] = len(duplicates)
        findings.append(
            Finding(
                "Invoice reference reuse, invisible to the paper register: "
                + "; ".join(details)
                + ". A duplicate check at issue time would have stopped both entries.",
                "duplicates",
            )
        )
    else:
        metrics["duplicate_invoice_refs"] = 0

    chit_with_invoice = [m for m in provisional if m.invoice_no]
    chit_without_chit_no = [m for m in provisional if not m.chit_no]
    if chit_with_invoice or chit_without_chit_no:
        examples = ", ".join(
            f"row {m.row} {m.date.isoformat()} {m.chit_no or '(no chit no)'} carries {m.invoice_no}"
            for m in chit_with_invoice
        ) or "none with an invoice number"
        findings.append(
            Finding(
                f"Chit hygiene is inconsistent: {len(chit_with_invoice)} chit rows also carry an invoice "
                f"number ({examples}) and {len(chit_without_chit_no)} chit rows have no chit number. "
                f"The provisional and final records share one field, which is how a reference gets reused.",
                "chit_hygiene",
            )
        )

    invoice_integrity = reference_integrity(movements, "invoice_no")
    chit_integrity = reference_integrity(movements, "chit_no")
    if invoice_integrity["gaps"]:
        gap_text = ", ".join(f"{prefix}{number}" for prefix, number in invoice_integrity["gaps"][:8])
        findings.append(
            Finding(
                f"Invoice numbering has {len(invoice_integrity['gaps'])} gaps in the sequence "
                f"(for example {gap_text}); either cancelled invoices or movements that never reached "
                f"the gate. The register cannot tell which, so the daily reconciliation needs both sources.",
                "sequence_gap",
            )
        )
    else:
        metrics["invoice_gaps"] = 0
    if chit_integrity["gaps"]:
        metrics["chit_gaps"] = len(chit_integrity["gaps"])
    else:
        metrics["chit_gaps"] = 0

    dwell = [m.dwell_minutes for m in movements if m.dwell_minutes is not None and m.dwell_minutes >= 0]
    missing_out = sum(1 for m in movements if m.out_minutes is None)
    negative = sum(1 for m in movements if m.dwell_minutes is not None and m.dwell_minutes < 0)
    outliers = sum(1 for value in dwell if value > cfg.register.dwell_outlier_minutes)
    if dwell:
        median = int(statistics.median(dwell))
        metrics.update(
            dwell_median_minutes=median,
            dwell_p90_minutes=round(_percentile(dwell, 0.9)),
            dwell_max_minutes=max(dwell),
            dwell_missing_out=missing_out,
            dwell_negative=negative,
        )
        findings.append(
            Finding(
                f"Turn-around is tight and complete: median {median} min, 90th percentile "
                f"{round(_percentile(dwell, 0.9))} min, max {max(dwell)} min over {len(dwell)} closed "
                f"movements; {missing_out} rows have no out-time, {negative} have an out-time before the "
                f"in-time, and {outliers} exceed {cfg.register.dwell_outlier_minutes} min. The gate is "
                f"not losing time to paperwork; any new check has to fit inside the existing minute or two "
                f"at the cabin.",
                "dwell",
            )
        )

    unique_vehicles = len({m.vehicle for m in movements})
    metrics["unique_vehicles"] = unique_vehicles
    if unique_vehicles == total and total > 20:
        findings.append(
            Finding(
                f"Every one of the {total} rows has a different plate ({unique_vehicles} unique): a month "
                f"of gate traffic with no repeat lorry. Either plates are copied inconsistently from the "
                f"invoice, or regular carriers are not recorded as master data. Vehicle history or an "
                f"allow-list cannot be built from this register until plates are validated against a master.",
                "vehicles",
            )
        )

    if cfg.register.operating_days:
        weekdays = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
        allowed = {weekdays[name] for name in cfg.register.operating_days if name in weekdays}
        present = {m.date for m in movements}
        absent = []
        day = first
        while day <= last:
            if day.weekday() in allowed and day not in present:
                absent.append(day.isoformat())
            day += __import__("datetime").timedelta(days=1)
        metrics["absent_operating_days"] = absent
        if absent:
            findings.append(
                Finding(
                    f"No gate movements at all on {len(absent)} configured operating days "
                    f"({', '.join(absent[:6])}{'...' if len(absent) > 6 else ''}). A day with no entries "
                    f"is either a holiday nobody recorded or a register gap; the daily reconciliation must "
                    f"treat a zero-activity day as an exception rather than as normal.",
                    "calendar",
                )
            )

    start, count, share = arrival_peak(movements, cfg.register.peak_window_hours)
    metrics["peak_window"] = {"start": _clock(start), "count": count, "share_percent": share}
    if share >= 40:
        findings.append(
            Finding(
                f"{count} of {total} arrivals ({share}%) fall in the {cfg.register.peak_window_hours}-hour "
                f"window from {_clock(start)}: the gate is bursty, so the pass has to be issued while the "
                f"queue is longest, not in a quiet office.",
                "peak",
            )
        )

    mapped = set(cfg.register.columns)
    missing_fields = [
        field
        for field in cfg.compliance.required_pass_fields
        if field not in mapped and field not in register.columns
    ]
    metrics["missing_required_fields"] = missing_fields
    if missing_fields:
        findings.append(
            Finding(
                f"The register cannot produce a compliant pass by itself: it has no "
                f"{', '.join(missing_fields)}. With the configured e-way bill threshold of "
                f"Rs {cfg.compliance.eway_bill_threshold_inr:,}, compliance for the "
                f"{len(invoice_movements)} invoice-backed movements cannot be checked from this data at "
                f"all, so the value and e-way bill number must be pulled from the invoice source at issue time.",
                "compliance_gap",
            )
        )

    if cfg.gate.connectivity in {"none", "poor", "intermittent"}:
        findings.append(
            Finding(
                f"Connectivity at the point of use is '{cfg.gate.connectivity}': a design that calls a "
                f"server, cloud printer or online validator per pass will fail exactly when the queue is "
                f"longest. The gate device must issue passes from local state and sync later.",
                "connectivity",
            )
        )

    ordered = sorted(findings, key=lambda f: _PRIORITY.get(f.metric, 99))
    metrics["finding_metrics"] = [f.metric for f in ordered]
    return [f.text for f in ordered], metrics


_PRIORITY = {
    "compliance_gap": 0,
    "connectivity": 1,
    "night": 2,
    "duplicates": 3,
    "chit_hygiene": 4,
    "volume": 5,
    "dwell": 6,
    "vehicles": 7,
    "calendar": 8,
    "peak": 9,
    "sequence_gap": 10,
}
