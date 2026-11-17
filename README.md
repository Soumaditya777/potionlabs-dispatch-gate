# Dispatch Gate Brief Compiler

A small, dependency-free Python pipeline that turns three stakeholders' informal interviews plus a
raw factory gate register into a schema-validated requirements brief for an offline-first
dispatch gate-pass system.

Built as the PotionLabs AI Engineer Intern assessment: a plastics manufacturer in Kolkata needs a
"gate-pass system" and has said almost nothing else. This repository contains the discovery
work, the pipeline that compiles it, the evidence trail, and the produced `output/result.json`.

---

## The problem

Trucks leave the factory on an invoice. At night the office is shut, so the guard writes a
hand-numbered chit and the invoice is raised the next morning. The gate cabin has no internet
line, the office is 200 m away, and mobile signal dies near the weighbridge. The owner wants
"one click, printed from the invoice, nothing typed twice". Accounts needs an e-way bill number
on any consignment above ₹1,00,000. The sanctioned budget is ₹90,000, hardware included.

The brief is not a document request — it is a decision under conflict. The pipeline's job is to
turn that into requirements each traced to a person, contradictions with a resolution, and a
plan that fits the constraints.

## What the tool does

```
interviews.json ──┐
                  ├─► LLM claim extraction ─► citation verifier ─┐
output_schema ────┘                                             ├─► schema validator ─► result.json
register.json ─────► deterministic analysis ────────────────────┤
config/factory.json ► plan + budget + thresholds ───────────────┘
```

1. **Ingest.** Interviews, register rows, the assessment schema, and a plant configuration.
2. **Extract.** One language-model call reads the transcript and proposes atomic requirements,
   cross-person conflicts and open questions, each with transcript citation ids.
3. **Verify.** Citations must exist, the attributed persona must match the cited speaker,
   conflicts must carry evidence from both sides, and the budget must appear in the transcript.
   Unverifiable claims are dropped and logged — the model never gets the last word.
4. **Analyse.** The register is processed deterministically: movement mix, night share,
   duplicate and reused references, sequence gaps, dwell distribution, vehicle uniqueness,
   calendar gaps, peak arrival window, missing compliance fields.
5. **Assemble.** Plan and bill of materials come from configuration plus measured facts. The
   result is validated against the server's JSON Schema before it is written.

The same pipeline runs against another plant by changing `config/factory.json` and the two data
files. Nothing about this factory is compiled into the code.

## Key findings from the register

Computed from `data/register.json` (129 movements, 2026-10-01 → 2026-10-31):

| Finding | Evidence |
|---|---|
| 18 chit movements, 14% of traffic | all arrivals between 20:00 and 06:00, never invoice-backed |
| Night coverage split | Bablu 10 chits, relief guard Montu 8 |
| Invoice reference reused | `GP/26-27/2203` on 2026-10-01 (Roy Brothers) and 2026-10-06 (New Era Polymers, chit C-48) |
| Chit carries an invoice number | row 26 / C-48 also stores `GP/26-27/2203` — provisional and final records share a field |
| Turn-around | median 45 min, p90 71 min, max 75 min, no negative or missing out-times |
| Vehicles | 129 rows, 129 distinct plates — no repeat carrier, so no vehicle history is derivable |
| Compliance gap | register has no consignment value and no e-way bill column; the ₹1,00,000 rule cannot be applied from gate data |
| Calendar | no movements on the two Sundays; every other day has 4–6 entries |

Full output: [`output/result.json`](output/result.json). Run metadata and dropped claims:
[`output/provenance.json`](output/provenance.json).

## Repository layout

```
run.sh                     entry point; writes output/result.json
config/factory.json        the only plant-specific file
data/interviews.json       50 asks, 28 unique answers, persona + speaker + questions
data/register.json         October gate register, 129 rows
data/output_schema.json    schema served by the assessment gate
src/gatepass/
  config.py                config loading and validation
  dataset.py               register ingestion and normalisation
  register.py              deterministic findings and metrics
  interviews.py            transcript loading, budget grounding
  llm.py                   provider chain, JSON extraction, response cache
  brief.py                 claim verification, assembly, plan, budget
  schema.py                in-tree JSON Schema validator (subset)
  cli.py                   command line entry point
tests/                     24 unit and end-to-end tests
docs/WRITEUP.md            the submitted half-page write-up
output/result.json         the deliverable
```

## Quickstart

```bash
./run.sh
# wrote output/result.json: 10 requirements, 1 conflicts, 8 register findings, budget=90000, llm=on
```

Requirements: Python 3.10+. No third-party runtime packages.

### Model providers

The call is made from `src/gatepass/llm.py` and tries, in order:

| Order | Configuration | Notes |
|---|---|---|
| 1 | `GATEPASS_LLM_URL` (+ `GATEPASS_LLM_KEY`) | any OpenAI-compatible `/chat/completions` endpoint |
| 2 | `OPENAI_API_KEY` | `gpt-4o-mini` by default |
| 3 | `ANTHROPIC_API_KEY` | `claude-3-5-haiku-latest` by default |
| 4 | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | `gemini-2.0-flash` by default |
| 5 | public endpoint | `text.pollinations.ai`, no key required |

`GATEPASS_LLM_MODEL` overrides the model. A successful response is cached in
`output/llm_cache.json` keyed by the exact prompt, so re-runs are deterministic and do not spend
quota. `--no-llm` forces the deterministic fallback. If every provider fails, the run still
completes and `output/provenance.json` records `"llm_used": false`.

### Why a language model here — and only here

The transcript is 50 informal, partly evasive answers. Deciding which sentences are
requirements, which two people contradict each other, and which questions were never actually
answered is a language task. Everything numeric or checkable — register findings, budget,
thresholds, plan, schema — is deterministic, so a reader can recompute it from the rows.

## Configuration

`config/factory.json` is the contract with the next factory:

```jsonc
{
  "plant": "...",
  "dataset": "data/register.json",
  "register": {
    "columns": {                 // map semantic names -> this export's column names
      "date": "date", "vehicle": "vehicle", "party": "party",
      "in_time": "in_time", "out_time": "out_time",
      "kind": "type", "invoice_no": "invoice_no", "chit_no": "chit_no", "guard": "guard"
    },
    "invoice_kinds": ["invoice"],
    "provisional_kinds": ["chit"],
    "night_window": ["20:00", "06:00"],
    "operating_days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
    "dwell_outlier_minutes": 120,
    "peak_window_hours": 3
  },
  "compliance": {
    "eway_bill_threshold_inr": 100000,
    "required_pass_fields": ["invoice_no", "date", "vehicle", "party", "in_time", "out_time", "eway_bill_no"]
  },
  "gate": { "connectivity": "none", "max_service_seconds": 60, "budget_inr": 90000, "multi_site": true },
  "architecture": {
    "plan": [ "..." ],
    "bill_of_materials_inr": { "rugged_android_tablet": 24000, "...": 0 },
    "resolution_policy": [ "..." ]
  }
}
```

If a required pass field is missing from the register, the pipeline says so in
`register_findings` and raises it as an open question instead of pretending compliance is
measurable.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

24 tests, standard library only:

- time/plate normalisation and rejection of bad times
- reference parsing, duplicate and sequence-gap detection
- configurable night window, operating days and compliance threshold
- empty register, negative dwell, missing out-time
- budget grounding against the transcript
- JSON extraction from fenced/prose-wrapped model output
- schema acceptance and rejection (extra field, bad enum, missing section)
- full offline CLI run, and clean failure on a missing input

## Design decisions

**Offline-first, not cloud-first.** A pass must print when the link is down, which is exactly
when the queue is longest. The gate reads a local cache; sync is opportunistic.

**Two modes, one record.** Day passes are invoice-backed; night passes are provisional chits
matched to an invoice the next morning. The night flow is not a workaround to be removed — it is
the operation, so it gets first-class controls: auto-numbering, the caller's name, and an
unmatched-chit queue.

**Compliance is a data problem, not a gate step.** The e-way bill number is fetched with the
invoice before the truck arrives, so the guard reads it rather than typing it. The owner's
"no extra minute" constraint holds.

**Reject.** Cloud printer/validator per pass, retyping from the invoice, RFID boom barrier,
biometric driver ID, Tally replacement, and a spreadsheet-first approach that only records after
the fact.

## Limits

- No Tally connector: the sync agent is specified, not built (no Tally export was available).
- The register cannot measure e-way compliance: no value column, no e-way column.
- Model claims are citation-checked, not entailment-checked; every shipped requirement was read
  against its source utterance.
- The ₹90,000 bill of materials is an estimate with ₹5,000 contingency, not a quote.
- Out of scope: driver identity, weighbridge integration, Tally write-back, roles, Bengali/Hindi UI.

## Data note

`data/interviews.json` and `data/register.json` are the fixtures served by the assessment gate,
reproduced here as the evidence behind `output/result.json`. The gate private key, access token
and any credentials are excluded via `.gitignore` and were never committed.

## Licence

Assessment work by Soumaditya Das. No licence granted for reuse of the interview fixtures.
