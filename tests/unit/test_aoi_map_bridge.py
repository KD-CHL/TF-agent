# -*- coding: utf-8 -*-
"""Phase D: AOI→地图图层回声桥 — 稳定 aoi_id、清除≠删业务层、摘要白名单、选定≠确认。"""
from __future__ import annotations

import os
import sys

import pytest

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

from aoi_context import aoi_from_bbox, compact_summary, validate_aoi  # noqa: E402
from aoi_map_bridge import (  # noqa: E402
    build_echo_messages,
    build_clear_messages,
    process_aoi_selected,
    process_aoi_cleared,
    aoi_recommendation_text,
)


def _polygon(coords):
    return {"type": "Polygon", "coordinates": [coords]}


class TestLayerEcho:
    def test_stable_layer_id(self):
        msgs = build_echo_messages(aoi_from_bbox(120.6, 30.2, 121.2, 30.9))
        add = [m for m in msgs if m["type"] == "CSTF_LAYER_ADD"]
        assert len(add) == 1
        assert add[0]["layer_id"].startswith("aoi:")
        assert add[0]["kind"] == "geojson"
        assert add[0]["data"]["type"] == "Polygon"

    def test_reselect_same_area_remove_then_add(self):
        aoi = aoi_from_bbox(120.6, 30.2, 121.2, 30.9)
        msgs = build_echo_messages(aoi, previous_aoi_id=aoi.aoi_id)
        types = [m["type"] for m in msgs]
        assert types == ["CSTF_LAYER_REMOVE", "CSTF_LAYER_ADD"]
        assert msgs[0]["layer_id"] == f"aoi:{aoi.aoi_id}"
        assert msgs[1]["layer_id"] == f"aoi:{aoi.aoi_id}"

    def test_clear_does_not_touch_business_layers(self):
        msgs = build_clear_messages("aoi_123", business_layer_ids=["shp_result", "tif_overlay"])
        assert len(msgs) == 1
        assert msgs[0]["type"] == "CSTF_LAYER_REMOVE"
        assert msgs[0]["layer_id"] == "aoi:aoi_123"
        # 业务层绝不在清除消息中
        assert all("shp_result" not in m.get("layer_id", "") for m in msgs)
        assert all("tif_overlay" not in m.get("layer_id", "") for m in msgs)

    def test_invalid_aoi_no_echo(self):
        ring = [[float("nan"), 30.5], [121.0, 30.5], [121.0, 31.0], [120.8, 31.0], [float("nan"), 30.5]]
        aoi = validate_aoi(_polygon(ring), source="map_polygon")
        msgs = build_echo_messages(aoi)
        assert msgs == []


class TestProcessMessages:
    def test_process_aoi_selected_returns_ack(self):
        state = {}
        result = process_aoi_selected(
            state,
            geometry=_polygon([[120.6, 30.2], [121.2, 30.2], [121.2, 30.9], [120.6, 30.9], [120.6, 30.2]]),
            source="map_rectangle",
        )
        assert result["ok"] is True
        assert state["_active_aoi"] is not None
        assert isinstance(result["echo"], list)
        assert any(m["type"] == "CSTF_LAYER_ADD" for m in result["echo"])

    def test_process_aoi_cleared(self):
        state = {"_active_aoi": aoi_from_bbox(120.6, 30.2, 121.2, 30.9)}
        result = process_aoi_cleared(state)
        assert result["ok"] is True
        assert state.get("_active_aoi") is None
        assert result["echo"]["type"] == "CSTF_LAYER_REMOVE"


class TestSelectionNotConfirmation:
    def test_aoi_select_does_not_confirm_pipeline(self):
        """选定 AOI 不改变任何确认门闩：pending_action 确认状态保持。"""
        state = {"_m5_plan_confirmed": False, "_e1_plan_confirmed": False}
        result = process_aoi_selected(
            state,
            geometry=_polygon([[120.6, 30.2], [121.2, 30.2], [121.2, 30.9], [120.6, 30.9], [120.6, 30.2]]),
            source="map_rectangle",
        )
        assert result["ok"] is True
        # 确认门闩完全不受影响
        assert state["_m5_plan_confirmed"] is False
        assert state["_e1_plan_confirmed"] is False
        assert "confirmed" not in result or result.get("confirmed") is None

    def test_aoi_context_independent_of_pending_task(self):
        state = {"pending_task": None}
        process_aoi_selected(
            state,
            geometry=_polygon([[120.6, 30.2], [121.2, 30.2], [121.2, 30.9], [120.6, 30.9], [120.6, 30.2]]),
            source="map_rectangle",
        )
        assert state["pending_task"] is None  # 不自动触发任何任务


class TestSummaryWhitelist:
    def test_injection_no_full_geojson(self):
        aoi = aoi_from_bbox(120.6, 30.2, 121.2, 30.9, label="杭州湾")
        text = aoi_recommendation_text(aoi, capabilities=None)
        assert "coordinates" not in text
        assert '"type"' not in text

    def test_injection_no_sensitive_paths(self):
        aoi = aoi_from_bbox(120.6, 30.2, 121.2, 30.9)
        text = aoi_recommendation_text(aoi, capabilities=None)
        assert "Z:" not in text and "C:" not in text and "/home/" not in text
        assert "model.pth" not in text


class TestCapabilityGate:
    def test_recommendation_obeys_blocked_capability(self):
        aoi = aoi_from_bbox(120.6, 30.2, 121.2, 30.9)
        caps = {"gee_download": "BLOCKED", "deep_learning_inference": "AVAILABLE"}
        text = aoi_recommendation_text(aoi, capabilities=caps)
        assert "gee" in text.lower() or "下载" in text
        assert "不建议" in text or "BLOCKED" in text or "受限" in text

    def test_recommendation_suggests_when_available(self):
        aoi = aoi_from_bbox(120.6, 30.2, 121.2, 30.9)
        caps = {"gee_download": "AVAILABLE", "deep_learning_inference": "AVAILABLE"}
        text = aoi_recommendation_text(aoi, capabilities=caps)
        assert "推理" in text or "下载" in text
