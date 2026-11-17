"""Tests for register ingestion and analysis.

The point of these tests is that the analysis is data-driven: the same code,
given a different configuration and rows, must produce different findings.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gatepass.config import load_config
from gatepass.dataset import load_register, normalise_plate, parse_clock
from gatepass.register import analyse, reference_integrity, split_reference

ROOT = Path(__file__).resolve().parents[1]


def make_config(overrides: dict | None = None) -> dict:
    config = json.loads((ROOT / "config" / "factory.json").read_text(encoding="utf-8"))
    if overrides:
        for path, value in overrides.items():
            node = config
            keys = path.split(".")
            for key in keys[:-1]:
                node = node[key]
            node[keys[-1]] = value
    return config


class TimeAndPlateTests(unittest.TestCase):
    def test_parse_clock(self):
        self.assertEqual(parse_clock("09:05"), 545)
        self.assertEqual(parse_clock("23:59:30"), 1439)
        self.assertIsNone(parse_clock(""))
        self.assertIsNone(parse_clock(None))

    def test_parse_clock_rejects_garbage(self):
        with self.assertRaises(Exception):
            parse_clock("half past nine")
        with self.assertRaises(Exception):
            parse_clock("25:00")

    def test_plate_normalisation(self):
        self.assertEqual(normalise_plate(" wb-25-b 6797 "), "WB 25 B 6797")


class ReferenceTests(unittest.TestCase):
    def test_split_reference(self):
        self.assertEqual(split_reference("GP/26-27/2203"), ("GP/26-27/", 2203))
        self.assertEqual(split_reference("C-48"), ("C-", 48))
        self.assertIsNone(split_reference("no-number"))

    def test_loader_unwraps_fixture_envelope(self):
        cfg_raw = make_config()
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps(cfg_raw), encoding="utf-8")
            cfg = load_config(cfg_path)
            register = load_register(ROOT / "data" / "register.json", cfg)
        self.assertEqual(len(register.movements), 129)
        self.assertEqual(register.movements[0].vehicle, "WB 25 B 6797")
        self.assertEqual(register.movements[0].dwell_minutes, 22)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cfg = load_config(ROOT / "config" / "factory.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rows, config=None):
        cfg_path = self.dir / "config.json"
        data = config or make_config()
        cfg_path.write_text(json.dumps(data), encoding="utf-8")
        cfg = load_config(cfg_path)
        path = self.dir / "register.json"
        path.write_text(json.dumps({"source": "test", "rows": rows}), encoding="utf-8")
        return load_register(path, cfg), cfg

    def rows(self):
        base = [
            ("2026-11-02", "WB 01 A 1111", "Alpha", "09:00", "09:30", "invoice", "INV/1/100", None, "G1"),
            ("2026-11-02", "WB 01 A 2222", "Beta", "21:00", "21:20", "chit", None, "C-1", "G2"),
            ("2026-11-03", "WB 01 A 3333", "Alpha", "10:15", "11:45", "invoice", "INV/1/101", None, "G1"),
            ("2026-11-04", "WB 01 A 4444", "Gamma", "22:10", "22:40", "chit", "INV/1/100", "C-2", "G2"),
        ]
        return [
            {
                "date": r[0], "vehicle": r[1], "party": r[2], "in_time": r[3], "out_time": r[4],
                "type": r[5], "invoice_no": r[6], "chit_no": r[7], "guard": r[8],
            }
            for r in base
        ]

    def test_findings_are_computed_from_rows(self):
        register, cfg = self.write(self.rows(), make_config({"register.operating_days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}))
        findings, metrics = analyse(register, cfg)
        text = " ".join(findings)
        self.assertEqual(metrics["movements"], 4)
        self.assertEqual(metrics["provisional_movements"], 2)
        self.assertEqual(metrics["duplicate_invoice_refs"], 1)
        self.assertIn("INV/1/100", text)
        self.assertIn("50%", text)  # two of four movements are chits
        self.assertEqual(metrics["unique_vehicles"], 4)

    def test_night_window_is_configurable(self):
        register, cfg = self.write(self.rows(), make_config({"register.night_window": ["00:00", "06:00"]}))
        findings, metrics = analyse(register, cfg)
        self.assertEqual(metrics["night_movements"], 0)
        self.assertNotIn("night movements", " ".join(findings))

    def test_missing_compliance_field_is_reported(self):
        register, cfg = self.write(self.rows())
        findings, metrics = analyse(register, cfg)
        self.assertIn("e-way bill number", " ".join(findings))
        self.assertTrue(any("eway" in item for item in metrics["missing_required_fields"]))

    def test_negative_dwell_is_counted_not_crashed(self):
        rows = self.rows()
        rows[0]["out_time"] = "08:00"
        register, cfg = self.write(rows)
        findings, metrics = analyse(register, cfg)
        self.assertEqual(metrics["dwell_negative"], 1)
        self.assertTrue(any("out-time before the in-time" in f for f in findings))

    def test_empty_register_is_handled(self):
        register, cfg = self.write([])
        findings, metrics = analyse(register, cfg)
        self.assertEqual(len(findings), 1)
        self.assertEqual(metrics["movements"], 0)


if __name__ == "__main__":
    unittest.main()
