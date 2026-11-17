"""Command line entry point for the dispatch-gate brief compiler."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .brief import build_brief
from .config import ConfigError, load_config
from .dataset import load_register
from .interviews import load_interviews
from .schema import load_schema


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gatepass",
        description="Compile gate interviews and a register export into a validated dispatch brief.",
    )
    parser.add_argument("--config", default="config/factory.json", help="plant configuration")
    parser.add_argument("--register", default=None, help="register export (defaults to config dataset)")
    parser.add_argument("--interviews", default="data/interviews.json", help="interview transcript")
    parser.add_argument("--schema", default="data/output_schema.json", help="output JSON schema")
    parser.add_argument("--out", default="output/result.json", help="result path")
    parser.add_argument("--provenance", default="output/provenance.json", help="run metadata path")
    parser.add_argument("--llm-cache", default="output/llm_cache.json", help="reuse a matching model response")
    parser.add_argument("--no-llm", action="store_true", help="skip the model call and use deterministic extraction")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cfg = load_config(args.config)
        register = load_register(args.register or cfg.dataset, cfg)
        utterances = load_interviews(args.interviews)
        schema = load_schema(args.schema)
        brief, report = build_brief(
            utterances=utterances,
            register=register,
            cfg=cfg,
            output_schema=schema,
            use_llm=not args.no_llm,
            llm_cache=args.llm_cache,
        )
    except (ConfigError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(brief, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    provenance = Path(args.provenance)
    provenance.parent.mkdir(parents=True, exist_ok=True)
    provenance.write_text(
        json.dumps(
            {
                "config": args.config,
                "register": args.register or cfg.dataset,
                "interviews": args.interviews,
                "schema": args.schema,
                "movements": len(register.movements),
                "utterances": len(utterances),
                "requirements": len(brief["requirements"]),
                "conflicts": len(brief["conflicts"]),
                "register_findings": len(brief["register_findings"]),
                **report.as_dict(),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"wrote {out}: {len(brief['requirements'])} requirements, {len(brief['conflicts'])} conflicts, "
        f"{len(brief['register_findings'])} register findings, budget={brief['budget_inr']}, "
        f"llm={'on' if report.llm_used else 'off'}"
        + (f" ({report.llm_provider}/{report.llm_model})" if report.llm_used else "")
    )
    for note in report.notes:
        print(f"note: {note}", file=sys.stderr)
    for dropped in report.dropped:
        print(f"dropped: {dropped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
