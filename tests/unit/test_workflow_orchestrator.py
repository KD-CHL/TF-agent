# -*- coding: utf-8 -*-
"""潮滩分析 Workflow 编排器：DAG 构建、父确认、条件执行、级联阻塞、账本、血缘。

短测：全部使用 override 执行器（不触碰 GEE/推理/E1/M5/PDF 真实引擎）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

import workflow_orchestrator as wo  # noqa: E402


# ---------------------------------------------------------------
#  工具：override 执行器（统一结果 dict）
# ---------------------------------------------------------------
def _ok_executor(step, workflow, exec_ctx):
    """成功执行器：按 step 生成 asset_id。"""
    return {
        "success": True,
        "status": wo.STEP_SUCCEEDED,
        "outputs": {},
        "assets": [{"asset_id": f"asset_{step['step_id']}", "asset_type": "x"}],
        "metrics": {},
        "warnings": [],
        "error": None,
        "plan_id": f"plan_{step['step_id']}",
    }


def _fail_executor(step, workflow, exec_ctx):
    return {
        "success": False,
        "status": wo.STEP_FAILED,
        "outputs": {},
        "assets": [],
        "metrics": {},
        "warnings": [],
        "error": f"模拟失败 {step['step_id']}",
    }


def _counting_executor(counter):
    def _fn(step, workflow, exec_ctx):
        counter[step["step_id"]] = counter.get(step["step_id"], 0) + 1
        return _ok_executor(step, workflow, exec_ctx)

    return _fn


def _all_ok_ctx():
    return {
        "gee_executor": _ok_executor,
        "inference_executor": _ok_executor,
        "e1_executor": _ok_executor,
        "m5_executor": _ok_executor,
        "report_executor": _ok_executor,
    }


def _mini_aoi(aoi_id="aoi_1", label="泉州湾"):
    return {"aoi_id": aoi_id, "label": label, "valid": True, "geometry": {}}


class WorkflowOrchestratorTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._data_dir = os.path.join(self._tmp.name, "data")
        os.makedirs(self._data_dir, exist_ok=True)
        # 把账本/血缘写盘重定向到临时目录，避免污染真实 data/
        self._old_ledger = wo.WORKFLOW_LEDGER_PATH
        self._old_data_dir = wo._DEFAULT_DATA_DIR
        wo.WORKFLOW_LEDGER_PATH = os.path.join(self._data_dir, "workflow_ledger.json")
        wo._DEFAULT_DATA_DIR = self._data_dir

    def tearDown(self):
        wo.WORKFLOW_LEDGER_PATH = self._old_ledger
        wo._DEFAULT_DATA_DIR = self._old_data_dir
        self._tmp.cleanup()

    def _build(self, **kw):
        defaults = dict(
            aoi=_mini_aoi(),
            target_year=2024,
            baseline_year=2022,
            region="quanzhou",
        )
        defaults.update(kw)
        return wo.build_analysis_workflow(**defaults)

    def _run(self, wf, ctx=None):
        if not wf.get("confirmed"):
            state = {wo.STATE_WORKFLOW_PENDING_PLAN: wf}
            ok, err = wo.confirm_workflow(state, wf["workflow_id"])
            self.assertTrue(ok, err)
        return wo.run_analysis_workflow(wf, exec_ctx=ctx or _all_ok_ctx())

    def _confirm(self, wf):
        """把 workflow 作为待确认计划存入 state 并确认（与 bridge 一致）。"""
        state = {wo.STATE_WORKFLOW_PENDING_PLAN: wf}
        ok, err = wo.confirm_workflow(state, wf["workflow_id"])
        self.assertTrue(ok, err)
        return state

    def test_corrupt_asset_registry_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "assets_registry.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"bad": [')
            with self.assertRaises(ValueError):
                wo.load_assets_registry(path)
            self.assertTrue(os.path.exists(path))
            self.assertTrue(list(__import__("pathlib").Path(td).glob("assets_registry.json.corrupt-*")))

    def test_invalid_asset_record_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "assets_registry.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"bad": {"file_path": []}}, handle)
            with self.assertRaises(ValueError):
                wo.load_assets_registry(path)
            self.assertTrue(list(__import__("pathlib").Path(td).glob("assets_registry.json.corrupt-*")))


def test_workflow_result_timeline_status_preserves_warnings():
    assert wo.workflow_result_timeline_status(wo.WF_SUCCEEDED) == "SUCCEEDED"
    assert wo.workflow_result_timeline_status(wo.WF_COMPLETED_WITH_WARNINGS) == "WARNING"
    assert wo.workflow_result_timeline_status(wo.WF_FAILED) == "FAILED"


def test_pdf_step_rejects_report_when_asset_registration_fails(monkeypatch, tmp_path):
    """A generated PDF is not a successful workflow result until registered."""
    import asset_report_engine as are

    report_path = tmp_path / "report.pdf"
    report_path.write_bytes(b"pdf")
    result = SimpleNamespace(
        success=True,
        report_path=str(report_path),
        sections=["基本信息"],
        warnings=[],
        error="",
    )
    monkeypatch.setattr(are, "generate_asset_report", lambda **_kwargs: result)
    monkeypatch.setattr(wo, "_register_report_asset", lambda *_args, **_kwargs: None)
    step = {"step_id": "pdf_report", "tool": wo.TOOL_PDF_REPORT}
    workflow = {"task_id": "24zhejiang", "context": {}}

    outcome = wo._run_report_step(
        step,
        workflow,
        exec_ctx={},
        push_log=lambda _message: None,
        stop_event=None,
    )

    assert outcome["success"] is False
    assert outcome["status"] == wo.STEP_FAILED
    assert "登记失败" in outcome["error"]


def test_report_asset_registration_rejects_empty_file(tmp_path):
    """空报告不能进入成果注册表，即使路径本身存在。"""
    report_path = tmp_path / "empty-report.pdf"
    report_path.touch()
    registry_path = tmp_path / "assets_registry.json"

    asset_id = wo._register_report_asset(
        "24zhejiang", str(report_path), exec_ctx={"registry_path": str(registry_path)}
    )

    assert asset_id is None
    assert not registry_path.exists()


def test_e1_asset_registration_rejects_empty_report(tmp_path):
    report_path = tmp_path / "empty-e1.json"
    report_path.touch()
    registry_path = tmp_path / "assets_registry.json"

    asset_id = wo._register_e1_workflow_asset(
        "24zhejiang", {"report_path": str(report_path)},
        exec_ctx={"registry_path": str(registry_path)},
    )

    assert asset_id is None
    assert not registry_path.exists()


def test_m5_asset_registration_rejects_empty_report(tmp_path):
    report_path = tmp_path / "empty-m5.json"
    report_path.touch()
    registry_path = tmp_path / "assets_registry.json"

    asset_id = wo._register_m5_workflow_asset(
        "24zhejiang", {"report_path": str(report_path)},
        exec_ctx={"registry_path": str(registry_path)},
    )

    assert asset_id is None
    assert not registry_path.exists()


def test_prediction_asset_selection_rejects_empty_file(tmp_path):
    empty = tmp_path / "task_Final.tif"
    empty.touch()

    selected = wo._find_prediction_asset(
        {"task_id": "24zhejiang"},
        {"asset": {"task": "24zhejiang", "file_path": str(empty)}},
    )

    assert selected is None


def test_engine_adapter_fails_when_registration_returns_none():
    step = {"step_id": "e1_quality", "tool": wo.TOOL_E1_QUALITY}
    workflow = {"workflow_id": "wf-registration-gate"}

    outcome = wo._run_engine_adapter(
        step,
        workflow,
        {"plan_id": "plan-1"},
        run_fn=lambda *_args: {"success": True, "report": {"report_path": "x"}},
        verify_fn=lambda _raw: {"ok": True, "checks": []},
        register_fn=lambda *_args: None,
        asset_type=wo.ASSET_E1,
        exec_ctx={},
        push_log=lambda _message: None,
        stop_event=None,
    )

    assert outcome["success"] is False
    assert outcome["status"] == wo.STEP_FAILED
    assert "登记" in outcome["error"]
    assert step["status"] == wo.STEP_FAILED


def test_engine_adapter_fails_closed_when_registration_raises():
    step = {"step_id": "e1_quality", "tool": wo.TOOL_E1_QUALITY}
    workflow = {"workflow_id": "wf-registration-exception"}

    def register(_plan, _raw, _verification):
        raise ValueError("registry write failed /Users/private/report.json")

    outcome = wo._run_engine_adapter(
        step,
        workflow,
        {"plan_id": "plan-1"},
        run_fn=lambda *_args: {"success": True},
        verify_fn=lambda _raw: {"ok": True, "checks": []},
        register_fn=register,
        asset_type=wo.ASSET_E1,
        exec_ctx={},
        push_log=lambda _message: None,
        stop_event=None,
    )

    assert outcome["success"] is False
    assert outcome["status"] == wo.STEP_FAILED
    assert "登记" in outcome["error"]
    assert "/Users/" not in outcome["error"]


def test_child_adapter_fails_closed_when_registration_raises():
    step = {"step_id": "local_inference", "tool": wo.TOOL_LOCAL_INFERENCE}
    workflow = {"workflow_id": "wf-child-registration-exception"}

    def register(_plan, _raw, _verification):
        raise ValueError("registry write failed /Users/private/report.json")

    outcome = wo._run_with_child_confirmation(
        step,
        workflow,
        {"ready": True, "plan_id": "plan-1"},
        execute_fn=lambda *_args: {"success": True},
        verify_fn=lambda *_args: {"ok": True, "checks": []},
        register_fn=register,
        asset_type=wo.ASSET_PREDICTION,
        exec_ctx={},
        push_log=lambda _message: None,
        stop_event=None,
    )

    assert outcome["success"] is False
    assert outcome["status"] == wo.STEP_FAILED
    assert "登记" in outcome["error"]
    assert "/Users/" not in outcome["error"]


# ---------------------------------------------------------------
#  1. DAG 构建（确定性结构）
# ---------------------------------------------------------------
class TestBuildWorkflow(WorkflowOrchestratorTestBase):
    def test_build_standard_full_workflow(self):
        wf = self._build(user_intent={"need_e1": True, "need_m5": True})
        steps = wf["steps"]
        ids = [s["step_id"] for s in steps]
        self.assertEqual(
            ids, ["gee_download", "local_inference", "e1_quality", "m5_change", "pdf_report"]
        )
        self.assertEqual(wf["schema"], "analysis_workflow_plan_v1")
        self.assertTrue(wf["workflow_id"].startswith("wf_"))
        self.assertEqual(wf["context"]["target_year"], 2024)
        self.assertEqual(wf["context"]["baseline_year"], 2022)
        self.assertEqual(wf["task_id"], "24quanzhou")

    def test_build_step_dependencies(self):
        wf = self._build()
        by_id = {s["step_id"]: s for s in wf["steps"]}
        self.assertEqual(by_id["gee_download"]["depends_on"], [])
        self.assertEqual(by_id["local_inference"]["depends_on"], ["gee_download"])
        self.assertEqual(by_id["e1_quality"]["depends_on"], ["local_inference"])
        self.assertEqual(by_id["m5_change"]["depends_on"], ["local_inference"])
        self.assertEqual(
            by_id["pdf_report"]["depends_on"], ["local_inference"]
        )
        self.assertEqual(
            by_id["pdf_report"]["optional_depends_on"], ["e1_quality", "m5_change"]
        )

    def test_build_required_flags(self):
        wf = self._build(user_intent={"need_e1": True, "need_m5": True})
        by_id = {s["step_id"]: s for s in wf["steps"]}
        self.assertTrue(by_id["gee_download"]["required"])
        self.assertTrue(by_id["local_inference"]["required"])
        self.assertTrue(by_id["e1_quality"]["required"])  # 用户明确要求
        self.assertTrue(by_id["m5_change"]["required"])
        self.assertTrue(by_id["pdf_report"]["required"])

    def test_build_no_e1_reference_auto_skip_condition(self):
        # need_e1 未指定 → reference_available（有真值才做）
        wf = self._build()
        e1 = [s for s in wf["steps"] if s["step_id"] == "e1_quality"][0]
        self.assertEqual(e1["condition"], "reference_available")
        self.assertFalse(e1["required"])

    def test_build_no_m5_baseline_auto_condition(self):
        wf = self._build()
        m5 = [s for s in wf["steps"] if s["step_id"] == "m5_change"][0]
        self.assertEqual(m5["condition"], "baseline_available")
        self.assertFalse(m5["required"])

    def test_build_no_baseline_year(self):
        wf = self._build(baseline_year=None)
        m5 = [s for s in wf["steps"] if s["step_id"] == "m5_change"][0]
        self.assertEqual(m5["condition"], "no_baseline_year")
        self.assertFalse(m5["required"])
        # 报告不再依赖 m5
        pdf = [s for s in wf["steps"] if s["step_id"] == "pdf_report"][0]
        self.assertNotIn("m5_change", pdf["depends_on"])

    def test_build_user_explicit_skip_e1_m5(self):
        wf = self._build(user_intent={"need_e1": False, "need_m5": False})
        by_id = {s["step_id"]: s for s in wf["steps"]}
        self.assertEqual(by_id["e1_quality"]["condition"], "user_skipped")
        self.assertEqual(by_id["m5_change"]["condition"], "user_skipped")
        pdf = by_id["pdf_report"]
        self.assertEqual(pdf["depends_on"], ["local_inference"])

    def test_build_user_required_reference(self):
        wf = self._build(user_intent={"need_e1": True})
        e1 = [s for s in wf["steps"] if s["step_id"] == "e1_quality"][0]
        self.assertEqual(e1["condition"], "reference_required")
        self.assertTrue(e1["required"])

    def test_build_user_required_baseline(self):
        wf = self._build(user_intent={"need_m5": True})
        m5 = [s for s in wf["steps"] if s["step_id"] == "m5_change"][0]
        self.assertEqual(m5["condition"], "baseline_required")
        self.assertTrue(m5["required"])

    def test_build_default_goal(self):
        wf = self._build(region="hangzhou")
        self.assertIn("2024", wf["goal"])
        self.assertIn("2022", wf["goal"])
        self.assertIn("泉州湾", wf["goal"])

    def test_build_custom_workflow_id(self):
        wf = self._build(workflow_id="wf_test_123")
        self.assertEqual(wf["workflow_id"], "wf_test_123")

    def test_build_task_id_from_region(self):
        wf = self._build(target_year=2022, region="zhejiang")
        self.assertEqual(wf["task_id"], "22zhejiang")

    def test_validate_blockers_missing_aoi(self):
        wf = self._build(aoi={})
        ok, blockers, _ = wo.validate_analysis_workflow(wf)
        self.assertFalse(ok)
        self.assertTrue(any("AOI" in b for b in blockers))

    def test_validate_blockers_missing_root_dir(self):
        wf = self._build(root_dir=os.path.join(self._tmp.name, "nope"))
        ok, blockers, _ = wo.validate_analysis_workflow(wf)
        self.assertFalse(ok)
        self.assertTrue(any("影像根目录" in b for b in blockers))

    def test_validate_blockers_missing_required_workflow_inputs(self):
        wf = self._build(root_dir="", final_root="", mask_root="", model_path="")
        ok, blockers, _ = wo.validate_analysis_workflow(wf)
        self.assertFalse(ok)
        self.assertTrue(any("缺少影像根目录" in b for b in blockers))
        self.assertTrue(any("缺少成果根目录" in b for b in blockers))
        self.assertTrue(any("缺少掩膜根目录" in b for b in blockers))
        self.assertTrue(any("缺少模型权重" in b for b in blockers))

    def test_validate_blockers_do_not_echo_windows_paths(self):
        wf = self._build(
            root_dir=r"C:\Users\chl\input",
            final_root=r"C:\Users\chl\final",
            mask_root=r"\\server\share\mask",
            model_path=r"E:\models\cdnet.pth",
        )
        ok, blockers, _ = wo.validate_analysis_workflow(wf)
        self.assertFalse(ok)
        text = "\n".join(blockers)
        self.assertNotIn(r"C:\Users\chl", text)
        self.assertNotIn(r"\\server\share", text)
        self.assertIn("input", text)


# ---------------------------------------------------------------
#  2. 父级确认门闩
# ---------------------------------------------------------------
class TestConfirmWorkflow(WorkflowOrchestratorTestBase):
    def _state_with(self, wf):
        return {wo.STATE_WORKFLOW_PENDING_PLAN: wf}

    def test_confirm_sets_confirmed(self):
        wf = self._build()
        state = self._state_with(wf)
        ok, err = wo.confirm_workflow(state, wf["workflow_id"])
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertTrue(wf["confirmed"])
        self.assertEqual(wf["status"], wo.WF_CONFIRMED)
        self.assertEqual(wf["confirmation_source"], "parent_workflow")
        self.assertIn(wf["workflow_id"], state[wo.STATE_WORKFLOW_PLAN_CONFIRMED])
        self.assertIsNotNone(wf["approved_params"])

    def test_confirm_idempotent(self):
        wf = self._build()
        state = self._state_with(wf)
        ok1, _ = wo.confirm_workflow(state, wf["workflow_id"])
        ok2, _ = wo.confirm_workflow(state, wf["workflow_id"])
        self.assertTrue(ok1)
        self.assertTrue(ok2)  # 幂等，不报错
        confirmed = state[wo.STATE_WORKFLOW_PLAN_CONFIRMED]
        self.assertEqual(len(confirmed), 1)

    def test_confirm_wrong_id_rejected(self):
        wf = self._build()
        state = self._state_with(wf)
        ok, err = wo.confirm_workflow(state, "wf_other")
        self.assertFalse(ok)
        self.assertIn("不一致", err or "")

    def test_is_workflow_confirmed(self):
        wf = self._build()
        state = self._state_with(wf)
        self.assertFalse(wo.is_workflow_confirmed(state, wf["workflow_id"]))
        wo.confirm_workflow(state, wf["workflow_id"])
        self.assertTrue(wo.is_workflow_confirmed(state, wf["workflow_id"]))

    def test_cancel_workflow(self):
        wf = self._build()
        state = self._state_with(wf)
        wo.confirm_workflow(state, wf["workflow_id"])
        ok, err = wo.cancel_workflow(state)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(wf["status"], wo.WF_CANCELLED)
        self.assertFalse(wo.is_workflow_confirmed(state, wf["workflow_id"]))

    def test_params_changed_pauses(self):
        wf = self._build()
        state = self._state_with(wf)
        wo.confirm_workflow(state, wf["workflow_id"])
        # 确认后修改目标年份
        wf["context"]["target_year"] = 2025
        changes = wo.check_params_changed(wf)
        self.assertTrue(any("target_year" in c for c in changes))
        self.assertEqual(wf["status"], wo.WF_PAUSED)

    def test_confirm_without_pending_plan_fails(self):
        wf = self._build()
        ok, err = wo.confirm_workflow({}, wf["workflow_id"])
        self.assertFalse(ok)
        self.assertIn("没有待确认", err or "")


# ---------------------------------------------------------------
#  3. DAG 执行（override 执行器）
# ---------------------------------------------------------------
class TestRunWorkflow(WorkflowOrchestratorTestBase):
    def test_optional_failure_does_not_block_pdf(self):
        wf = self._build(user_intent={"need_e1": None, "need_m5": False})
        by_id = {s["step_id"]: s for s in wf["steps"]}
        # 让自动 E1 进入执行，保持其可选语义；M5 明确跳过。
        by_id["e1_quality"]["condition"] = None

        def _fail_optional_e1(step, workflow, exec_ctx):
            if step["step_id"] == "e1_quality":
                return _fail_executor(step, workflow, exec_ctx)
            return _ok_executor(step, workflow, exec_ctx)

        result = self._run(wf, {**_all_ok_ctx(), "e1_executor": _fail_optional_e1})
        self.assertEqual(result["status"], wo.WF_COMPLETED_WITH_WARNINGS)
        self.assertEqual(by_id["e1_quality"]["status"], wo.STEP_FAILED)
        self.assertEqual(by_id["m5_change"]["status"], wo.STEP_SKIPPED)
        self.assertEqual(by_id["pdf_report"]["status"], wo.STEP_SUCCEEDED)
        self.assertIn("模拟失败 e1_quality", "\n".join(result["warnings"]))
        self.assertIn("成果报告", result["summary"])

    def test_required_e1_failure_blocks_pdf_and_fails_workflow(self):
        wf = self._build(user_intent={"need_e1": True, "need_m5": False})

        def _fail_required_e1(step, workflow, exec_ctx):
            if step["step_id"] == "e1_quality":
                return _fail_executor(step, workflow, exec_ctx)
            return _ok_executor(step, workflow, exec_ctx)

        result = self._run(wf, {**_all_ok_ctx(), "e1_executor": _fail_required_e1})
        by_id = {s["step_id"]: s for s in wf["steps"]}
        self.assertEqual(result["status"], wo.WF_FAILED)
        self.assertEqual(by_id["e1_quality"]["status"], wo.STEP_FAILED)
        self.assertEqual(by_id["pdf_report"]["status"], wo.STEP_BLOCKED)
    def test_full_dag_asset_chaining(self):
        wf = self._build(user_intent={"need_e1": True, "need_m5": True})
        result = self._run(wf)
        self.assertEqual(result["status"], wo.WF_SUCCEEDED)
        self.assertEqual(result["assets"]["dataset"], "asset_gee_download")
        self.assertEqual(result["assets"]["prediction"], "asset_local_inference")
        self.assertEqual(result["assets"]["e1"], "asset_e1_quality")
        self.assertEqual(result["assets"]["m5"], "asset_m5_change")
        self.assertEqual(result["assets"]["report"], "asset_pdf_report")
        # 步骤全成功且 asset_id 同步
        for s in wf["steps"]:
            self.assertEqual(s["status"], wo.STEP_SUCCEEDED)
            self.assertEqual(s["asset_id"], f"asset_{s['step_id']}")

    def test_optional_steps_auto_skip(self):
        # 无真值、无基线 → e1/m5 自动跳过，主流程仍完成
        wf = self._build()  # need_e1/need_m5 均为 None
        result = self._run(wf)
        self.assertEqual(result["status"], wo.WF_COMPLETED_WITH_WARNINGS)
        steps = {s["step_id"]: s["status"] for s in wf["steps"]}
        self.assertEqual(steps["e1_quality"], wo.STEP_SKIPPED)
        self.assertEqual(steps["m5_change"], wo.STEP_SKIPPED)
        self.assertEqual(steps["pdf_report"], wo.STEP_SUCCEEDED)
        self.assertIn("dataset", result["assets"])
        self.assertIn("prediction", result["assets"])
        self.assertIn("report", result["assets"])

    def test_required_step_failed_cascade_blocked(self):
        wf = self._build(user_intent={"need_e1": True, "need_m5": True})
        ctx = _all_ok_ctx()
        ctx["gee_executor"] = _fail_executor
        result = self._run(wf, ctx)
        self.assertEqual(result["status"], wo.WF_FAILED)
        steps = {s["step_id"]: s["status"] for s in wf["steps"]}
        self.assertEqual(steps["gee_download"], wo.STEP_FAILED)
        # 全部下游级联 BLOCKED（含可选步骤也不得误判为 SKIPPED）
        for sid in ("local_inference", "e1_quality", "m5_change", "pdf_report"):
            self.assertEqual(steps[sid], wo.STEP_BLOCKED, sid)

    def test_optional_step_failed_keeps_warnings(self):
        # 语义函数：可选失败 → COMPLETED_WITH_WARNINGS（永不 FAILED）
        wf = self._build(user_intent={"need_e1": True, "need_m5": True})
        by_id = {s["step_id"]: s for s in wf["steps"]}
        by_id["gee_download"]["status"] = wo.STEP_SUCCEEDED
        by_id["local_inference"]["status"] = wo.STEP_SUCCEEDED
        by_id["e1_quality"]["status"] = wo.STEP_FAILED
        by_id["e1_quality"]["required"] = False  # 模拟可选
        by_id["m5_change"]["status"] = wo.STEP_SUCCEEDED
        by_id["pdf_report"]["status"] = wo.STEP_SUCCEEDED
        self.assertEqual(wo._evaluate_workflow_status(wf), wo.WF_COMPLETED_WITH_WARNINGS)

    def test_optional_blocked_step_keeps_warnings(self):
        """可选步骤被依赖阻断时也不能把 Workflow 伪报为全成功。"""
        wf = self._build(user_intent={"need_e1": False, "need_m5": False})
        by_id = {s["step_id"]: s for s in wf["steps"]}
        for step in wf["steps"]:
            step["status"] = wo.STEP_SUCCEEDED
        by_id["e1_quality"]["required"] = False
        by_id["e1_quality"]["status"] = wo.STEP_BLOCKED
        result = wo._finalize_result(wf, status=wo._evaluate_workflow_status(wf))

        assert result["status"] == wo.WF_COMPLETED_WITH_WARNINGS
        assert any("可选步骤" in warning and "精度评价" in warning
                   for warning in result["warnings"])

    def test_required_optional_mix_all_succeeded(self):
        wf = self._build()
        by_id = {s["step_id"]: s for s in wf["steps"]}
        for s in wf["steps"]:
            s["status"] = wo.STEP_SUCCEEDED
        self.assertEqual(wo._evaluate_workflow_status(wf), wo.WF_SUCCEEDED)

    def test_paused_workflow_not_executed(self):
        wf = self._build()
        state = {wo.STATE_WORKFLOW_PENDING_PLAN: wf}
        wo.confirm_workflow(state, wf["workflow_id"])
        wf["context"]["target_year"] = 2025  # 参数变化
        wf["status"] = wo.WF_PAUSED
        result = self._run(wf)
        self.assertEqual(result["status"], wo.WF_PAUSED)
        self.assertTrue(any("参数" in e for e in result["errors"]))

    def test_rerun_no_duplicate_start(self):
        wf = self._build(user_intent={"need_e1": True, "need_m5": True})
        counter = {}
        ctx = {
            "gee_executor": _counting_executor(counter),
            "inference_executor": _counting_executor(counter),
            "e1_executor": _counting_executor(counter),
            "m5_executor": _counting_executor(counter),
            "report_executor": _counting_executor(counter),
        }
        self._run(wf, ctx)
        first = dict(counter)
        self._run(wf, ctx)  # 第二次：全部终态，不再执行
        self.assertEqual(counter, first)
        self.assertTrue(all(v == 1 for v in counter.values()))

    def test_stop_event_cancels(self):
        import threading
        wf = self._build(user_intent={"need_e1": True, "need_m5": True})
        self._confirm(wf)
        ev = threading.Event()
        ev.set()

        def _blocking(step, workflow, exec_ctx):
            return _ok_executor(step, workflow, exec_ctx)

        ctx = _all_ok_ctx()
        ctx["gee_executor"] = _blocking
        result = wo.run_analysis_workflow(wf, exec_ctx=ctx, stop_event=ev)
        self.assertEqual(result["status"], wo.WF_CANCELLED)


# ---------------------------------------------------------------
#  4. find_ready_steps
# ---------------------------------------------------------------
class TestFindReadySteps(WorkflowOrchestratorTestBase):
    def test_initial_only_gee_ready(self):
        wf = self._build()
        ready = wo.find_ready_steps(wf)
        self.assertEqual([s["step_id"] for s in ready], ["gee_download"])

    def test_after_gee_inference_ready(self):
        wf = self._build()
        gee = wf["steps"][0]
        gee["status"] = wo.STEP_SUCCEEDED
        ready = wo.find_ready_steps(wf)
        self.assertEqual([s["step_id"] for s in ready], ["local_inference"])

    def test_blocked_by_failed_dep(self):
        wf = self._build(user_intent={"need_e1": True, "need_m5": True})
        gee = wf["steps"][0]
        gee["status"] = wo.STEP_FAILED
        wo.find_ready_steps(wf)
        by_id = {s["step_id"]: s for s in wf["steps"]}
        self.assertEqual(by_id["local_inference"]["status"], wo.STEP_BLOCKED)
        self.assertIn("依赖失败", by_id["local_inference"]["error"] or "")
        # 可选步骤同样 BLOCKED，而非 SKIPPED
        self.assertEqual(by_id["e1_quality"]["status"], wo.STEP_BLOCKED)
        self.assertEqual(by_id["m5_change"]["status"], wo.STEP_BLOCKED)

    def test_user_skipped_marked_skipped(self):
        wf = self._build(user_intent={"need_e1": False, "need_m5": False})
        gee = wf["steps"][0]
        gee["status"] = wo.STEP_SUCCEEDED
        inf = wf["steps"][1]
        inf["status"] = wo.STEP_SUCCEEDED
        wo.find_ready_steps(wf)
        by_id = {s["step_id"]: s for s in wf["steps"]}
        self.assertEqual(by_id["e1_quality"]["status"], wo.STEP_SKIPPED)
        self.assertEqual(by_id["m5_change"]["status"], wo.STEP_SKIPPED)


# ---------------------------------------------------------------
#  5. 账本（ledger）
# ---------------------------------------------------------------
class TestLedger(WorkflowOrchestratorTestBase):
    def test_ledger_upsert_and_restore(self):
        wf = self._build(user_intent={"need_e1": True, "need_m5": True})
        self._run(wf)
        ledger = wo.load_workflow_ledger()
        self.assertIn(wf["workflow_id"], ledger)
        row = ledger[wf["workflow_id"]]
        self.assertEqual(row["status"], wo.WF_SUCCEEDED)
        self.assertEqual(
            row["steps"]["gee_download"]["asset_id"], "asset_gee_download"
        )
        restored = wo.load_workflow(wf["workflow_id"])
        self.assertIsNotNone(restored)
        self.assertEqual(restored["workflow_id"], wf["workflow_id"])
        self.assertEqual(len(restored["steps"]), 5)
        restored_ids = {s["step_id"]: s["status"] for s in restored["steps"]}
        self.assertEqual(restored_ids["gee_download"], wo.STEP_SUCCEEDED)
        self.assertEqual(restored_ids["pdf_report"], wo.STEP_SUCCEEDED)

    def test_ledger_file_exists_after_run(self):
        wf = self._build()
        self._run(wf)
        self.assertTrue(os.path.isfile(wo.WORKFLOW_LEDGER_PATH))

    def test_ledger_no_workflow_id_skipped(self):
        wf = self._build()
        wf["workflow_id"] = ""
        wo._ledger_upsert(wf, status=wo.WF_RUNNING)
        self.assertEqual(wo.load_workflow_ledger(), {})

    def test_corrupt_ledger_is_preserved_and_rejected(self):
        path = wo.WORKFLOW_LEDGER_PATH
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"wf_bad": [')
        with self.assertRaises(ValueError):
            wo.load_workflow_ledger(path)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(list(__import__("pathlib").Path(self._data_dir).glob(
            "workflow_ledger.json.corrupt-*")))

    def test_invalid_ledger_record_is_preserved_and_rejected(self):
        path = wo.WORKFLOW_LEDGER_PATH
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"wf_bad": []}, handle)
        with self.assertRaises(ValueError):
            wo.load_workflow_ledger(path)
        self.assertTrue(list(__import__("pathlib").Path(self._data_dir).glob(
            "workflow_ledger.json.corrupt-*")))


# ---------------------------------------------------------------
#  6. 血缘（lineage）
# ---------------------------------------------------------------
class TestLineage(WorkflowOrchestratorTestBase):
    def test_git_head_resolution_does_not_spawn_subprocess(self):
        with patch("subprocess.run", side_effect=AssertionError("git subprocess is forbidden")):
            commit = wo._git_head_or_unknown()
        self.assertRegex(commit, r"^(?:[0-9a-f]{7}|unknown)$")

    def test_record_and_get_lineage(self):
        wo.record_asset_lineage(
            "ds_1", asset_type="dataset", workflow_id="wf_1", derived_from=[]
        )
        wo.record_asset_lineage(
            "pred_1", asset_type="prediction", workflow_id="wf_1",
            derived_from=["ds_1"]
        )
        wo.record_asset_lineage(
            "rep_1", asset_type="report", workflow_id="wf_1",
            derived_from=["pred_1", "ds_1"]
        )
        info = wo.get_asset_lineage("rep_1")
        self.assertTrue(info["found"])
        self.assertIn("pred_1", info["ancestors"])
        self.assertIn("ds_1", info["ancestors"])
        self.assertEqual(info["workflow_id"], "wf_1")
        # children
        ds_children = wo.get_asset_lineage("ds_1")["children"]
        self.assertIn("pred_1", ds_children)
        self.assertIn("rep_1", ds_children)

    def test_record_workflow_lineage_after_run(self):
        wf = self._build(user_intent={"need_e1": True, "need_m5": True})
        self._run(wf)
        wo.record_workflow_lineage(wf)
        for s in wf["steps"]:
            info = wo.get_asset_lineage(s["asset_id"])
            self.assertTrue(info["found"])
            self.assertEqual(info["workflow_id"], wf["workflow_id"])
        rep = wo.get_asset_lineage("asset_pdf_report")
        self.assertIn("asset_local_inference", rep["ancestors"])
        self.assertIn("asset_e1_quality", rep["ancestors"])
        self.assertIn("asset_m5_change", rep["ancestors"])

    def test_lineage_not_found(self):
        info = wo.get_asset_lineage("nope")
        self.assertFalse(info["found"])
        self.assertEqual(info["ancestors"], [])

    def test_corrupt_lineage_is_preserved_and_rejected(self):
        path = os.path.join(self._data_dir, "workflow_lineage.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"asset_bad": [')
        with self.assertRaises(ValueError):
            wo.get_asset_lineage("asset_bad")
        self.assertTrue(list(__import__("pathlib").Path(self._data_dir).glob(
            "workflow_lineage.json.corrupt-*")))

    def test_enrich_asset_metadata(self):
        out = wo.enrich_asset_metadata(
            {"k": "v"}, workflow_id="wf_x", derived_from=["a"],
            produced_by={"tool": "t", "plan_id": "p", "step_id": "s", "code_commit": "c"},
            asset_type="dataset",
        )
        self.assertEqual(out["workflow_id"], "wf_x")
        self.assertEqual(out["derived_from"], ["a"])
        self.assertEqual(out["asset_type"], "dataset")
        self.assertEqual(out["produced_by"]["tool"], "t")


# ---------------------------------------------------------------
#  7. 面向用户总结（grounded / 防泄漏）
# ---------------------------------------------------------------
class TestSummarize(WorkflowOrchestratorTestBase):
    def test_summary_grounded_status(self):
        wf = self._build(user_intent={"need_e1": True, "need_m5": True})
        self._run(wf)
        summary = wf["final_result"]["summary"]
        self.assertIn("已完成", summary)
        self.assertIn("获取卫星影像", summary)
        self.assertIn("成果报告", summary)
        self.assertIn("asset_gee_download", summary)

    def test_summary_no_llm_forged_success(self):
        wf = self._build(user_intent={"need_e1": True, "need_m5": True})
        by_id = {s["step_id"]: s for s in wf["steps"]}
        by_id["gee_download"]["status"] = wo.STEP_FAILED
        by_id["gee_download"]["error"] = "模拟失败"
        for sid in ("local_inference", "e1_quality", "m5_change", "pdf_report"):
            by_id[sid]["status"] = wo.STEP_BLOCKED
        status = wo._evaluate_workflow_status(wf)
        summary = wo.summarize_workflow_result_for_chat(wf, status)
        self.assertIn("失败", summary)
        self.assertNotIn("全部必需步骤成功", summary)

    def test_sensitive_value_redacted(self):
        filtered = wo._sensitive_filtered("下载失败: token=abc123")
        self.assertEqual(filtered, "<redacted>")
        self.assertNotIn("abc123", filtered)

    def test_path_in_step_error_is_redacted_in_summary(self):
        filtered = wo._sensitive_filtered("下载失败: /Users/chl/private/result.tif")
        self.assertNotIn("/Users/", filtered)
        self.assertNotIn("result.tif", filtered)

    def test_format_plan_for_user(self):
        wf = self._build(user_intent={"need_e1": True, "need_m5": True})
        text = wo.format_workflow_plan_for_user(wf)
        self.assertIn("一键潮滩分析", text)
        self.assertIn("24quanzhou", text)
        self.assertIn("获取卫星影像", text)
        self.assertIn("成果报告", text)
        self.assertIn("2024", text)

    def test_summary_failed_uses_real_error(self):
        wf = self._build()
        e1 = [s for s in wf["steps"] if s["step_id"] == "e1_quality"][0]
        e1["status"] = wo.STEP_FAILED
        e1["error"] = "参考真值目录为空"
        status = wo._evaluate_workflow_status(wf)
        summary = wo.summarize_workflow_result_for_chat(wf, status)
        # 失败来自真实步骤错误，非模型臆测
        self.assertIn("参考真值目录为空", summary) or self.assertIn("失败", summary)


if __name__ == "__main__":
    unittest.main()
