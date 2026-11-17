"""Provider-agnostic chat-completion call.

The model is used for exactly one job: reading three people's informal answers
and turning them into atomic requirements, contradictions and open questions
that cite the utterance they came from.  That is a language task; the register
arithmetic stays deterministic in register.py.

Providers are tried in this order:
  1. GATEPASS_LLM_URL          any OpenAI-compatible /chat/completions endpoint
  2. OPENAI_API_KEY            api.openai.com
  3. ANTHROPIC_API_KEY         api.anthropic.com
  4. GEMINI_API_KEY            generativelanguage.googleapis.com
  5. public no-key endpoint    text.pollinations.ai (used for the shipped run)

No key is required for the shipped run, and no key is ever written to disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SYSTEM_PROMPT = """You are a senior business analyst on a factory digitisation project.
You read interview transcripts and produce a precise, evidence-grounded requirements brief.

Hard rules:
- Only state what an interviewee actually said. Never invent a requirement, a number or a policy.
- Every requirement, conflict and constraint must cite the transcript ids it came from.
- A requirement's source_persona must be the persona who stated it.
- A conflict exists only when two different personas said incompatible things. Cite both sides.
- The budget is a number only if someone stated a sanctioned amount; otherwise null.
- Open questions must name who would know the answer.
- Keep each text under 300 characters, concrete and free of filler.
- Reply with a single JSON object and nothing else: no prose, no code fence."""

USER_TEMPLATE = """Project: {plant} dispatch gate.

Interview transcript (verbatim, ids in square brackets):
{transcript}

Design decisions already taken by the engineer (use these to resolve conflicts, do not treat them as interview statements):
{policy}

Produce JSON with exactly this shape:
{{
  "requirements": [
    {{"text": "...", "source_persona": "dev|anjali|bablu", "status": "confirmed|assumed", "evidence": ["u01"]}}
  ],
  "conflicts": [
    {{"between": ["dev", "anjali"], "about": "...", "resolution": "...", "evidence": {{"dev": ["u24"], "anjali": ["u08"]}}}}
  ],
  "constraints": [
    {{"text": "...", "evidence": ["u12"]}}
  ],
  "budget_inr": 90000,
  "open_questions": [
    {{"text": "...", "owner": "who would know"}}
  ]
}}

Rules for this output:
- requirements: 10 to 14 atomic, testable statements. Each must name the actor or the artefact and the condition it holds under. They must collectively cover: what the tool prints or produces; where invoice data comes from and who owns it; who uses it and where; the expected service time at the gate; what happens when the office system or network is unavailable; the night/provisional workflow; compliance fields and thresholds; record-keeping and audit; authority over data; reuse at another site. Treat a stated constraint as a requirement on the tool when it shapes the design (for example "works with no internet" is a requirement).
- Write requirements as full sentences ("The gate pass must ...", "The guard must be able to ..."), not keyword fragments.
- A requirement is behaviour of the new tool. Do not restate current manual practice ("the guard writes chits in the red register") as a requirement; use it as evidence for what the tool must replace or preserve.
- Do not promote the engineer's design decisions (the list above) into requirements; they are resolutions, not interview facts.
- conflicts: every genuine cross-person disagreement about scope, compliance or process. Look specifically for an absolute claim by one person (a document is always there, one click is enough) colliding with another person's description of how the process actually runs when that condition does not hold; with the engineer's decision as the resolution. Do not invent disagreement where one person simply deferred to another. For each conflict, cite evidence separately per side under "evidence": {{"<persona>": ["u.."], "<other persona>": ["u.."]}}; each id must be an utterance by that persona.
- constraints: hard limits (site, connectivity, money, legal, people, time), stated as facts.
- open_questions: 4 to 8 items that the transcript genuinely does not answer and that are not already fixed by configuration or by an engineer decision; each names the persona or role who could answer.
- budget_inr: the sanctioned amount as spoken, or null. Do not estimate.
Return JSON only."""


class LLMError(RuntimeError):
    """Raised when no provider produced usable JSON."""


@dataclass
class LLMResult:
    data: dict[str, Any]
    provider: str
    model: str
    endpoint: str
    attempts: int
    latency_ms: int
    raw_excerpt: str = ""
    cached: bool = False
    errors: list[str] = field(default_factory=list)


def _post(url: str, payload: dict, headers: dict[str, str], timeout: int = 120) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _openai_chat(url: str, key: str | None, model: str, system: str, user: str) -> tuple[str, str]:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 4000,
    }
    body = _post(url, payload, headers)
    content = body["choices"][0]["message"]["content"]
    return content, str(body.get("model") or model)


def _anthropic(key: str, model: str, system: str, user: str) -> tuple[str, str]:
    body = _post(
        "https://api.anthropic.com/v1/messages",
        {
            "model": model,
            "max_tokens": 4000,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    content = "".join(part.get("text", "") for part in body.get("content", []) if part.get("type") == "text")
    return content, str(body.get("model") or model)


def _gemini(key: str, model: str, system: str, user: str) -> tuple[str, str]:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )
    body = _post(
        url,
        {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 4000, "responseMimeType": "application/json"},
        },
        {},
    )
    content = body["candidates"][0]["content"]["parts"][0]["text"]
    return content, model


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise LLMError("model response contained no parseable JSON object")


def providers() -> list[tuple[str, str, str | None]]:
    """Return (provider, endpoint, key) candidates in preference order."""
    chain: list[tuple[str, str, str | None]] = []
    if os.environ.get("GATEPASS_LLM_URL"):
        chain.append(
            (
                "custom-openai-compatible",
                os.environ["GATEPASS_LLM_URL"],
                os.environ.get("GATEPASS_LLM_KEY"),
            )
        )
    if os.environ.get("OPENAI_API_KEY"):
        chain.append(
            (
                "openai",
                os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions",
                os.environ["OPENAI_API_KEY"],
            )
        )
    if os.environ.get("ANTHROPIC_API_KEY"):
        chain.append(("anthropic", "https://api.anthropic.com/v1/messages", os.environ["ANTHROPIC_API_KEY"]))
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        chain.append(
            ("gemini", "", os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        )
    if os.environ.get("GATEPASS_ALLOW_PUBLIC_LLM", "1") != "0":
        chain.append(("pollinations-public", "https://text.pollinations.ai/openai", None))
    return chain


def default_model(provider: str) -> str:
    if provider == "openai":
        return os.environ.get("GATEPASS_LLM_MODEL", "gpt-4o-mini")
    if provider == "anthropic":
        return os.environ.get("GATEPASS_LLM_MODEL", "claude-3-5-haiku-latest")
    if provider == "gemini":
        return os.environ.get("GATEPASS_LLM_MODEL", "gemini-2.0-flash")
    return os.environ.get("GATEPASS_LLM_MODEL", "openai")


def extract(
    transcript: str,
    policy: tuple[str, ...],
    plant: str,
    retries: int = 2,
    cache_path: str | None = None,
) -> LLMResult:
    user = USER_TEMPLATE.format(
        plant=plant,
        transcript=transcript,
        policy="\n".join(f"- {item}" for item in policy) or "- none stated",
    )
    cache_key = hashlib.sha256((SYSTEM_PROMPT + "\n" + user).encode("utf-8")).hexdigest()
    if cache_path and Path(cache_path).exists():
        try:
            cached = json.loads(Path(cache_path).read_text(encoding="utf-8"))
            if cached.get("key") == cache_key and isinstance(cached.get("data"), dict):
                return LLMResult(
                    data=cached["data"],
                    provider=str(cached.get("provider", "cache")),
                    model=str(cached.get("model", "cached")),
                    endpoint=str(cached.get("endpoint", "")),
                    attempts=0,
                    latency_ms=0,
                    raw_excerpt=str(cached.get("raw_excerpt", "")),
                    cached=True,
                    errors=[],
                )
        except (OSError, json.JSONDecodeError):
            pass
    errors: list[str] = []
    started = time.monotonic()
    for provider, endpoint, key in providers():
        model = default_model(provider)
        for attempt in range(1, retries + 1):
            try:
                if provider == "anthropic":
                    content, served_model = _anthropic(key or "", model, SYSTEM_PROMPT, user)
                elif provider == "gemini":
                    content, served_model = _gemini(key or "", model, SYSTEM_PROMPT, user)
                else:
                    content, served_model = _openai_chat(endpoint, key, model, SYSTEM_PROMPT, user)
                data = extract_json(content)
                if not isinstance(data, dict):
                    raise LLMError("model returned JSON that is not an object")
                if cache_path:
                    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(cache_path).write_text(
                        json.dumps(
                            {
                                "key": cache_key,
                                "provider": provider,
                                "model": served_model,
                                "endpoint": endpoint,
                                "raw_excerpt": content[:600],
                                "data": data,
                            },
                            indent=2,
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                return LLMResult(
                    data=data,
                    provider=provider,
                    model=served_model,
                    endpoint=endpoint,
                    attempts=attempt,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    raw_excerpt=content[:600],
                    errors=errors,
                )
            except urllib.error.HTTPError as exc:
                errors.append(f"{provider} attempt {attempt}: HTTP {exc.code}: {exc.reason}")
                if exc.code == 402 and attempt < retries:
                    time.sleep(46)
                elif attempt < retries:
                    time.sleep(1.5 * attempt)
            except (urllib.error.URLError, KeyError, TypeError, LLMError, json.JSONDecodeError) as exc:
                errors.append(f"{provider} attempt {attempt}: {type(exc).__name__}: {exc}")
                if attempt < retries:
                    time.sleep(1.5 * attempt)
        # next provider
    raise LLMError("; ".join(errors[-4:]) or "no LLM provider configured")
