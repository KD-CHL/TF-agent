# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

from execution_request import (  # noqa: E402
    EXECUTION_ENTRYPOINTS,
    ExecutionRequest,
    attach_execution_request,
    from_pending_task,
)
from agent_command_bridge import build_pending_task, init_ui_session_defaults  # noqa: E402


class TestExecutionRequestContract(unittest.TestCase):
    def test_mode_mapping_has_single_production_entrypoint(self):
        self.assertEqual(EXECUTION_ENTRYPOINTS["dl"], "inference_agent_loop")
        self.assertEqual(EXECUTION_ENTRYPOINTS["index"], "index_agent_loop")
        self.assertEqual(EXECUTION_ENTRYPOINTS["workflow"], "workflow_orchestrator")
        self.assertNotEqual(EXECUTION_ENTRYPOINTS["dl"], "run_pipeline_sync")

    def test_agent_and_ui_snapshots_differ_only_by_confirmation_source(self):
        agent_pending = {
            "task": "p1",
            "mode": "dl",
            "prob": 0.05,
            "cnt": 2,
            "points_shp": None,
            "force_rerun": False,
        }
        ui = attach_execution_request(agent_pending, confirmation_source="ui")
        agent = attach_execution_request(agent_pending, confirmation_source="agent")
        self.assertEqual(ui["execution_request"]["task"], agent["execution_request"]["task"])
        self.assertEqual(ui["execution_request"]["mode"], agent["execution_request"]["mode"])
        self.assertEqual(ui["execution_request"]["entrypoint"], "inference_agent_loop")
        self.assertEqual(ui["execution_request"]["params"], agent["execution_request"]["params"])
        self.assertEqual(ui["execution_request"]["confirmation_source"], "ui")

    def test_attaching_contract_again_preserves_request_identity_for_reruns(self):
        pending = {
            "task": "p1",
            "mode": "dl",
            "plan_id": "plan-rerun-safe",
            "prob": 0.05,
            "cnt": 2,
        }
        first = attach_execution_request(pending, confirmation_source="ui")
        second = attach_execution_request(first, confirmation_source="ui")

        self.assertEqual(
            first["execution_request"]["request_id"],
            second["execution_request"]["request_id"],
        )

    def test_changed_parameters_get_a_new_request_identity(self):
        first = attach_execution_request(
            {"task": "p1", "mode": "dl", "plan_id": "plan-params", "prob": 0.05},
            confirmation_source="ui",
        )
        changed = dict(first)
        changed["prob"] = 0.10
        second = attach_execution_request(changed, confirmation_source="ui")

        self.assertNotEqual(
            first["execution_request"]["request_id"],
            second["execution_request"]["request_id"],
        )

    def test_legacy_sync_mode_is_explicitly_named(self):
        source = (Path(__file__).parents[2] / "TF-agent" / "app.py").read_text(encoding="utf-8")
        self.assertIn('elif mode == "legacy_dl":', source)
        self.assertIn("未启动旧兼容入口", source)

    def test_legacy_m4_worker_uses_shared_gee_adapter(self):
        source = (Path(__file__).parents[2] / "TF-agent" / "app.py").read_text(encoding="utf-8")
        start = source.index("def _pipeline_worker_entry")
        end = source.index("def _workflow_worker_entry", start)
        worker = source[start:end]
        self.assertIn("build_legacy_m4_plan", worker)
        self.assertIn("_gee_worker_entry", worker)
        self.assertNotIn("ok = run_m4_download_sync(ctx, shared, stop_event)", worker)

    def test_independent_postflight_workers_gate_success_on_registration(self):
        source = (Path(__file__).parents[2] / "TF-agent" / "app.py").read_text(encoding="utf-8")
        for worker_name, register_name in (
            ("run_m5_sync", "register_m5_asset"),
            ("run_e1_sync", "register_e1_asset"),
        ):
            start = source.index(f"def {worker_name}")
            end = source.find("\ndef ", start + 5)
            worker = source[start:end if end >= 0 else len(source)]
            self.assertIn(f"asset_id = {register_name}", worker)
            self.assertIn("if not asset_id", worker)

    def test_independent_asset_registerers_reject_empty_reports(self):
        source = (Path(__file__).parents[2] / "TF-agent" / "app.py").read_text(encoding="utf-8")
        for function_name in ("register_m5_asset", "register_e1_asset"):
            start = source.index(f"def {function_name}")
            end = source.find("\ndef ", start + 5)
            function = source[start:end if end >= 0 else len(source)]
            self.assertIn("_nonempty_file", function)

    def test_legacy_optional_postflight_phases_verify_before_success_timeline(self):
        """兼容主流程的可选 M5/E1 也不能把未校验报告写成成功。"""
        source = (Path(__file__).parents[2] / "TF-agent" / "app.py").read_text(encoding="utf-8")
        m5_start = source.index("def _run_m5_phase")
        m5_end = source.index("def run_m5_sync", m5_start)
        m5_phase = source[m5_start:m5_end]
        e1_start = source.index("def _run_e1_phase")
        e1_end = source.index("def run_pipeline_sync", e1_start)
        e1_phase = source[e1_start:e1_end]
        self.assertIn("verify_m5_outputs", m5_phase)
        self.assertIn("register_m5_asset", m5_phase)
        self.assertIn("verify_e1_outputs", e1_phase)
        self.assertIn("register_e1_asset", e1_phase)

        generic_start = source.index("if success and not _inference_handled")
        generic_end = source.index("_job_status =", generic_start)
        generic_finalize = source[generic_start:generic_end]
        self.assertIn("m5_verification", generic_finalize)
        self.assertIn("e1_verification", generic_finalize)
        self.assertIn('"变化分析校验通过" if _m5_ok', generic_finalize)
        self.assertIn('"精度评价校验通过" if _e1_ok', generic_finalize)
        self.assertIn("_optional_postflight_warning", generic_finalize)

    def test_compatibility_framework_has_no_production_importers(self):
        """历史框架可保留给旧数据读取，但不能成为新的生产入口。"""
        app_dir = Path(__file__).parents[2] / "TF-agent"
        importers = []
        for path in app_dir.glob("*.py"):
            if path.name == "agent_task_framework.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "agent_task_framework" in text:
                importers.append(path.name)
        self.assertEqual(importers, [])

    def test_bridge_pending_task_gets_contract(self):
        state = {}
        init_ui_session_defaults(state)
        state["ui_selected_task"] = "p1"
        state["ui_run_mode"] = "dl"
        pending, autotune, errors = build_pending_task(
            state, {"type": "run_pipeline", "task": "p1", "confirmed": True}
        )
        self.assertIsNone(autotune)
        self.assertFalse(errors)
        # build_pending_task 保持兼容 schema；apply_system_command 负责附加契约。
        self.assertEqual(pending["mode"], "dl")
        request = from_pending_task(pending, confirmation_source="agent")
        self.assertEqual(request["entrypoint"], "inference_agent_loop")

    def test_autotune_pending_task_gets_contract(self):
        state = {}
        init_ui_session_defaults(state)
        state["ui_selected_task"] = "p1"
        pending, autotune, errors = build_pending_task(
            state,
            {
                "type": "run_autotune",
                "task": "p1",
                "confirmed": True,
                "autotune_params": {"reference_id": "ref-2020", "objective": "iou"},
            },
        )
        self.assertIsNone(pending)
        self.assertFalse(errors)
        self.assertEqual(autotune["mode"], "autotune")
        contract = attach_execution_request(autotune, confirmation_source="agent")
        self.assertEqual(contract["execution_request"]["entrypoint"], "autotune")

    def test_invalid_task_or_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            ExecutionRequest(task="", mode="dl")
        with self.assertRaises(ValueError):
            ExecutionRequest(task="p1", mode="unknown")


if __name__ == "__main__":
    unittest.main()
