# -*- coding: utf-8 -*-
import json, os, sys, tempfile
from pathlib import Path

sys.path.insert(0, r"E:\Code\GEE\TF-agent")
import m5_agent_loop
import m5_engine
from agent_command_bridge import apply_system_command, init_ui_session_defaults, build_pending_task

SANDBOX = r"E:\Code\GEE\_e2e_sandbox\output"
PROD = r"E:\Data\843output"
out = {"success": {}, "no_baseline": {}}

state = {}
init_ui_session_defaults(state)
state["ui_final_root"] = SANDBOX
state["ui_selected_task"] = "24zhejiang1"
apply_system_command(state, {"pending_action": {"type": "propose_m5", "task": "24zhejiang1"}})
plan = state["_m5_pending_plan"]
assert plan["ready"], plan["blockers"]
pt, _, errs = build_pending_task(state, {"type": "run_m5", "confirmed": False})
assert pt is None and errs
apply_system_command(state, {"pending_action": {"type": "run_m5", "confirmed": True}})
pt = state["pending_task"]
assert pt["mode"] == "m5"

with tempfile.TemporaryDirectory() as td:
    report = m5_engine.run_m5_after_synthesis(
        current_shp=plan["current_shp"],
        current_task="24zhejiang1",
        final_root=SANDBOX,
        baseline_shp_override=plan["baseline_shp"],
        workspace_dir=td,
        logger=lambda m: print(str(m).encode("ascii", "replace").decode("ascii")),
    )
    assert report
    report["baseline_task"] = plan["baseline_task"]
    ver = m5_agent_loop.verify_m5_outputs(report, workspace_dir=td)
    assert ver["ok"]
    summary = m5_agent_loop.summarize_m5_report_for_chat(report, ver)
    from app import register_m5_asset, load_asset_registry

    key = register_m5_asset("24zhejiang1", report)
    entry = load_asset_registry().get(key)
    spatial = report.get("spatial_outputs") or {}
    loss = spatial.get("loss_shapefile_path")
    silt = spatial.get("siltation_shapefile_path")
    has_diff = (loss and str(loss) != "None" and os.path.isfile(str(loss))) or (
        silt and str(silt) != "None" and os.path.isfile(str(silt))
    )
    out["success"] = {
        "task": "24zhejiang1",
        "baseline": plan["baseline_task"],
        "ready_before_confirm": True,
        "blocked_without_confirm": True,
        "alert": report.get("alert_level"),
        "metrics": (report.get("quantitative_metrics") or {}).get("area_evolution"),
        "report_path": report.get("report_path"),
        "report_exists": os.path.isfile(report["report_path"]),
        "has_diff_shp": bool(has_diff),
        "loss": loss,
        "silt": silt,
        "asset_key": key,
        "asset_registered": bool(entry),
        "summary_has_alert": report.get("alert_level", "") in summary,
        "summary_excerpt": summary[:500],
        "verify_ok": ver["ok"],
    }

state2 = {}
init_ui_session_defaults(state2)
state2["ui_final_root"] = PROD
state2["ui_selected_task"] = "20zhejiang1"
apply_system_command(state2, {"pending_action": {"type": "propose_m5", "task": "20zhejiang1"}})
plan2 = state2["_m5_pending_plan"]
assert not plan2["ready"]
assert any("基线" in b for b in plan2["blockers"])
pt2, _, errs2 = build_pending_task(state2, {"type": "run_m5", "confirmed": True})
summary2 = m5_agent_loop.format_m5_plan_for_user(plan2)
out["no_baseline"] = {
    "task": "20zhejiang1",
    "ready": False,
    "blockers": plan2["blockers"],
    "pending_task_created": pt2 is not None,
    "blocked_execution": pt2 is None,
    "no_success_language": ("检测完成" not in summary2 and "已验证" not in summary2),
}

Path(r"E:\Code\GEE\tests\acceptance\_out").mkdir(parents=True, exist_ok=True)
Path(r"E:\Code\GEE\tests\acceptance\_out\m5_minimal_real.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(out, ensure_ascii=False, indent=2))
print("OK")
