# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

from preview_cache import cleanup_preview_cache, preview_cache_dir  # noqa: E402


class TestPreviewCache(unittest.TestCase):
    def test_cleanup_removes_expired_previews_and_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            old = cache / "preview_old.png"
            recent = cache / "preview_recent.png"
            unrelated = cache / "keep.txt"
            old.write_bytes(b"old")
            recent.write_bytes(b"recent")
            unrelated.write_bytes(b"keep")
            now = time.time()
            os.utime(old, (now - 10_000, now - 10_000))

            result = cleanup_preview_cache(str(cache), max_age_seconds=3600, now=now)

            self.assertEqual(result["removed"], 1)
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(unrelated.exists())

    def test_cleanup_enforces_file_limit_by_removing_oldest_preview(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            for index in range(3):
                path = cache / f"preview_{index}.png"
                path.write_bytes(str(index).encode())
                os.utime(path, (100 + index, 100 + index))

            result = cleanup_preview_cache(str(cache), max_age_seconds=10_000, max_files=2, now=200)

            self.assertEqual(result["removed"], 1)
            self.assertFalse((cache / "preview_0.png").exists())
            self.assertTrue((cache / "preview_1.png").exists())
            self.assertTrue((cache / "preview_2.png").exists())

    def test_cleanup_does_not_follow_symlinks_or_delete_outside_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "cache"
            cache.mkdir()
            outside = root / "outside.png"
            outside.write_bytes(b"outside")
            link = cache / "preview_link.png"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")

            result = cleanup_preview_cache(str(cache), max_age_seconds=0, now=time.time() + 1)

            self.assertEqual(result["removed"], 0)
            self.assertTrue(link.is_symlink())
            self.assertTrue(outside.exists())

    def test_preview_cache_dir_uses_explicit_configuration(self):
        old = os.environ.get("CSTF_CHAT_PREVIEW_DIR")
        try:
            os.environ["CSTF_CHAT_PREVIEW_DIR"] = "/tmp/cstf-preview-test"
            self.assertEqual(preview_cache_dir(), os.path.abspath("/tmp/cstf-preview-test"))
        finally:
            if old is None:
                os.environ.pop("CSTF_CHAT_PREVIEW_DIR", None)
            else:
                os.environ["CSTF_CHAT_PREVIEW_DIR"] = old


if __name__ == "__main__":
    unittest.main()
