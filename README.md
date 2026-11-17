# Dispatch gate brief compiler

A small, offline-capable pipeline that turns three people's informal answers plus a raw gate
register into a validated requirements brief for a factory dispatch-gate system.

`run.sh` writes `output/result.json`, which is validated against the assessment's
`output_schema` before it is written.

## What it does

1. **Ingests** the interview transcript (`data/interviews.json`) and the register export
   (`data/register.json`). Column names, night window, operating days, value threshold,
   required pass fields and the plan all come from `config/factory.json`; nothing about
   this plant is compiled into the code.
2. **Extracts claims with a language model** (`src/gatepass/llm.py`): requirements,
   cross-person contradictions and open questions, each tied to transcript utterance ids.
   The model is used here because the input is unstructured speech and the output has to be
   attributed and deduplicated; arithmetic and rule checks stay deterministic.
3. **Verifies every claim** (`src/gatepass/brief.py`): citations must exist, the attributed
   persona must match the cited speaker, conflicts must carry evidence from both sides, and
   the budget must appear in the transcript. Anything unverifiable is dropped and logged in
   `output/provenance.json`.
4. **Computes register findings deterministically** (`src/gatepass/register.py`): volume mix,
   night share, duplicate/reused references, sequence gaps, dwell distribution, vehicle
   uniqueness, calendar gaps, peak arrival window, and missing compliance fields.
5. **Builds the plan** from configuration plus the measured register facts, and refuses to
   emit anything that violates the schema (`src/gatepass/schema.py`).

## Run

```bash
./run.sh
```

Requires Python 3.10+ and nothing else (standard library only). The model call tries, in
order: `GATEPASS_LLM_URL` (any OpenAI-compatible endpoint), `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, then a public no-key endpoint. A matching response is
cached in `output/llm_cache.json` so re-runs are deterministic and do not spend quota. With
`--no-llm` (or if every provider fails) the pipeline uses deterministic sentence extraction
and records that in `output/provenance.json`.

## Test

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

24 tests cover time/plate normalisation, reference parsing, duplicate and night detection,
config-driven behaviour, empty/negative inputs, budget grounding, JSON extraction from model
output, schema acceptance/rejection, and a full offline CLI run.

## Inputs

- `data/interviews.json` — 50 asks across the three stakeholders, deduplicated to 28 unique
  answers with every question kept. Each answer has `persona`, `speaker`, `questions`.
- `data/register.json` — the October gate register: 129 rows, 2026-10-01 to 2026-10-31.
- `data/output_schema.json` — schema served by the assessment gate.
- `config/factory.json` — the only plant-specific file: column mapping, thresholds, calendar,
  architecture plan and bill of materials.

## Limits

- The register has no consignment value and no e-way bill column, so compliance can only be
  flagged as unverifiable, not measured.
- The model's prose is constrained by citation checks, not by semantic entailment: a
  requirement can still over-generalise a cited sentence. Every requirement in the shipped
  output was read against its source utterance.
- No Tally connector is included; the plan specifies the integration but the assessment data
  contains no Tally export to build against.
