"""Configuration loading and validation.

The configuration is the only place where plant-specific facts live: which
JSON column holds the vehicle number, what counts as night, what the e-way
bill threshold is, and which fields a pass must carry.  The pipeline raises a
clear error instead of silently guessing when a required key is missing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when configuration or input data cannot be used."""


@dataclass(frozen=True)
class RegisterConfig:
    columns: dict[str, str]
    invoice_kinds: tuple[str, ...]
    provisional_kinds: tuple[str, ...]
    night_window: tuple[str, str]
    operating_days: tuple[str, ...]
    dwell_outlier_minutes: int
    peak_window_hours: int


@dataclass(frozen=True)
class ComplianceConfig:
    eway_bill_threshold_inr: int
    required_pass_fields: tuple[str, ...]


@dataclass(frozen=True)
class GateConfig:
    connectivity: str
    max_service_seconds: int
    budget_inr: int | None
    multi_site: bool


@dataclass(frozen=True)
class ArchitectureConfig:
    plan: tuple[str, ...]
    bill_of_materials_inr: dict[str, int]
    resolution_policy: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    plant: str
    dataset: str
    register: RegisterConfig
    compliance: ComplianceConfig
    gate: GateConfig
    architecture: ArchitectureConfig

    @property
    def any_invoice_value_field(self) -> bool:
        """True when the register config knows where consignment value lives."""
        return "invoice_value_inr" in self.register.columns


def load_config(path: str | Path) -> Config:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        reg = raw["register"]
        cols = dict(reg["columns"])
        missing = {"date", "vehicle", "in_time"} - set(cols)
        if missing:
            raise ConfigError(f"register.columns missing required keys: {sorted(missing)}")
        night = reg.get("night_window", ["20:00", "06:00"])
        if len(night) != 2:
            raise ConfigError("register.night_window must be [start, end]")
        comp = raw["compliance"]
        gate = raw.get("gate", {})
        arch = raw.get("architecture", {})
        return Config(
            plant=str(raw.get("plant", "unnamed plant")),
            dataset=str(raw.get("dataset", "")),
            register=RegisterConfig(
                columns=cols,
                invoice_kinds=tuple(reg.get("invoice_kinds", ["invoice"])),
                provisional_kinds=tuple(reg.get("provisional_kinds", ["chit"])),
                night_window=(str(night[0]), str(night[1])),
                operating_days=tuple(reg.get("operating_days", [])),
                dwell_outlier_minutes=int(reg.get("dwell_outlier_minutes", 120)),
                peak_window_hours=int(reg.get("peak_window_hours", 3)),
            ),
            compliance=ComplianceConfig(
                eway_bill_threshold_inr=int(comp.get("eway_bill_threshold_inr", 0)),
                required_pass_fields=tuple(comp.get("required_pass_fields", [])),
            ),
            gate=GateConfig(
                connectivity=str(gate.get("connectivity", "unknown")),
                max_service_seconds=int(gate.get("max_service_seconds", 60)),
                budget_inr=(int(gate["budget_inr"]) if gate.get("budget_inr") is not None else None),
                multi_site=bool(gate.get("multi_site", False)),
            ),
            architecture=ArchitectureConfig(
                plan=tuple(arch.get("plan", [])),
                bill_of_materials_inr={
                    str(k): int(v) for k, v in arch.get("bill_of_materials_inr", {}).items()
                },
                resolution_policy=tuple(arch.get("resolution_policy", [])),
            ),
        )
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"invalid configuration {path}: {exc}") from exc
