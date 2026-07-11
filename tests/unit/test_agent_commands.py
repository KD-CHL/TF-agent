# -*- coding: utf-8 -*-
"""Agent JSON 指令桥接回归测试（无需 Streamlit 运行时）。"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest

_TF_AGENT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent"))
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

from agent_command_bridge import (  # noqa: E402
    apply_agent_reply_immediate,
    apply_system_command,
    build_pending_task,
    flush_pending_agent_commands,
    init_ui_session_defaults,
    parse_system_command,
    process_agent_reply,
)


def _base_state() -> dict:
    s: dict = {}
    init_ui_session_defaults(s)
    s["ui_prob_th"] = 0.12
    s["ui_min_cnt"] = 4
    s["ui_m5_enabled"] = False
    s["ui_selected_task"] = "24zhejiang1"
    s["map_center"] = [30.0, 120.0]
    s["map_zoom"] = 8
    return s


class TestParseSystemCommand(unittest.TestCase):
    def test_json_block(self):
        raw = '好的。\n[SYSTEM_COMMAND_JSON]\n{"map":{"lat":30.2,"lon":121.5,"zoom":11}}\n[/SYSTEM_COMMAND_JSON]'
        cmd = parse_system_command(raw)
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd["map"]["lat"], 30.2)

    def test_legacy_pipeline(self):
        raw = "COMMAND_RUN_PIPELINE|24zhejiang1|0.05|2"
        cmd = parse_system_command(raw)
        self.assertEqual(cmd["sidebar_states"]["selected_task"], "24zhejiang1")
        self.assertEqual(cmd["pending_action"]["type"], "run_pipeline")

    def test_irrelevant_text_returns_none(self):
        self.assertIsNone(parse_system_command("今天天气不错"))


class TestDeltaMerge(unittest.TestCase):
    def test_unmentioned_params_preserved(self):
        state = _base_state()
        before = copy.deepcopy(state)
        reply = (
            "[SYSTEM_COMMAND_JSON]\n"
            + json.dumps({"sidebar_states": {"prob_th": 0.05}}, ensure_ascii=False)
            + "\n[/SYSTEM_COMMAND_JSON]"
        )
        result, _ = process_agent_reply(state, reply)
        self.assertTrue(result.applied)
        self.assertTrue(result.queued)
        flush_pending_agent_commands(state)
        self.assertEqual(state["ui_prob_th"], 0.05)
        self.assertEqual(state["ui_min_cnt"], before["ui_min_cnt"])
        self.assertEqual(state["ui_m5_enabled"], before["ui_m5_enabled"])

    def test_null_fields_skipped(self):
        state = _base_state()
        cmd = {"sidebar_states": {"prob_th": None, "min_cnt": 3}}
        apply_system_command(state, cmd)
        self.assertEqual(state["ui_prob_th"], 0.12)
        self.assertEqual(state["ui_min_cnt"], 3)

    def test_map_and_sidebar_concurrent(self):
        state = _base_state()
        cmd = {
            "map": {"lat": 31.0, "lon": 122.0, "zoom": 10},
            "sidebar_states": {"m5_enabled": True, "run_mode": "index"},
        }
        result = apply_system_command(state, cmd)
        self.assertTrue(result.map_updated)
        self.assertEqual(state["map_center"], [31.0, 122.0])
        self.assertEqual(state["ui_inference_mode"], "指数法")
        self.assertTrue(state["ui_m5_enabled"])

    def test_workflow_tab_alias(self):
        state = _base_state()
        apply_system_command(state, {"sidebar_states": {"workflow_tab": "gee数据下载"}})
        self.assertEqual(state["ui_workflow"], "GEE 数据下载")


class TestPendingActions(unittest.TestCase):
    def test_run_pipeline_schema(self):
        state = _base_state()
        cmd = {
            "sidebar_states": {"run_mode": "dl", "prob_th": 0.05, "min_cnt": 2},
            "pending_action": {"type": "run_pipeline", "task": "24zhejiang1"},
        }
        result = apply_system_command(state, cmd)
        self.assertEqual(result.action_type, "run_pipeline")
        pt = state["pending_task"]
        self.assertEqual(pt["task"], "24zhejiang1")
        self.assertEqual(pt["prob"], 0.05)
        self.assertEqual(pt["cnt"], 2)
        self.assertEqual(pt["mode"], "dl")
        self.assertIn("force_rerun", pt)

    def test_run_m4_schema(self):
        state = _base_state()
        cmd = {
            "sidebar_states": {"workflow_tab": "GEE 数据下载", "m4_cloud": 20},
            "pending_action": {
                "type": "run_m4",
                "task": "zhejiang1",
                "m4_params": {"cloud_limit": 20, "start_date": "2020-06-01"},
            },
        }
        result = apply_system_command(state, cmd)
        self.assertFalse(result.errors, result.errors)
        pt = state["pending_task"]
        self.assertEqual(pt["mode"], "m4")
        self.assertEqual(pt["m4"]["cloud_limit"], 20)
        self.assertEqual(pt["m4"]["start_date"], "2020-06-01")

    def test_run_autotune_schema(self):
        state = _base_state()
        cmd = {
            "pending_action": {
                "type": "run_autotune",
                "task": "24zhejiang1",
                "autotune_params": {"reference_id": "师姐_2020", "objective": "max_iou"},
            },
        }
        result = apply_system_command(state, cmd)
        self.assertFalse(result.errors, result.errors)
        at = state["pending_autotune"]
        self.assertEqual(at["reference_id"], "师姐_2020")
        self.assertEqual(at["objective"], "iou")

    def test_missing_task_error(self):
        state = _base_state()
        state.pop("ui_selected_task", None)
        pt, _, errs = build_pending_task(state, {"type": "run_pipeline"})
        self.assertIsNone(pt)
        self.assertTrue(errs)


class TestCoercion(unittest.TestCase):
    def test_prob_clamped(self):
        state = _base_state()
        apply_system_command(state, {"sidebar_states": {"prob_th": 0.99}})
        self.assertEqual(state["ui_prob_th"], 0.50)

    def test_bool_chinese(self):
        state = _base_state()
        apply_system_command(state, {"sidebar_states": {"e1_enabled": "打开"}})
        self.assertTrue(state["ui_e1_enabled"])


class TestScenarioExamples(unittest.TestCase):
    """用户口语场景 A–D 的标准 JSON 映射验证。"""

    def test_scenario_a_dl_pipeline(self):
        state = _base_state()
        payload = {
            "sidebar_states": {
                "selected_task": "24zhejiang1",
                "run_mode": "dl",
                "prob_th": 0.05,
                "min_cnt": 2,
            },
            "pending_action": {"type": "run_pipeline", "task": "24zhejiang1"},
        }
        reply = f"[SYSTEM_COMMAND_JSON]\n{json.dumps(payload, ensure_ascii=False)}\n[/SYSTEM_COMMAND_JSON]"
        result, clean = process_agent_reply(state, reply)
        self.assertTrue(result.applied)
        self.assertTrue(result.queued)
        flush_pending_agent_commands(state)
        self.assertIn("24zhejiang", clean or "ok")
        self.assertEqual(state["pending_task"]["mode"], "dl")


class TestStreamlitDeferQueue(unittest.TestCase):
    """模拟 Streamlit：聊天区只入队，侧栏渲染前 flush。"""

    def test_root_dir_and_m4_queued_then_flushed(self):
        state = _base_state()
        payload = {
            "sidebar_states": {
                "root_dir": r"H:\我的云端硬盘",
                "workflow_tab": "GEE数据下载",
                "m4_roi_name": "hangzhou_bay",
                "m4_start_date": "2020-01-01",
                "m4_end_date": "2020-01-31",
            },
            "pending_action": {"type": "run_m4", "task": "hangzhou_bay"},
        }
        reply = f"[SYSTEM_COMMAND_JSON]\n{json.dumps(payload, ensure_ascii=False)}\n[/SYSTEM_COMMAND_JSON]"
        result, _ = process_agent_reply(state, reply)
        self.assertTrue(result.queued)
        self.assertNotIn("pending_task", state)
        flushed = flush_pending_agent_commands(state)
        self.assertTrue(flushed.applied)
        self.assertEqual(state["ui_root_dir"], r"H:\我的云端硬盘")
        self.assertEqual(state["ui_workflow"], "GEE 数据下载")
        self.assertEqual(state["pending_task"]["mode"], "m4")

    def test_immediate_apply_for_unit_tests(self):
        state = _base_state()
        reply = (
            "[SYSTEM_COMMAND_JSON]\n"
            + json.dumps({"sidebar_states": {"root_dir": "E:/Data/test"}}, ensure_ascii=False)
            + "\n[/SYSTEM_COMMAND_JSON]"
        )
        apply_agent_reply_immediate(state, reply)
        self.assertEqual(state["ui_root_dir"], "E:\\Data\\test")

    def test_scenario_b_e1(self):
        state = _base_state()
        apply_system_command(
            state,
            {"sidebar_states": {"e1_enabled": True, "e1_reference": "师姐_2020"}},
        )
        self.assertTrue(state["ui_e1_enabled"])
        self.assertEqual(state["ui_e1_reference"], "师姐_2020")

    def test_scenario_c_m5_index(self):
        state = _base_state()
        apply_system_command(
            state,
            {
                "sidebar_states": {"m5_enabled": True, "run_mode": "index"},
                "pending_action": {"type": "run_pipeline", "task": "24zhejiang1"},
            },
        )
        self.assertTrue(state["ui_m5_enabled"])
        self.assertEqual(state["pending_task"]["mode"], "index")

    def test_scenario_d_m4_download(self):
        state = _base_state()
        apply_system_command(
            state,
            {
                "sidebar_states": {"workflow_tab": "GEE数据下载", "m4_cloud": 20},
                "pending_action": {"type": "run_m4", "task": "zhejiang1"},
            },
        )
        self.assertEqual(state["ui_workflow"], "GEE 数据下载")
        self.assertEqual(state["ui_m4_cloud_limit"], 20)
        self.assertEqual(state["pending_task"]["mode"], "m4")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    outcome = runner.run(suite)
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
