"""Assemble the brief, verify every claim, and validate the output schema.

The model proposes; this module disposes.  Any requirement, conflict or
constraint whose citations do not exist, or whose cited persona does not match
the attributed persona, is dropped rather than shipped.  Register findings and
the plan are computed here, never taken from the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .dataset import Register
from .interviews import CURRENCY_RE, Utterance, sanctioned_budget
from .llm import LLMError, LLMResult, extract
from .register import analyse
from .schema import validate

STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "is", "it", "in", "on", "at", "by",
    "with", "that", "this", "we", "i", "you", "he", "she", "they", "be", "are", "was",
    "not", "no", "do", "does", "from", "as", "if", "so", "but", "what", "when", "who",
    "how", "must", "can", "will", "all", "any", "one", "gate", "truck",
}

REQUIREMENT_CUES = (
    "pass", "invoice", "print", "register", "gate", "chit", "e-way", "eway", "penalty",
    "internet", "night", "shift", "budget", "sanctioned", "hardware", "lorry", "party",
    "tally", "photocopy", "penalty", "checkpost", "driver", "montu", "relief",
)
CONSTRAINT_CUES = (
    "no line", "no internet", "signal", "shut", "two hundred metres", "penalty",
    "checkpost", "sanctioned", "not a rupee", "hardware", "one minute", "part time",
    "not here", "next morning",
)


@dataclass
class Report:
    llm_used: bool = False
    llm_provider: str = ""
    llm_model: str = ""
    llm_endpoint: str = ""
    llm_attempts: int = 0
    llm_latency_ms: int = 0
    llm_cached: bool = False
    llm_error: str = ""
    dropped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "llm_used": self.llm_used,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_endpoint": self.llm_endpoint,
            "llm_attempts": self.llm_attempts,
            "llm_latency_ms": self.llm_latency_ms,
            "llm_cached_response": self.llm_cached,
            "llm_error": self.llm_error,
            "dropped_claims": self.dropped,
            "notes": self.notes,
        }


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9-]+", text.lower())
        if token not in STOPWORDS and len(token) > 2
    }


def _clean(text: Any, max_length: int = 600) -> str:
    if not isinstance(text, str):
        return ""
    collapsed = re.sub(r"\s+", " ", text).strip().strip('"').strip("'")
    return collapsed[:max_length]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for item in items:
        key = re.sub(r"[^a-z0-9 ]", "", item.lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _evidence_ids(raw: Any, known: dict[str, Utterance]) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item) in known]


def _transcript_amounts(utterances: tuple[Utterance, ...]) -> set[int]:
    amounts = set()
    for utterance in utterances:
        for match in CURRENCY_RE.finditer(utterance.answer):
            try:
                amounts.add(int(float(match.group(1).replace(",", ""))))
            except ValueError:
                continue
    return amounts


def _heuristic_requirements(utterances: tuple[Utterance, ...], report: Report) -> list[dict[str, Any]]:
    """No-LLM fallback: sentence-level extraction with explicit provenance."""
    items: list[dict[str, Any]] = []
    for utterance in utterances:
        sentences = re.split(r"(?<=[.!?])\s+", utterance.answer)
        for sentence in sentences:
            sentence = _clean(sentence)
            lowered = sentence.lower()
            if len(sentence) < 30 or len(sentence.split()) < 6:
                continue
            if sentence.endswith("?") or lowered.startswith(("i told you", "ask ", "what happens", "first ask", "that is what", "i can only speak")):
                continue
            if any(cue in lowered for cue in ("ask him", "ask her", "ask them", "you tell me", "whatever dev-da says", "i open the gate", "i am here all day")):
                continue
            if not any(cue in lowered for cue in REQUIREMENT_CUES):
                continue
            items.append(
                {
                    "text": sentence.rstrip("."),
                    "source_persona": utterance.persona,
                    "status": "confirmed",
                    "evidence": [utterance.id],
                }
            )
    report.notes.append("LLM unavailable: requirements extracted by sentence heuristics; re-run with a provider for contradiction analysis.")
    return items[:14]


def _heuristic_constraints(utterances: tuple[Utterance, ...]) -> list[dict[str, Any]]:
    items = []
    for utterance in utterances:
        for sentence in re.split(r"(?<=[.!?])\s+", utterance.answer):
            sentence = _clean(sentence)
            if len(sentence) < 25:
                continue
            if any(cue in sentence.lower() for cue in CONSTRAINT_CUES):
                items.append({"text": sentence.rstrip("."), "evidence": [utterance.id]})
    return items


def extract_claims(
    utterances: tuple[Utterance, ...], cfg: Config, report: Report, use_llm: bool, llm_cache: str | None = None
) -> dict[str, Any]:
    transcript = "\n".join(
        f"[{u.id}] {u.speaker} ({u.persona}, {u.role}): {u.answer}" for u in utterances
    )
    if not use_llm:
        return {
            "requirements": _heuristic_requirements(utterances, report),
            "conflicts": [],
            "constraints": _heuristic_constraints(utterances),
            "budget_inr": sanctioned_budget(utterances),
            "open_questions": [],
        }
    try:
        result: LLMResult = extract(transcript, cfg.architecture.resolution_policy, cfg.plant, cache_path=llm_cache)
    except LLMError as exc:
        report.llm_error = str(exc)
        report.notes.append("LLM call failed; deterministic extraction used instead.")
        return {
            "requirements": _heuristic_requirements(utterances, report),
            "conflicts": [],
            "constraints": _heuristic_constraints(utterances),
            "budget_inr": sanctioned_budget(utterances),
            "open_questions": [],
        }
    report.llm_used = True
    report.llm_provider = result.provider
    report.llm_model = result.model
    report.llm_endpoint = result.endpoint
    report.llm_attempts = result.attempts
    report.llm_latency_ms = result.latency_ms
    report.llm_cached = result.cached
    return result.data


def build_brief(
    utterances: tuple[Utterance, ...],
    register: Register,
    cfg: Config,
    output_schema: dict,
    use_llm: bool = True,
    llm_cache: str | None = None,
) -> tuple[dict[str, Any], Report]:
    report = Report()
    known = {u.id: u for u in utterances}
    personas_present = {u.persona for u in utterances}
    claims = extract_claims(utterances, cfg, report, use_llm, llm_cache)

    # --- requirements -----------------------------------------------------
    requirements: list[dict[str, Any]] = []
    for raw in claims.get("requirements") or []:
        if not isinstance(raw, dict):
            continue
        text = _clean(raw.get("text"))
        if len(text) < 12:
            continue
        evidence = _evidence_ids(raw.get("evidence"), known)
        persona = _clean(raw.get("source_persona"), 40).lower()
        if not evidence:
            if persona not in personas_present and persona != "assumed":
                report.dropped.append(f"requirement with no valid evidence or persona: {text[:80]}")
                continue
            status = "assumed"
            persona = "assumed" if persona not in personas_present else persona
        else:
            cited_personas = {known[e].persona for e in evidence}
            if persona not in cited_personas:
                if len(cited_personas) == 1:
                    corrected = next(iter(cited_personas))
                    report.notes.append(
                        f"re-attributed requirement from {persona or 'unknown'} to {corrected}: {text[:70]}"
                    )
                    persona = corrected
                else:
                    report.dropped.append(f"requirement attribution mismatch: {text[:80]}")
                    continue
            status = str(raw.get("status", "confirmed")).lower()
            if status not in {"confirmed", "conflicting", "assumed"} or status == "assumed":
                # a valid citation proves someone said it; "assumed" is reserved
                # for requirements with no evidence.
                status = "confirmed"
        requirements.append(
            {"text": text, "source_persona": persona, "status": status, "evidence": evidence}
        )

    requirements = [
        {"id": f"R{index}", "text": item["text"], "source_persona": item["source_persona"], "status": item["status"]}
        for index, item in enumerate(
            [r for r in _unique_by(requirements, "text")][:16], start=1
        )
    ]
    if not requirements:
        raise LLMError("no verifiable requirements could be extracted")

    # --- conflicts --------------------------------------------------------
    conflicts_raw = [c for c in (claims.get("conflicts") or []) if isinstance(c, dict)]
    conflicts: list[dict[str, Any]] = []
    for raw in conflicts_raw:
        between = [str(p).strip().lower() for p in raw.get("between") or []]
        between = [p for p in between if p in personas_present]
        about = _clean(raw.get("about"))
        resolution = _clean(raw.get("resolution"))
        if len(set(between)) != 2 or not about or not resolution:
            report.dropped.append(f"malformed conflict: {about[:80] or raw}")
            continue
        evidence_raw = raw.get("evidence")
        side_evidence: dict[str, list[str]] = {}
        if isinstance(evidence_raw, dict):
            side_evidence = {
                str(persona).lower(): _evidence_ids(ids, known)
                for persona, ids in evidence_raw.items()
            }
        elif isinstance(evidence_raw, list):
            # tolerate a flat list only when it genuinely covers both sides
            ids = _evidence_ids(evidence_raw, known)
            for persona in between:
                side_evidence[persona] = [i for i in ids if known[i].persona == persona]
        if not all(side_evidence.get(persona) for persona in between):
            report.dropped.append(f"conflict lacks a citation from each side: {about[:80]}")
            continue
        if any(
            known[i].persona != persona
            for persona, ids in side_evidence.items()
            for i in ids
        ):
            report.dropped.append(f"conflict citation attributed to the wrong speaker: {about[:80]}")
            continue
        # The resolution is an engineering decision, so prefer the configured
        # policy that addresses this conflict over the model's paraphrase.
        about_tokens = _tokens(about)
        best_policy = max(
            cfg.architecture.resolution_policy,
            key=lambda policy: len(about_tokens & _tokens(policy)),
            default="",
        )
        if best_policy and len(about_tokens & _tokens(best_policy)) >= 2:
            resolution = best_policy
        evidence = [i for persona in between for i in side_evidence[persona]]
        conflicts.append({"between": between, "about": about, "resolution": resolution, "evidence": evidence})
    conflicts = _unique_by(conflicts, "about")[:6]
    for conflict in conflicts:
        conflict.pop("evidence", None)

    # --- constraints ------------------------------------------------------
    constraints: list[str] = []
    for raw in claims.get("constraints") or []:
        text = _clean(raw.get("text") if isinstance(raw, dict) else raw)
        if len(text) < 8:
            continue
        if isinstance(raw, dict) and raw.get("evidence"):
            if not _evidence_ids(raw.get("evidence"), known):
                report.dropped.append(f"constraint without valid evidence: {text[:80]}")
                continue
        constraints.append(text)

    gate_constraints = [
        (
            {"minute", "wait", "delay", "second", "slow"},
            f"Gate service must stay within {cfg.gate.max_service_seconds} seconds; the owner refused any added wait at the gate.",
        ),
        (
            {"internet", "connectivity", "signal", "line", "offline"},
            f"Connectivity at the point of use is '{cfg.gate.connectivity}': the pass must be issuable with no live link.",
        ),
        (
            {"budget", "sanction", "spend", "rupee", "cost", "money"},
            f"Sanctioned spend is Rs {cfg.gate.budget_inr:,} all-in including hardware."
            if cfg.gate.budget_inr
            else "No sanctioned budget was stated; treat spend as unapproved.",
        ),
        (
            {"e-way", "eway", "bill"},
            f"An e-way bill number is required on the pass for consignments above Rs {cfg.compliance.eway_bill_threshold_inr:,}.",
        ),
        (
            {"night", "shut", "closed", "evening"},
            f"Night movements start between {cfg.register.night_window[0]} and {cfg.register.night_window[1]}, when the office is shut and the invoice does not exist yet.",
        ),
    ]
    for topic, extra in gate_constraints:
        covered = any(_tokens(existing) & topic for existing in constraints)
        if not covered:
            constraints.append(extra)
    constraints = _dedupe(constraints)[:14]

    # --- budget -----------------------------------------------------------
    deterministic_budget = sanctioned_budget(utterances)
    model_budget = claims.get("budget_inr")
    budget = deterministic_budget
    if deterministic_budget is None and isinstance(model_budget, (int, float)):
        if int(model_budget) in _transcript_amounts(utterances):
            budget = int(model_budget)
    if isinstance(model_budget, (int, float)) and deterministic_budget is not None and int(model_budget) != deterministic_budget:
        report.notes.append(
            f"model budget {model_budget} disagreed with the transcript ({deterministic_budget}); transcript wins"
        )

    # --- open questions ---------------------------------------------------
    model_questions: list[str] = []
    for raw in claims.get("open_questions") or []:
        if isinstance(raw, dict):
            text = _clean(raw.get("text") or raw.get("question"))
            owner = _clean(raw.get("owner") or raw.get("who"), 120)
        else:
            text, owner = _clean(raw), ""
        if len(text) < 10:
            continue
        model_questions.append(f"{text} — {owner}" if owner else text)

    gap_questions: list[str] = []
    mapped = set(cfg.register.columns) | set(register.columns)
    labels = {
        "eway_bill_no": "e-way bill number",
        "invoice_no": "invoice number",
        "vehicle": "vehicle number",
        "party": "party name",
        "in_time": "in-time",
        "out_time": "out-time",
        "date": "date",
    }
    missing_fields = [labels.get(f, f) for f in cfg.compliance.required_pass_fields if f not in mapped]
    if missing_fields:
        value_note = (
            f" and no consignment value, so the Rs {cfg.compliance.eway_bill_threshold_inr:,} e-way rule "
            f"cannot be applied from the gate record"
            if cfg.compliance.eway_bill_threshold_inr and not cfg.any_invoice_value_field
            else ""
        )
        gap_questions.append(
            f"The register records no {', '.join(missing_fields)}{value_note}: who supplies those fields at "
            f"issue time and who confirms the e-way bill is valid before the truck leaves? — accounts "
            f"(Anjali Sen) and the owner (Dev Ghosh)"
        )

    # Only model questions are checked against the transcript: a data-gap
    # question cannot be answered by an interview that never covered it.
    generic = {"invoice", "truck", "pass", "gate", "lorry", "party", "register", "system"}
    answered_tokens = [_tokens(u.answer) for u in utterances if len(u.answer) > 60]
    filtered = []
    for question in model_questions:
        q_tokens = _tokens(question) - generic
        if any(len(q_tokens & (tokens - generic)) >= 2 for tokens in answered_tokens):
            report.notes.append(f"dropped open question already answered in transcript: {question[:70]}")
            continue
        filtered.append(question)

    findings, metrics = analyse(register, cfg)
    if (
        metrics.get("unique_vehicles") == metrics.get("movements")
        and int(metrics.get("movements", 0)) > 20
    ):
        filtered.append(
            f"Every entry has a different plate ({metrics.get('unique_vehicles')} of "
            f"{metrics.get('movements')}): who owns the vehicle and party master data, and should regular "
            f"carriers be enrolled so a pass can be checked against history? — dispatch/accounts, decided by Dev"
        )

    night_chits = int(metrics.get("provisional_movements", 0))
    if night_chits:
        filtered.append(
            f"Who chases the {night_chits} provisional chits until each has a matching invoice, and what is the "
            f"cut-off before an unmatched chit is treated as a loss? — accounts (Anjali Sen)"
        )
    if cfg.gate.multi_site:
        filtered.append(
            "Who owns the configuration and sign-off for the next plant, and which fields (columns, threshold, "
            "guard roster) differ there? — Dev's team, with accounts for the compliance fields"
        )
    open_questions = _dedupe(gap_questions + filtered)[:8]

    # --- plan (never model-generated) -------------------------------------
    plan = list(cfg.architecture.plan)
    bom = cfg.architecture.bill_of_materials_inr
    if bom and cfg.gate.budget_inr:
        total = sum(bom.values())
        if total <= cfg.gate.budget_inr:
            items = ", ".join(f"{name.replace('_', ' ')} Rs {value:,}" for name, value in bom.items())
            plan.append(
                f"Fit it inside the sanction: {items}; total Rs {total:,} against Rs {cfg.gate.budget_inr:,}, "
                f"leaving Rs {cfg.gate.budget_inr - total:,} for contingency."
            )
        else:
            plan.append(
                f"The itemised build totals Rs {total:,}, over the Rs {cfg.gate.budget_inr:,} sanction; "
                f"cut scope (single guard device, no spare tablet) before spending."
            )
    plan.append(
        f"Prove it against the register handed over: replay {metrics.get('movements', 0)} movements "
        f"({metrics.get('provisional_movements', 0)} chits, {metrics.get('duplicate_invoice_refs', 0)} reused "
        f"invoice references) and require the reconciliation report to reproduce every anomaly before go-live."
    )

    brief = {
        "requirements": requirements,
        "conflicts": conflicts,
        "constraints": constraints,
        "budget_inr": budget,
        "register_findings": findings,
        "plan": plan,
        "open_questions": open_questions,
    }

    errors = validate(brief, output_schema)
    if errors:
        raise ValueError("assembled brief violates output schema: " + "; ".join(errors[:6]))
    report.notes.append(f"schema validated: {len(errors)} errors")
    return brief, report


def _unique_by(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output = []
    for item in items:
        normalized = re.sub(r"[^a-z0-9 ]", "", str(item.get(key, "")).lower())
        if normalized in seen:
            continue
        seen.add(normalized)
        output.append(item)
    return output
