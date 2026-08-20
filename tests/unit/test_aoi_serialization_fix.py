# -*- coding: utf-8 -*-
"""回归测试：_active_aoi 为 AOIContext 对象时，下游 dict 消费方不得崩溃。

背景：aoi_map_bridge.process_aoi_selected 把 AOIContext（dataclass 对象）存入
st.session_state['_active_aoi']，而 agent_command_bridge / workflow_orchestrator /
gee_agent_loop 按 dict 消费（dict(obj) 抛 TypeError），导致 GEE 步骤以
「AOI 无效（必须是合法 GeoJSON Polygon）」失败、下游全部 BLOCKED。
本测试锁定归一化修复：AOIContext → to_dict()，空几何 → 明确失败提示。
"""
from __future__ import annotations

import os
import sys

import pytest

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

from aoi_context import aoi_from_bbox  # noqa: E402


def _make_aoi(label="福建_测试区"):
    return aoi_from_bbox(120.6, 30.2, 121.2, 30.9, source="map_polygon", label=label)


class TestAoiStateToDict:
    def test_aoicontext_object_converted(self):
        import agent_command_bridge as acb

        state = {"_active_aoi": _make_aoi()}
        d = acb._aoi_state_to_dict(state)
        assert isinstance(d, dict)
        assert d.get("geometry", {}).get("type") == "Polygon"
        assert d.get("valid") is True
        assert d.get("bbox") == (120.6, 30.2, 121.2, 30.9)

    def test_plain_dict_passthrough(self):
        import agent_command_bridge as acb

        aoi_dict = _make_aoi().to_dict()
        state = {"_active_aoi": aoi_dict}
        assert acb._aoi_state_to_dict(state) == aoi_dict

    def test_missing_or_none_returns_empty(self):
        import agent_command_bridge as acb

        assert acb._aoi_state_to_dict({}) == {}
        assert acb._aoi_state_to_dict({"_active_aoi": None}) == {}


class TestProposeWorkflowNoCrash:
    def test_aoicontext_in_state_does_not_crash(self):
        """用户在地图绘制 AOI（对象形式）→ propose_workflow_plan 不得崩溃。"""
        import agent_command_bridge as acb

        state = {"_active_aoi": _make_aoi()}
        action = {
            "type": "propose_workflow",
            "target_year": 2020,
            "baseline_year": 2020,
            "task": "20fujian1",
            "region": "福建",
            "need_m5": True,
            "need_report": True,
        }
        wf, errs = acb.propose_workflow_plan(state, action)
        assert not any("未解析到有效 AOI" in e for e in errs), f"errs={errs}"
        assert wf.get("workflow_id")
        step_ids = [s["step_id"] for s in wf.get("steps", [])]
        assert step_ids == ["gee_download", "local_inference", "e1_quality",
                            "m5_change", "pdf_report"]
        assert wf["context"]["aoi_id"]


class TestGeePlanAoi:
    def test_valid_aoi_dict_ready(self):
        """AOIContext.to_dict() 必须能构建 ready 的 GEE 计划（无「AOI 无效」blocker）。"""
        import gee_agent_loop as gal

        aoi = _make_aoi()
        plan = gal.build_gee_download_plan(
            task_id="20fujian1",
            aoi=aoi.to_dict(),
            start_date="2020-01-01", end_date="2020-12-31",
            local_out_dir=r"E:\Data\20fujian1",
        )
        assert plan["ready"], f"blockers={plan.get('blockers')}"
        assert not any("AOI 无效" in str(b) for b in plan.get("blockers") or [])
        assert (plan.get("aoi") or {}).get("geometry", {}).get("type") == "Polygon"

    def test_empty_geometry_blocked(self):
        """无几何的 dict（旧回退路径，valid 缺省 True）必须被阻塞。"""
        import gee_agent_loop as gal

        plan = gal.build_gee_download_plan(
            task_id="20fujian1",
            aoi={"aoi_id": "x", "label": "x", "source": "map_polygon"},
            start_date="2020-01-01", end_date="2020-12-31",
            local_out_dir=r"E:\Data\20fujian1",
        )
        assert not plan["ready"]
        assert any("AOI 无效" in str(b) for b in plan.get("blockers") or [])


class TestRunGeeStepAoiHandling:
    @pytest.fixture()
    def wf(self):
        import workflow_orchestrator as wo

        aoi = _make_aoi()
        return wo.build_analysis_workflow(
            aoi=aoi.to_dict(),
            target_year=2020,
            baseline_year=2020,
            task_id="20fujian1",
            region="福建",
            root_dir=r"E:\Data",
            final_root=r"E:\Data\843output",
            mask_root=r"E:\Data\843mask",
            model_path=r"E:\Code\GEE\best_train_loss_model_resnet50.pth",
            user_intent={"need_m5": True, "need_report": True},
        )

    def _run_gee(self, wf, aoi):
        import threading

        import workflow_orchestrator as wo

        logs = []
        return wo._run_gee_step(
            wf["steps"][0], wf,
            exec_ctx={"aoi": aoi,
                      "registry_path": os.path.join(_TF_AGENT, "assets_registry.json")},
            push_log=logs.append,
            stop_event=threading.Event(),
        )

    def test_aoicontext_object_normalized(self, wf):
        """exec_ctx aoi 为 AOIContext 对象：不得再报「AOI 无效」，只允许执行期错误。"""
        res = self._run_gee(wf, _make_aoi())
        err = str(res.get("error") or "")
        assert "AOI 无效" not in err
        assert "AOI" not in err[:30]  # 不能是 AOI 相关失败

    def test_dict_normalized(self, wf):
        res = self._run_gee(wf, _make_aoi().to_dict())
        err = str(res.get("error") or "")
        assert "AOI 无效" not in err

    def test_missing_aoi_actionable_error(self, wf):
        """无 AOI（未绘制）→ 明确可操作提示，而不是晦涩 blocker。"""
        res = self._run_gee(wf, None)
        assert res["success"] is False
        assert "绘制" in str(res.get("error") or "")
        assert res["status"] == "FAILED"

    def test_empty_geometry_dict_actionable_error(self, wf):
        res = self._run_gee(wf, {"aoi_id": "x", "label": "x", "source": "map_polygon"})
        assert res["success"] is False
        assert "绘制" in str(res.get("error") or "")


class TestFlushExceptionIsolation:
    def test_crashing_command_does_not_raise(self):
        """单条指令异常必须被隔离为 errors，不能拖垮 flush 调用方（Streamlit 脚本）。"""
        import agent_command_bridge as acb

        state = {"_active_aoi": _make_aoi(), acb.PENDING_AGENT_COMMANDS_KEY: [
            {"type": "bogus", "pending_action": {"type": "boom"}},
        ]}
        # 不 mock apply_system_command：若 flush 内部异常被捕获即通过
        result = acb.flush_pending_agent_commands(state)
        assert result.applied is True
        # 无论指令是否被识别，都不允许抛出异常
        assert acb.PENDING_AGENT_COMMANDS_KEY not in state
