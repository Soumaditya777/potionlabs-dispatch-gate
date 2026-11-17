#!/usr/bin/env bash
# Builds output/result.json from the interview transcript and the register export.
# No arguments. No third-party dependencies. The model call is made from Python;
# if no provider is reachable the run falls back to deterministic extraction and
# says so in output/provenance.json.
set -euo pipefail

cd "$(dirname "$0")"
export PYTHONPATH="${PYTHONPATH:-}:src"
export PYTHONIOENCODING="utf-8"

mkdir -p output
python3 -m gatepass \
  --config config/factory.json \
  --interviews data/interviews.json \
  --register data/register.json \
  --schema data/output_schema.json \
  --out output/result.json \
  --provenance output/provenance.json "$@"
