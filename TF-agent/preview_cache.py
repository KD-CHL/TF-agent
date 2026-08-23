"""Bounded lifecycle helpers for uploaded chat preview files.

Preview images are local UI artefacts.  They must never be treated as durable
conversation attachments, and cleanup is deliberately restricted to files
created by the preview writer (``preview_*.png``) in the configured directory.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, Optional


_PREVIEW_PREFIX = "preview_"
_PREVIEW_SUFFIX = ".png"
_DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_DEFAULT_MAX_FILES = 200


def preview_cache_dir(app_dir: Optional[str] = None) -> str:
    """Return the private directory used for chat previews.

    ``CSTF_CHAT_PREVIEW_DIR`` is intended for tests and deployments that keep
    mutable data outside the source checkout.  The default remains compatible
    with the existing UI layout.
    """

    configured = os.environ.get("CSTF_CHAT_PREVIEW_DIR", "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    root = app_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.abspath(root), "_chat_upload_tmp", "_preview_cache")


def _preview_files(directory: Path):
    try:
        entries = list(directory.iterdir())
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return []
    files = []
    for entry in entries:
        # Never follow a link from a cleanup routine.  A symlink may point to
        # a user's original upload or another application-owned directory.
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            if not (entry.name.startswith(_PREVIEW_PREFIX) and entry.suffix.lower() == _PREVIEW_SUFFIX):
                continue
            stat = entry.stat()
            files.append((entry, float(stat.st_mtime), int(stat.st_size)))
        except OSError:
            continue
    return files


def cleanup_preview_cache(
    directory: Optional[str] = None,
    *,
    max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS,
    max_files: int = _DEFAULT_MAX_FILES,
    now: Optional[float] = None,
) -> Dict[str, int]:
    """Remove expired/excess preview files and return aggregate counters.

    Cleanup is best-effort: a concurrently opened file or a read-only cache
    must not break Streamlit startup.  The return value contains counts only,
    so UI logs do not expose local paths.
    """

    if max_age_seconds < 0:
        raise ValueError("max_age_seconds must be non-negative")
    if max_files < 0:
        raise ValueError("max_files must be non-negative")
    root = Path(directory or preview_cache_dir())
    if not root.exists() or not root.is_dir():
        return {"removed": 0, "bytes": 0, "failed": 0}

    current = float(time.time() if now is None else now)
    candidates = _preview_files(root)
    # Old files are removed first; among remaining files the oldest ones are
    # evicted when the bounded count is exceeded.
    candidates.sort(key=lambda item: item[1], reverse=True)
    keep = []
    removed = 0
    removed_bytes = 0
    failed = 0

    for entry, mtime, size in candidates:
        expired = current - mtime > max_age_seconds
        over_limit = len(keep) >= max_files
        if not expired and not over_limit:
            keep.append((entry, mtime, size))
            continue
        try:
            entry.unlink()
            removed += 1
            removed_bytes += max(0, size)
        except OSError:
            failed += 1

    return {"removed": removed, "bytes": removed_bytes, "failed": failed}


__all__ = ["cleanup_preview_cache", "preview_cache_dir"]
