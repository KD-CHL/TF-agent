# -*- coding: utf-8 -*-
"""Phase D: AOI 上下文 — aoi_context.py 的 TDD 测试（结构/校验/geodesic 面积/摘要）。"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

from aoi_context import (  # noqa: E402
    AOIContext,
    aoi_from_bbox,
    aoi_from_click,
    aoi_from_current_view,
    compact_summary,
    geodesic_area_km2,
    validate_aoi,
)


def _polygon(coords):
    return {"type": "Polygon", "coordinates": [coords]}


class TestAOIContextModel:
    def test_from_bbox_creates_valid(self):
        aoi = aoi_from_bbox(120.6, 30.2, 121.2, 30.9, label="杭州湾北岸")
        assert aoi.valid is True
        assert aoi.source == "map_rectangle"
        assert aoi.label == "杭州湾北岸"
        assert aoi.crs == "EPSG:4326"
        assert aoi.aoi_id
        # bbox 原样保留
        assert aoi.bbox == (120.6, 30.2, 121.2, 30.9)
        # 质心正确
        assert abs(aoi.centroid[0] - 120.9) < 1e-6
        assert abs(aoi.centroid[1] - 30.55) < 1e-6

    def test_rectangle_polygon_ring_closed(self):
        aoi = aoi_from_bbox(120.6, 30.2, 121.2, 30.9)
        ring = aoi.geometry["coordinates"][0]
        assert ring[0] == ring[-1]  # 环闭合
        assert len(ring) == 5  # 4 角 + 闭合点

    def test_click_creates_small_box(self):
        aoi = aoi_from_click(120.8, 30.5)
        assert aoi.source == "map_click"
        assert abs(aoi.centroid[0] - 120.8) < 1e-9
        assert abs(aoi.centroid[1] - 30.5) < 1e-9
        # ±0.002° 小方框
        ring = aoi.geometry["coordinates"][0]
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        assert max(lons) - min(lons) <= 0.004 + 1e-9
        assert max(lats) - min(lats) <= 0.004 + 1e-9

    def test_current_view_source(self):
        aoi = aoi_from_current_view(110.0, 20.0, 125.0, 40.0)
        assert aoi.source == "current_view"


class TestValidation:
    def test_nan_coords_invalid(self):
        ring = [[float("nan"), 30.5], [121.0, 30.5], [121.0, 31.0], [120.8, 31.0], [float("nan"), 30.5]]
        aoi = validate_aoi(_polygon(ring), source="map_polygon")
        assert aoi.valid is False
        assert any("有限" in w or "NaN" in w or "Inf" in w for w in aoi.warnings)

    def test_inf_coords_invalid(self):
        ring = [[120.8, float("inf")], [121.0, 30.5], [121.0, 31.0], [120.8, 31.0], [120.8, float("inf")]]
        aoi = validate_aoi(_polygon(ring), source="map_polygon")
        assert aoi.valid is False

    def test_fewer_than_3_points_invalid(self):
        ring = [[120.8, 30.5], [121.0, 31.0], [120.8, 30.5]]
        aoi = validate_aoi(_polygon(ring), source="map_polygon")
        assert aoi.valid is False
        assert any("3" in w for w in aoi.warnings)

    def test_bbox_out_of_range_invalid(self):
        aoi = aoi_from_bbox(170.0, 30.0, 190.0, 40.0)  # lon 越界
        assert aoi.valid is False

    def test_self_intersection_repaired_with_warning(self):
        # 蝴蝶结自相交多边形
        ring = [[120.8, 30.5], [121.2, 31.0], [120.8, 31.0], [121.2, 30.5], [120.8, 30.5]]
        aoi = validate_aoi(_polygon(ring), source="map_polygon")
        assert aoi.valid is True  # make_valid 修复成功
        assert any("自相交" in w for w in aoi.warnings)

    def test_vertex_limit_warns(self):
        ring = [[120.8 + i * 0.001, 30.5] for i in range(2001)]
        ring.append(ring[0])
        aoi = validate_aoi(_polygon(ring), source="map_polygon", max_vertices=2000)
        assert any("顶点" in w or "降采样" in w for w in aoi.warnings)


class TestGeodesicArea:
    def test_area_positive_geodesic(self):
        # 杭州湾约 0.5°×0.5° @ 30.5°N → 约 2670 km²
        aoi = aoi_from_bbox(120.6, 30.2, 121.1, 30.7)
        assert aoi.area_km2 > 2000
        assert aoi.area_km2 < 3500

    def test_area_geodesic_differs_from_planar(self):
        # 10°×10° @ 30°N：geodesic 与平面近似差异 >5%
        ring = [[110.0, 30.0], [120.0, 30.0], [120.0, 40.0], [110.0, 40.0], [110.0, 30.0]]
        geo = geodesic_area_km2([tuple(p) for p in ring])
        planar = (10 * 111.32 * math.cos(math.radians(30))) * (10 * 111.32)
        assert abs(geo - planar) / planar > 0.05

    def test_zero_area_invalid(self):
        ring = [[120.8, 30.5], [120.8, 30.5], [120.8, 30.6], [120.8, 30.5]]
        aoi = validate_aoi(_polygon(ring), source="map_polygon")
        assert aoi.valid is False
        assert any("面积" in w for w in aoi.warnings)

    def test_cross_antimeridian_warns(self):
        aoi = aoi_from_bbox(179.5, 0.0, -179.5, 10.0)
        assert any("180" in w or "经线" in w for w in aoi.warnings)


class TestSummaryInjection:
    def test_compact_summary_no_full_geojson(self):
        aoi = aoi_from_bbox(120.6, 30.2, 121.2, 30.9, label="杭州湾北岸")
        s = compact_summary(aoi)
        assert '"type": "Polygon"' not in s
        assert "coordinates" not in s

    def test_compact_summary_contains_essentials(self):
        aoi = aoi_from_bbox(120.6, 30.2, 121.2, 30.9, label="杭州湾北岸")
        s = compact_summary(aoi)
        assert "id=" in s and "source=map_rectangle" in s
        assert "bbox=" in s and "centroid=" in s and "area_km2=" in s
        assert "label=杭州湾北岸" in s

    def test_invalid_aoi_summary_marks_invalid(self):
        ring = [[float("nan"), 30.5], [121.0, 30.5], [121.0, 31.0], [120.8, 31.0], [float("nan"), 30.5]]
        aoi = validate_aoi(_polygon(ring), source="map_polygon")
        s = compact_summary(aoi)
        assert "invalid" in s


class TestAOISingleton:
    def test_new_selection_overrides_old(self):
        a1 = aoi_from_bbox(120.6, 30.2, 121.2, 30.9)
        a2 = aoi_from_bbox(121.0, 30.0, 121.5, 30.5)
        assert a1.aoi_id != a2.aoi_id

    def test_stable_id_roundtrip(self):
        aoi = aoi_from_bbox(120.6, 30.2, 121.2, 30.9)
        restored = AOIContext.from_dict(aoi.to_dict())
        assert restored.aoi_id == aoi.aoi_id
        assert restored.bbox == aoi.bbox
