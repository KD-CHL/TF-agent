# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

import train_agent  # noqa: E402


class TestTrainAgentCli(unittest.TestCase):
    def test_import_has_no_training_side_effect_and_schema_is_validated(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "data.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"instruction": "a", "output": "b"}) + "\n")
            self.assertEqual(train_agent.validate_training_data(path), [])
            self.assertEqual(train_agent.main(["--data-path", path, "--dry-run"]), 0)

    def test_invalid_schema_is_reported_without_model_import(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "bad.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"instruction": "only"}) + "\n")
            errors = train_agent.validate_training_data(path)
        self.assertTrue(errors)
        self.assertTrue(any("output" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
