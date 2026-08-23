#!/usr/bin/env python3
"""从 JSONL 文档构建本地 Chroma 知识库。"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "TF-agent"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from knowledge_store import (
    KnowledgeManifest, ingest_documents, knowledge_db_path, knowledge_embedding_model,
    load_jsonl_documents,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build/update CSTF knowledge base")
    parser.add_argument("input_jsonl")
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--embedding-model", default=None)
    parser.add_argument("--collection", default="remote_sensing_papers")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        docs = load_jsonl_documents(args.input_jsonl)
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    db = knowledge_db_path(args.db_path)
    if args.dry_run:
        print(json.dumps({
            "documents": len(docs),
            "db_path_configured": bool(db),
            "embedding_model": args.embedding_model or knowledge_embedding_model(),
            "collection": args.collection,
        }, ensure_ascii=False))
        return 0
    import chromadb
    from chromadb.utils import embedding_functions

    client = chromadb.PersistentClient(path=db)
    model_name = args.embedding_model or knowledge_embedding_model()
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model_name)
    collection = client.get_or_create_collection(name=args.collection, embedding_function=ef)
    try:
        result = ingest_documents(
            docs,
            collection=collection,
            manifest=KnowledgeManifest(os.path.join(db, "manifest.json")),
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
