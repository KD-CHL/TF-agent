# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

import e1_agent_loop  # noqa: E402
import m5_agent_loop  # noqa: E402


def test_m5_summary_does_not_claim_verified_when_output_check_fails():
    report = {
        "target_roi": "24zhejiang",
        "alert_level": "GREEN",
        "current_shp": "current.shp",
        "baseline_shp": "baseline.shp",
        "quantitative_metrics": {"area_evolution": {}, "centroid_trajectory": {}},
    }
    text = m5_agent_loop.summarize_m5_report_for_chat(
        report, {"ok": False, "checks": [{"name": "report_json_on_disk", "passed": False}]}
    )
    assert "已验证" not in text.splitlines()[0]
    assert "未完全通过" in text


def test_e1_summary_does_not_claim_verified_when_output_check_fails():
    report = {
        "roi_name": "24zhejiang",
        "reference": "师姐_2020",
        "comparisons": {"source_a": {"jaccard_iou": 0.2, "intersection_km2": 1.0}},
    }
    text = e1_agent_loop.summarize_e1_report_for_chat(
        report, {"ok": False, "checks": [{"name": "report_json_on_disk", "passed": False}]}
    )
    assert "已验证" not in text.splitlines()[0]
    assert "未完全通过" in text
