"""End-to-end and schema tests.

These run the real CLI in offline mode (no network) and then validate the file
it produced against the server's schema.  The shipped output is validated the
same way.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from gatepass.schema import load_schema, validate

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = load_schema(ROOT / "data" / "output_schema.json")


class SchemaTests(unittest.TestCase):
    def test_valid_result_passes(self):
        result = json.loads((ROOT / "output" / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(result, SCHEMA), [])

    def test_shipped_result_has_all_sections(self):
        result = json.loads((ROOT / "output" / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(result),
            {"requirements", "conflicts", "constraints", "budget_inr", "register_findings", "plan", "open_questions"},
        )
        self.assertGreaterEqual(len(result["requirements"]), 5)
        self.assertGreaterEqual(len(result["register_findings"]), 5)
        self.assertEqual(result["budget_inr"], 90000)
        for requirement in result["requirements"]:
            self.assertIn(requirement["source_persona"], {"dev", "anjali", "bablu", "assumed"})
            self.assertIn(requirement["status"], {"confirmed", "conflicting", "assumed"})

    def test_validator_rejects_extra_properties(self):
        broken = {
            "requirements": [{"id": "R1", "text": "x", "source_persona": "dev", "status": "confirmed", "extra": 1}],
            "conflicts": [], "constraints": [], "budget_inr": None,
            "register_findings": [], "plan": [], "open_questions": [],
        }
        errors = validate(broken, SCHEMA)
        self.assertTrue(any("unexpected property" in error for error in errors))

    def test_validator_rejects_bad_enum(self):
        broken = {
            "requirements": [{"id": "R1", "text": "x", "source_persona": "dev", "status": "maybe"}],
            "conflicts": [], "constraints": [], "budget_inr": None,
            "register_findings": [], "plan": [], "open_questions": [],
        }
        self.assertTrue(any("not in" in error for error in validate(broken, SCHEMA)))

    def test_validator_rejects_missing_section(self):
        self.assertTrue(validate({"requirements": []}, SCHEMA))


class EndToEndTests(unittest.TestCase):
    def test_offline_run_produces_schema_valid_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "result.json"
            prov = Path(tmp) / "provenance.json"
            process = subprocess.run(
                [
                    sys.executable, "-m", "gatepass",
                    "--config", "config/factory.json",
                    "--register", "data/register.json",
                    "--interviews", "data/interviews.json",
                    "--schema", "data/output_schema.json",
                    "--out", str(out),
                    "--provenance", str(prov),
                    "--no-llm",
                ],
                cwd=ROOT,
                env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin:/usr/local/bin"},
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(validate(result, SCHEMA), [])
            self.assertEqual(result["budget_inr"], 90000)
            self.assertEqual(len(result["register_findings"]), 8)
            provenance = json.loads(prov.read_text(encoding="utf-8"))
            self.assertFalse(provenance["llm_used"])
            self.assertIn("heuristics", " ".join(provenance["notes"]))

    def test_missing_input_fails_cleanly(self):
        process = subprocess.run(
            [sys.executable, "-m", "gatepass", "--register", "no-such-file.json", "--no-llm"],
            cwd=ROOT,
            env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin:/usr/local/bin"},
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("error:", process.stderr)


if __name__ == "__main__":
    unittest.main()
