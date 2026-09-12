# -*- coding: utf-8 -*-
"""E1 评价范围契约：无显式 ROI 时不得退化为全国超大网格。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
_JB_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "research", "jb")
)
for _path in (_TF_AGENT, _JB_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from E1 import _vector_bounds_4326  # noqa: E402
import e1_engine  # noqa: E402


class _FakeGeoDataFrame:
    crs = "EPSG:32651"
    total_bounds = (380330.0, 4509150.0, 412940.0, 4540850.0)

    def to_crs(self, crs):
        self.crs = crs
        self.total_bounds = (120.10, 40.70, 120.55, 41.02)
        return self


class TestE1EngineContract(unittest.TestCase):
    def test_target_bounds_are_transformed_to_wgs84(self):
        with mock.patch("E1.gpd.read_file", return_value=_FakeGeoDataFrame()):
            bounds = _vector_bounds_4326("target.shp")
        self.assertEqual(bounds, (120.10, 40.70, 120.55, 41.02))

    def test_invalid_target_bounds_return_none_for_safe_fallback(self):
        class Empty:
            crs = None
            total_bounds = (0.0, 0.0, 0.0, 0.0)

        with mock.patch("E1.gpd.read_file", return_value=Empty()):
            self.assertIsNone(_vector_bounds_4326("target.shp"))

    def test_loaded_legacy_report_gets_disk_paths_for_verification(self):
        with unittest.mock.patch("builtins.open", mock.mock_open(read_data='{"roi_name":"task","comparisons":{}}')):
            with unittest.mock.patch("os.path.isfile", return_value=True):
                report = e1_engine.load_e1_report("/tmp/e1_workspace", "task")
        self.assertEqual(
            report["report_path"],
            "/tmp/e1_workspace/outputs_e1/E1_PIXEL_REPORT_task.json",
        )
        self.assertEqual(
            report["report_md_path"],
            "/tmp/e1_workspace/outputs_e1/E1_PIXEL_REPORT_task.md",
        )


if __name__ == "__main__":
    unittest.main()
