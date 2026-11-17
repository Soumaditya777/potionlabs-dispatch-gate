"""Interview transcript loading and grounding helpers.

The transcript is the only source for requirements, conflicts and budget.
Everything the model is allowed to claim has to point back to an utterance id
in this file, which is what the verifier in brief.py enforces.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError

PERSONA_ROLES = {
    "dev": "owner, signs off on what gets built",
    "anjali": "accounts, owns money, GST and audit paperwork",
    "bablu": "gate guard on day shift, keeps the red register",
}

CURRENCY_RE = re.compile(r"(?:rs\.?|inr|₹)\s*([\d][\d,]*(?:\.\d+)?)", re.IGNORECASE)
SANCTION_RE = re.compile(r"sanction|budget|approved|all in|spend", re.IGNORECASE)


DEFLECTION_CUES = (
    "ask him", "ask her", "ask them", "not something i decide", "i don't hand out",
    "you tell me what you want to know", "whatever dev-da says", "i open the gate, i close the gate",
    "i am here all day", "i only see the paperwork",
)


@dataclass(frozen=True)
class Utterance:
    id: str
    persona: str
    speaker: str
    question: str
    answer: str
    asked_count: int = 1
    questions: tuple[str, ...] = ()

    @property
    def role(self) -> str:
        return PERSONA_ROLES.get(self.persona, "interviewed stakeholder")


def load_interviews(path: str | Path) -> tuple[Utterance, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw.get("interviews") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ConfigError(f"{path}: expected an 'interviews' array")
    utterances = []
    for index, item in enumerate(items, start=1):
        try:
            utterances.append(
                Utterance(
                    id=str(item.get("id") or f"u{index:02d}"),
                    persona=str(item["persona"]).strip().lower(),
                    speaker=str(item.get("speaker", item["persona"])),
                    question=str(item.get("question", "")).strip(),
                    answer=str(item["answer"]).strip(),
                    asked_count=int(item.get("asked_count", 1)),
                    questions=tuple(str(q) for q in item.get("questions", [])),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"{path}: bad interview entry {index}: {exc}") from exc
    if not utterances:
        raise ConfigError(f"{path}: no interviews found")
    return tuple(utterances)


def personas(utterances: tuple[Utterance, ...]) -> dict[str, str]:
    return {u.persona: u.speaker for u in utterances}


def sanctioned_budget(utterances: tuple[Utterance, ...]) -> int | None:
    """Deterministic fallback: a currency amount stated as sanctioned."""
    candidates: list[int] = []
    for utterance in utterances:
        for match in CURRENCY_RE.finditer(utterance.answer):
            amount = int(float(match.group(1).replace(",", "")))
            window = utterance.answer[max(0, match.start() - 80) : match.end() + 80]
            if SANCTION_RE.search(window) and amount >= 1000:
                candidates.append(amount)
    if not candidates:
        return None
    # The smallest stated sanction is the safe reading: do not design past it.
    return min(candidates)


def render_transcript(utterances: tuple[Utterance, ...]) -> str:
    lines = []
    for u in utterances:
        repeat = f" (asked {u.asked_count}x, answer unchanged)" if u.asked_count > 1 else ""
        lines.append(f"[{u.id}] {u.speaker} ({u.persona}, {u.role}){repeat}: {u.answer}")
    return "\n".join(lines)

