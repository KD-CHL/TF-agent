# -*- coding: utf-8 -*-
"""知识库文档 manifest 与幂等入库工具。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


class ManifestCorruptError(ValueError):
    """Raised when a manifest cannot be safely used for an incremental build."""


def knowledge_db_path(default_root: Optional[str] = None) -> str:
    root = os.environ.get("CHROMA_RS_DB_PATH") or default_root or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "rs_knowledge_db"
    )
    return os.path.abspath(os.path.expanduser(root))


def knowledge_embedding_model() -> str:
    """Return the configured embedding model without loading or downloading it."""
    return (os.environ.get("CSTF_KB_EMBEDDING_MODEL") or "BAAI/bge-small-zh-v1.5").strip()


@dataclass(frozen=True)
class KnowledgeDocument:
    document_id: str
    source: str
    title: str
    published_at: str
    checksum: str
    content: str

    @classmethod
    def from_dict(cls, row: Dict[str, Any]) -> "KnowledgeDocument":
        if not isinstance(row, dict):
            raise ValueError("文档记录必须是 JSON object。")
        content = str(row.get("content") or row.get("text") or "").strip()
        source = str(row.get("source") or "未知来源").strip()
        title = str(row.get("title") or row.get("document_id") or "未命名文档").strip()
        document_id = str(row.get("document_id") or row.get("id") or "").strip()
        if not document_id or not content:
            raise ValueError("文档必须包含非空 document_id 和 content。")
        computed_checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
        declared_checksum = str(row.get("checksum") or "").strip().lower()
        if declared_checksum and declared_checksum != computed_checksum:
            raise ValueError("文档 checksum 与 content 不匹配。")
        checksum = computed_checksum
        return cls(
            document_id=document_id,
            source=source,
            title=title,
            published_at=str(row.get("published_at") or "").strip(),
            checksum=checksum,
            content=content,
        )

    def metadata(self) -> Dict[str, str]:
        return {
            "document_id": self.document_id,
            "source": self.source,
            "title": self.title,
            "published_at": self.published_at,
            "checksum": self.checksum,
        }


class KnowledgeManifest:
    def __init__(self, path: str):
        self.path = os.path.abspath(os.path.expanduser(path))
        self.corruption_backup_path: Optional[str] = None
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)

    def load(self) -> Dict[str, Dict[str, Any]]:
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                if not all(isinstance(entry, dict) for entry in data.values()):
                    self.corruption_backup_path = self._preserve_corrupt_manifest()
                    raise ManifestCorruptError(
                        "知识库 manifest 记录结构无效；原文件已保留，已停止增量写入。"
                    )
                return data
            self.corruption_backup_path = self._preserve_corrupt_manifest()
            raise ManifestCorruptError(
                "知识库 manifest 顶层结构无效；原文件已保留，已停止增量写入。"
            )
        except (json.JSONDecodeError, UnicodeError) as exc:
            self.corruption_backup_path = self._preserve_corrupt_manifest()
            raise ManifestCorruptError(
                "知识库 manifest JSON 无效；原文件已保留，已停止增量写入。"
            ) from exc
        except OSError:
            raise

    def _preserve_corrupt_manifest(self) -> Optional[str]:
        if not os.path.isfile(self.path):
            return None
        stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
        backup = f"{self.path}.corrupt-{stamp}"
        suffix = 1
        while os.path.exists(backup):
            backup = f"{self.path}.corrupt-{stamp}-{suffix}"
            suffix += 1
        try:
            shutil.copy2(self.path, backup)
            return backup
        except OSError:
            return None

    def save(self, data: Dict[str, Dict[str, Any]]) -> None:
        if not isinstance(data, dict) or any(not isinstance(entry, dict) for entry in data.values()):
            raise ValueError("知识库 manifest 必须是 document_id 到 object 记录的映射。")
        directory = os.path.dirname(self.path) or "."
        fd, temp = tempfile.mkstemp(
            prefix=f".{os.path.basename(self.path)}.", suffix=".tmp", dir=directory
        )
        try:
            # ``fchmod`` is not exposed by every supported platform (notably
            # some Windows Python builds); ``mkstemp`` still creates a private
            # descriptor there, while POSIX keeps the explicit 0600 mode.
            chmod = getattr(os, "fchmod", None)
            if callable(chmod):
                chmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise


def load_jsonl_documents(path: str) -> List[KnowledgeDocument]:
    """Load and validate a JSONL source, rejecting duplicate document IDs."""
    documents: List[KnowledgeDocument] = []
    seen: set[str] = set()
    with open(path, "r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                document = KnowledgeDocument.from_dict(json.loads(line))
            except (ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"第 {line_no} 行无效：{exc}") from None
            if document.document_id in seen:
                raise ValueError(f"第 {line_no} 行重复 document_id：{document.document_id}")
            seen.add(document.document_id)
            documents.append(document)
    return documents


def ingest_documents(
    documents: Iterable[KnowledgeDocument],
    *,
    collection: Any,
    manifest: KnowledgeManifest,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """调用 Chroma-compatible collection.upsert/delete；相同 checksum 不重复向量化。"""
    existing = manifest.load()
    incoming = {doc.document_id: doc for doc in documents}
    added = updated = skipped = 0
    if not dry_run:
        stale = set(existing) - set(incoming)
        if stale and hasattr(collection, "delete"):
            collection.delete(ids=sorted(stale))
        for doc_id, doc in incoming.items():
            old = existing.get(doc_id) or {}
            if old.get("checksum") == doc.checksum:
                skipped += 1
                continue
            collection.upsert(
                ids=[doc.document_id],
                documents=[doc.content],
                metadatas=[doc.metadata()],
            )
            if old:
                updated += 1
            else:
                added += 1
        manifest.save({doc_id: asdict(doc) for doc_id, doc in incoming.items()})
    else:
        for doc_id, doc in incoming.items():
            if (existing.get(doc_id) or {}).get("checksum") == doc.checksum:
                skipped += 1
            elif doc_id in existing:
                updated += 1
            else:
                added += 1
    return {"added": added, "updated": updated, "skipped": skipped, "deleted": max(0, len(set(existing) - set(incoming)))}


__all__ = [
    "KnowledgeDocument", "KnowledgeManifest", "ManifestCorruptError", "ingest_documents", "knowledge_db_path",
    "load_jsonl_documents",
    "knowledge_embedding_model",
]
