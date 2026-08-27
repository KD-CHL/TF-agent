# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

import place_geocoder as geo  # noqa: E402


class TestDirectMapPlaceExtraction(unittest.TestCase):
    def test_extracts_arbitrary_chinese_and_english_places(self):
        self.assertEqual(geo.extract_direct_map_place("聚焦到香港"), "香港")
        self.assertEqual(geo.extract_direct_map_place("请帮我把地图定位到 New York"), "New York")
        self.assertEqual(geo.extract_direct_map_place("跳转至杭州市西湖区附近"), "杭州市西湖区")

    def test_rejects_questions_and_combined_tasks(self):
        self.assertIsNone(geo.extract_direct_map_place("杭州在哪里？"))
        self.assertIsNone(geo.extract_direct_map_place("查看杭州天气"))
        self.assertIsNone(geo.extract_direct_map_place("聚焦杭州并运行当前任务"))


class TestPlaceGeocoder(unittest.TestCase):
    def setUp(self):
        geo._reset_cache_for_tests()

    def test_geocodes_and_caches_without_real_network(self):
        payload = [
            {
                "lat": "48.8582602",
                "lon": "2.2944991",
                "display_name": "埃菲尔铁塔, 巴黎, 法国",
                "addresstype": "tourism",
            }
        ]
        with mock.patch.object(geo, "_fetch_nominatim", return_value=payload) as fetch:
            first, first_error = geo.geocode_place("埃菲尔铁塔")
            second, second_error = geo.geocode_place("埃菲尔铁塔")

        self.assertIsNone(first_error)
        self.assertIsNone(second_error)
        self.assertEqual(first["lat"], 48.8582602)
        self.assertEqual(first["lon"], 2.2944991)
        self.assertEqual(first["zoom"], 15)
        self.assertIn("OpenStreetMap", first["attribution"])
        self.assertEqual(first, second)
        fetch.assert_called_once()

    def test_no_result_returns_clear_error(self):
        with mock.patch.object(geo, "_fetch_nominatim", return_value=[]):
            result, error = geo.geocode_place("不存在的地名XYZ")
        self.assertIsNone(result)
        self.assertIn("未找到地名", error)


if __name__ == "__main__":
    unittest.main()
