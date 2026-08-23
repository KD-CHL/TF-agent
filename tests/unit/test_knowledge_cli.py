from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

from scripts import build_knowledge_base  # noqa: E402


class TestKnowledgeCli(unittest.TestCase):
    def test_dry_run_accepts_explicit_model_and_collection(self):
        with tempfile.TemporaryDirectory() as td:
            source = os.path.join(td, "docs.jsonl")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"document_id": "d1", "content": "潮滩"}, ensure_ascii=False))
            output = StringIO()
            with redirect_stdout(output):
                code = build_knowledge_base.main([
                    source, "--db-path", os.path.join(td, "db"),
                    "--embedding-model", "local/test-model",
                    "--collection", "test_collection", "--dry-run",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["embedding_model"], "local/test-model")
            self.assertEqual(payload["collection"], "test_collection")

    def test_non_dry_run_missing_chroma_is_actionable(self):
        with tempfile.TemporaryDirectory() as td:
            source = os.path.join(td, "docs.jsonl")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"document_id": "d1", "content": "潮滩"}, ensure_ascii=False))
            with patch.dict(sys.modules, {"chromadb": None}):
                with self.assertRaisesRegex(SystemExit, "ChromaDB/embedding 依赖不可用"):
                    build_knowledge_base.main([source, "--db-path", os.path.join(td, "db")])
