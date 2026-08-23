# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import unittest

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

from context_budget import bound_messages  # noqa: E402


class TestContextBudget(unittest.TestCase):
    def test_keeps_recent_messages_and_redacts_paths(self):
        rows = [{"role": "assistant", "content": "welcome"}]
        rows.extend({"role": "user", "content": f"msg{i} /Users/chl/{i}-" + "x" * 20} for i in range(10))
        out = bound_messages(rows, max_messages=4, max_chars=200)
        self.assertLessEqual(len(out), 4)
        self.assertIn("msg9", out[-1]["content"])
        self.assertNotIn("/Users/", " ".join(m["content"] for m in out))

    def test_empty_is_stable(self):
        self.assertEqual(bound_messages([]), [])

    def test_redacts_precise_spatial_fields_without_consent(self):
        rows = [{
            "role": "assistant",
            "content": "bbox=(120.6,30.2,121.2,30.9) centroid=(120.9,30.55) "
            "地图中心: [30.55, 120.9] zoom=10",
        }]
        clean = bound_messages(rows)
        self.assertNotIn("120.6", clean[0]["content"])
        self.assertNotIn("30.55", clean[0]["content"])
        self.assertIn("<spatial-redacted>", clean[0]["content"])

        permitted = bound_messages(rows, allow_spatial_metadata=True)
        self.assertIn("120.6", permitted[0]["content"])
        self.assertIn("地图中心", permitted[0]["content"])


if __name__ == "__main__":
    unittest.main()
