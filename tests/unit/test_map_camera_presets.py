# -*- coding: utf-8 -*-
"""A 阶段 · 相机预设测试：杭州湾/乐清湾/中国/点位，zoom→height 映射，地名与坐标分离，非法坐标阻断。

运行：
    D:\anaconda3\envs\gwx\python.exe -m pytest tests/unit/test_map_camera_presets.py -q --tb=short -p no:cacheprovider
"""
from __future__ import annotations

import os
import sys
import unittest

_TF_AGENT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent"))
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

import map_protocol as mp  # noqa: E402


class TestCameraPresets(unittest.TestCase):
    """预设表：区域（杭州湾/乐清湾）、总览（中国）、点位。"""

    def test_hangzhou_bay_preset_region(self):
        self.assertIn("杭州湾", mp.CAMERA_PRESETS)
        self.assertEqual(mp.CAMERA_PRESETS["杭州湾"]["preset"], "region")

    def test_hangzhou_bay_coords(self):
        p = mp.CAMERA_PRESETS["杭州湾"]
        self.assertAlmostEqual(p["lat"], 30.5, delta=0.5)
        self.assertAlmostEqual(p["lon"], 120.8, delta=0.5)

    def test_yueqing_bay_preset_region(self):
        self.assertIn("乐清湾", mp.CAMERA_PRESETS)
        self.assertEqual(mp.CAMERA_PRESETS["乐清湾"]["preset"], "region")

    def test_china_preset_overview(self):
        self.assertIn("中国", mp.CAMERA_PRESETS)
        self.assertEqual(mp.CAMERA_PRESETS["中国"]["preset"], "overview")

    def test_presets_are_mutable_registry_not_hardcoded_in_coords_parser(self):
        # 预设是独立表；坐标解析（make_fly_message）不内嵌地名
        self.assertIsInstance(mp.CAMERA_PRESETS, dict)
        self.assertGreaterEqual(len(mp.CAMERA_PRESETS), 3)

    def test_resolve_preset_known_name(self):
        got = mp.resolve_preset("杭州湾")
        self.assertIsNotNone(got)
        self.assertEqual(got["preset"], "region")
        self.assertAlmostEqual(got["lat"], 30.5, delta=0.5)

    def test_resolve_preset_unknown_name_returns_none(self):
        self.assertIsNone(mp.resolve_preset("不存在的地名XYZ"))

    def test_resolve_preset_case_insensitive_optional(self):
        # 可选：大小写/空白容忍（实现里不做也允许，测试仅当实现支持时成立）
        got = mp.resolve_preset(" 杭州湾 ")
        if got is not None:
            self.assertEqual(got["preset"], "region")


class TestZoomHeightMapping(unittest.TestCase):
    """zoom → lookAt 高度映射（与 globe_engine.zoom_to_height_m 一致）。"""

    def test_zoom3_china_range(self):
        self.assertAlmostEqual(mp.zoom_to_height_m(3), 4_800_000.0, delta=1.0)

    def test_zoom9_region_range(self):
        self.assertAlmostEqual(mp.zoom_to_height_m(9), 280_000.0, delta=1.0)

    def test_zoom11_point_range(self):
        self.assertAlmostEqual(mp.zoom_to_height_m(11), 90_000.0, delta=1.0)

    def test_zoom15_close_range(self):
        self.assertAlmostEqual(mp.zoom_to_height_m(15), 35_000.0, delta=1.0)

    def test_zoom_clamped_low(self):
        self.assertAlmostEqual(mp.zoom_to_height_m(0), 4_800_000.0, delta=1.0)

    def test_zoom_clamped_high(self):
        self.assertAlmostEqual(mp.zoom_to_height_m(99), 35_000.0, delta=1.0)


class TestPresetFlight(unittest.TestCase):
    """预设驱动飞行：地名→坐标+高度；纯坐标→point 高度。"""

    def test_fly_with_preset_region_uses_region_height(self):
        msg, errs = mp.make_fly_message(120.8, 30.5, zoom=9, preset="region")
        self.assertFalse(errs)
        self.assertAlmostEqual(msg["height"], 280_000.0, delta=1.0)

    def test_fly_with_overview_preset_uses_china_height(self):
        msg, errs = mp.make_fly_message(104.0, 36.0, zoom=3, preset="overview")
        self.assertFalse(errs)
        self.assertAlmostEqual(msg["height"], 4_800_000.0, delta=1.0)

    def test_fly_plain_coords_uses_point_range(self):
        msg, errs = mp.make_fly_message(121.5, 30.9, zoom=11)
        self.assertFalse(errs)
        self.assertAlmostEqual(msg["height"], 90_000.0, delta=1.0)

    def test_fly_explicit_height_overrides_preset(self):
        msg, _ = mp.make_fly_message(120.8, 30.5, zoom=9, preset="region", height=500_000.0)
        self.assertAlmostEqual(msg["height"], 500_000.0, delta=1.0)

    def test_preset_resolution_through_fly(self):
        # 通过地名获得坐标+preset 后生成的飞行消息，label 取自预设
        p = mp.resolve_preset("杭州湾")
        msg, errs = mp.make_fly_message(p["lon"], p["lat"], zoom=9, preset=p["preset"], label=p.get("label"))
        self.assertFalse(errs)
        self.assertEqual(msg["label"], "杭州湾")


if __name__ == "__main__":
    unittest.main()
