"""Tests for interview grounding: budget parsing and citation enforcement."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gatepass.interviews import load_interviews, sanctioned_budget
from gatepass.llm import extract_json

ROOT = Path(__file__).resolve().parents[1]


class InterviewTests(unittest.TestCase):
    def setUp(self):
        self.utterances = load_interviews(ROOT / "data" / "interviews.json")

    def test_transcript_loads_with_ids_and_personas(self):
        self.assertGreaterEqual(len(self.utterances), 20)
        ids = [u.id for u in self.utterances]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({u.persona for u in self.utterances}, {"dev", "anjali", "bablu"})
        self.assertTrue(all(u.answer for u in self.utterances))

    def test_budget_comes_from_the_transcript(self):
        self.assertEqual(sanctioned_budget(self.utterances), 90000)

    def test_budget_is_none_when_nobody_says_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "interviews.json"
            path.write_text(
                json.dumps({"interviews": [{"id": "u01", "persona": "dev", "answer": "No idea, ask accounts."}]}),
                encoding="utf-8",
            )
            utterances = load_interviews(path)
        self.assertIsNone(sanctioned_budget(utterances))


class JSONExtractionTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_json(self):
        self.assertEqual(extract_json('```json\n{"a": [1, 2]}\n```'), {"a": [1, 2]})

    def test_json_with_prose_around_it(self):
        text = 'Here is the brief:\n{"a": {"b": "brace } inside"}}\nHope this helps.'
        self.assertEqual(extract_json(text), {"a": {"b": "brace } inside"}})

    def test_no_json_raises(self):
        with self.assertRaises(Exception):
            extract_json("I could not do that.")


if __name__ == "__main__":
    unittest.main()
