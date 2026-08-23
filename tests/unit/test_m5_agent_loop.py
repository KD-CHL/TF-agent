# -*- coding: utf-8 -*-
"""M5 Agent 闭环：预检、确认门闩、计划与校验。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TF_AGENT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent"))
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

from agent_command_bridge import (  # noqa: E402
    apply_system_command,
    build_pending_task,
    init_ui_session_defaults,
    propose_m5_plan,
)
import m5_agent_loop  # noqa: E402


def _touch_shp(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 最小 sidecar，仅满足 os.path.isfile 检查（引擎几何测试不在此覆盖）
    path.write_bytes(b"")
    path.with_suffix(".shx").write_bytes(b"")
    path.with_suffix(".dbf").write_bytes(b"")
    path.with_suffix(".prj").write_text('GEOGCS["WGS 84"]', encoding="utf-8")


class TestM5Intent(unittest.TestCase):
    def test_intent_and_confirm(self):
        self.assertTrue(m5_agent_loop.is_m5_intent("对当前任务做变化检测"))
        self.assertTrue(m5_agent_loop.is_m5_intent("跑一下 M5"))
        self.assertFalse(m5_agent_loop.is_m5_intent("跳转到杭州湾"))
        self.assertTrue(m5_agent_loop.is_m5_confirm_utterance("确认"))
        self.assertTrue(m5_agent_loop.is_m5_confirm_utterance("开始执行"))
        self.assertFalse(m5_agent_loop.is_m5_confirm_utterance("确认一下概率改成多少比较好呢" * 3))


class TestM5Preflight(unittest.TestCase):
    def test_ready_when_current_and_baseline_exist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cur = root / "24zhejiang1" / "24zhejiang1_Final_p0.05_c2.shp"
            base = root / "20zhejiang1" / "20zhejiang1_Final_p0.05_c2.shp"
            _touch_shp(cur)
            _touch_shp(base)
            plan = m5_agent_loop.build_m5_preflight(
                final_root=str(root),
                current_task="24zhejiang1",
                prob=0.05,
                cnt=2,
            )
            self.assertTrue(plan["ready"])
            self.assertEqual(plan["baseline_task"], "20zhejiang1")
            self.assertEqual(len(plan["available_periods"]), 1)
            self.assertFalse(plan["blockers"])

    def test_blocked_without_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cur = root / "24zhejiang1" / "24zhejiang1_Final_p0.05_c2.shp"
            _touch_shp(cur)
            plan = m5_agent_loop.build_m5_preflight(
                final_root=str(root),
                current_task="24zhejiang1",
            )
            self.assertFalse(plan["ready"])
            self.assertTrue(any("基线" in b for b in plan["blockers"]))


class TestM5VerifyAndSummary(unittest.TestCase):
    def test_empty_report_file_fails_verification(self):
        with tempfile.TemporaryDirectory() as td:
            report_path = os.path.join(td, "empty-report.json")
            Path(report_path).touch()
            report = {
                "target_roi": "24zhejiang1",
                "baseline_task": "20zhejiang1",
                "current_shp": "current.shp",
                "baseline_shp": "baseline.shp",
                "alert_level": "GREEN",
                "report_path": report_path,
                "quantitative_metrics": {"area_evolution": {}},
            }

            verification = m5_agent_loop.verify_m5_outputs(report)

            self.assertFalse(verification["ok"])
            failed = {c["name"] for c in verification["checks"] if not c["passed"]}
            self.assertIn("report_json_on_disk", failed)

    def test_verify_and_summary(self):
        with tempfile.TemporaryDirectory() as td:
            report_path = os.path.join(td, "ADVANCED_ALERT_REPORT_24zhejiang1.json")
            loss = os.path.join(td, "24zhejiang1_loss_zones.shp")
            Path(loss).write_bytes(b"shp")
            report = {
                "target_roi": "24zhejiang1",
                "baseline_task": "20zhejiang1",
                "baseline_shp": "base.shp",
                "current_shp": "cur.shp",
                "alert_level": "YELLOW",
                "diagnostic_message": "面积扩张",
                "report_path": report_path,
                "quantitative_metrics": {
                    "area_evolution": {
                        "baseline_area_km2": 10.0,
                        "current_area_km2": 12.0,
                        "change_rate_percentage": 20.0,
                    },
                    "centroid_trajectory": {"drift_distance_meters": 100.0},
                },
                "spatial_outputs": {
                    "loss_shapefile_path": loss,
                    "siltation_shapefile_path": "None",
                },
            }
            Path(report_path).write_text(json.dumps(report), encoding="utf-8")
            ver = m5_agent_loop.verify_m5_outputs(report)
            self.assertTrue(ver["ok"])
            self.assertEqual(ver["map_candidate"], os.path.normpath(loss))
            text = m5_agent_loop.summarize_m5_report_for_chat(report, ver)
            self.assertIn("YELLOW", text)
            self.assertIn("20.0", text)
            self.assertIn("已验证", text)


class TestM5BridgeGate(unittest.TestCase):
    def _state(self, final_root: str, task: str = "24zhejiang1") -> dict:
        s: dict = {}
        init_ui_session_defaults(s)
        s["ui_final_root"] = final_root
        s["ui_selected_task"] = task
        s["ui_prob_th"] = 0.05
        s["ui_min_cnt"] = 2
        return s

    def test_propose_then_run_requires_confirm(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _touch_shp(root / "24zhejiang1" / "24zhejiang1_Final_p0.05_c2.shp")
            _touch_shp(root / "20zhejiang1" / "20zhejiang1_Final_p0.05_c2.shp")
            state = self._state(str(root))
            r = apply_system_command(
                state, {"pending_action": {"type": "propose_m5", "task": "24zhejiang1"}}
            )
            self.assertEqual(r.action_type, "propose_m5")
            self.assertTrue(state["_m5_pending_plan"]["ready"])
            self.assertFalse(state.get("is_running"))
            self.assertNotIn("pending_task", state)

            pt, _, errs = build_pending_task(
                state, {"type": "run_m5", "confirmed": False}
            )
            self.assertIsNone(pt)
            self.assertTrue(errs)

            pt2, _, errs2 = build_pending_task(
                state, {"type": "run_m5", "confirmed": True}
            )
            self.assertFalse(errs2)
            self.assertEqual(pt2["mode"], "m5")
            self.assertTrue(pt2["m5"]["current_shp"])
            self.assertTrue(pt2["m5"]["baseline_shp"])

    def test_confirm_m5_starts_pending(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _touch_shp(root / "24zhejiang1" / "24zhejiang1_Final_p0.05_c2.shp")
            _touch_shp(root / "20zhejiang1" / "20zhejiang1_Final_p0.05_c2.shp")
            state = self._state(str(root))
            propose_m5_plan(state, {"task": "24zhejiang1"})
            r = apply_system_command(
                state, {"pending_action": {"type": "confirm_m5"}}
            )
            self.assertEqual(r.action_type, "run_m5")
            self.assertTrue(state.get("is_running"))
            self.assertEqual(state["pending_task"]["mode"], "m5")


if __name__ == "__main__":
    unittest.main()
