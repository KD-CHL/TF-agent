#!/usr/bin/env python3
"""从 JSONL 文档构建/更新本地 Chroma 知识库（应用目录入口）。"""
from __future__ import annotations

import argparse
import json
import os
import sys

APP_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

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
    model_name = args.embedding_model or knowledge_embedding_model()
    try:
        import chromadb
        from chromadb.utils import embedding_functions
    except ImportError:
        raise SystemExit(
            "ChromaDB/embedding 依赖不可用；请安装 TF-agent/requirements.txt，"
            "或先使用 --dry-run 校验输入。"
        ) from None
    try:
        client = chromadb.PersistentClient(path=db)
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model_name)
        collection = client.get_or_create_collection(name=args.collection, embedding_function=ef)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"知识库初始化失败（{type(exc).__name__}）；请检查 embedding 模型缓存、数据库目录权限和模型配置。"
        ) from None
    try:
        result = ingest_documents(
            docs, collection=collection,
            manifest=KnowledgeManifest(os.path.join(db, "manifest.json")),
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"知识库入库失败（{type(exc).__name__}）；manifest 未被标记为成功，请检查本地 Chroma 状态后重试。"
        ) from None
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
