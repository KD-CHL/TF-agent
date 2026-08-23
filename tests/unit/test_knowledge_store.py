# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import stat
from unittest.mock import patch

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

from knowledge_store import (  # noqa: E402
    KnowledgeDocument, KnowledgeManifest, ingest_documents, knowledge_db_path,
    knowledge_embedding_model, load_jsonl_documents,
)


class FakeCollection:
    def __init__(self):
        self.rows = {}
        self.upserts = 0
        self.deleted = []

    def upsert(self, *, ids, documents, metadatas):
        self.upserts += 1
        self.rows[ids[0]] = {"document": documents[0], "metadata": metadatas[0]}

    def delete(self, *, ids):
        self.deleted.extend(ids)
        for doc_id in ids:
            self.rows.pop(doc_id, None)


class TestKnowledgeStore(unittest.TestCase):
    def test_document_validation_and_checksum(self):
        doc = KnowledgeDocument.from_dict({"document_id": "d1", "source": "paper", "content": "潮滩"})
        self.assertEqual(len(doc.checksum), 64)
        with self.assertRaises(ValueError):
            KnowledgeDocument.from_dict({"source": "x", "content": ""})

    def test_declared_checksum_must_match_content(self):
        with self.assertRaisesRegex(ValueError, "checksum"):
            KnowledgeDocument.from_dict({
                "document_id": "d-bad-checksum",
                "content": "正文已经变化",
                "checksum": "0" * 64,
            })

    def test_idempotent_update_delete_and_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = KnowledgeManifest(os.path.join(td, "manifest.json"))
            collection = FakeCollection()
            doc1 = KnowledgeDocument.from_dict({"document_id": "d1", "source": "s", "content": "a"})
            doc2 = KnowledgeDocument.from_dict({"document_id": "d2", "source": "s", "content": "b"})
            first = ingest_documents([doc1, doc2], collection=collection, manifest=manifest)
            self.assertEqual(first["added"], 2)
            self.assertEqual(collection.upserts, 2)
            second = ingest_documents([doc1, doc2], collection=collection, manifest=manifest)
            self.assertEqual(second["skipped"], 2)
            self.assertEqual(collection.upserts, 2)
            changed = KnowledgeDocument.from_dict({"document_id": "d1", "source": "s2", "content": "changed"})
            third = ingest_documents([changed], collection=collection, manifest=manifest)
            self.assertEqual(third["updated"], 1)
            self.assertEqual(third["deleted"], 1)
            dry = ingest_documents([changed], collection=collection, manifest=manifest, dry_run=True)
            self.assertEqual(dry["skipped"], 1)

    def test_path_respects_environment(self):
        old = os.environ.get("CHROMA_RS_DB_PATH")
        os.environ["CHROMA_RS_DB_PATH"] = "/tmp/cstf-kb-test"
        try:
            self.assertEqual(knowledge_db_path(), "/tmp/cstf-kb-test")
        finally:
            if old is None:
                os.environ.pop("CHROMA_RS_DB_PATH", None)
            else:
                os.environ["CHROMA_RS_DB_PATH"] = old

    def test_embedding_model_respects_environment_without_loading_model(self):
        old = os.environ.get("CSTF_KB_EMBEDDING_MODEL")
        os.environ["CSTF_KB_EMBEDDING_MODEL"] = "local/test-embedding"
        try:
            self.assertEqual(knowledge_embedding_model(), "local/test-embedding")
        finally:
            if old is None:
                os.environ.pop("CSTF_KB_EMBEDDING_MODEL", None)
            else:
                os.environ["CSTF_KB_EMBEDDING_MODEL"] = old

    def test_corrupt_manifest_is_preserved_before_rebuild(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{broken json")
            manifest = KnowledgeManifest(path)
            with self.assertRaisesRegex(ValueError, "JSON 无效"):
                manifest.load()
            self.assertTrue(manifest.corruption_backup_path)
            self.assertTrue(os.path.exists(manifest.corruption_backup_path))
            manifest.save({"d1": {"checksum": "ok"}})
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["d1"]["checksum"], "ok")

    def test_jsonl_loader_rejects_duplicate_document_ids(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "docs.jsonl")
            rows = [
                {"document_id": "d1", "source": "s", "content": "a"},
                {"document_id": "d1", "source": "s", "content": "b"},
            ]
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(json.dumps(row, ensure_ascii=False) for row in rows))
            with self.assertRaisesRegex(ValueError, "重复 document_id"):
                load_jsonl_documents(path)

    def test_jsonl_loader_rejects_non_object_rows_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "docs.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("[]\n")
            with self.assertRaisesRegex(ValueError, "第 1 行无效"):
                load_jsonl_documents(path)

    def test_non_object_manifest_is_preserved_before_rebuild(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([{"document_id": "lost-if-overwritten"}], handle)
            manifest = KnowledgeManifest(path)
            with self.assertRaisesRegex(ValueError, "顶层结构无效"):
                manifest.load()
            self.assertTrue(manifest.corruption_backup_path)
            self.assertTrue(os.path.exists(manifest.corruption_backup_path))

    def test_malformed_manifest_entry_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"bad": "not-an-object"}, handle)
            manifest = KnowledgeManifest(path)
            with self.assertRaises(ValueError):
                manifest.load()
            self.assertTrue(manifest.corruption_backup_path)
            self.assertTrue(os.path.isfile(manifest.corruption_backup_path))

    def test_manifest_save_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "manifest.json")
            manifest = KnowledgeManifest(path)
            manifest.save({"d1": {"checksum": "ok"}})
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertFalse([name for name in os.listdir(td) if name.endswith(".tmp")])

    def test_manifest_save_does_not_require_posix_fchmod(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "manifest.json")
            manifest = KnowledgeManifest(path)
            with patch("knowledge_store.os.fchmod", None, create=True):
                manifest.save({"d1": {"checksum": "ok"}})
            self.assertTrue(os.path.isfile(path))

    def test_manifest_save_rejects_non_object_entries(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = KnowledgeManifest(os.path.join(td, "manifest.json"))
            with self.assertRaises(ValueError):
                manifest.save({"bad": "not-an-object"})


if __name__ == "__main__":
    unittest.main()
