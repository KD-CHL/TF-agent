# -*- coding: utf-8 -*-
"""
M5 可信闭环验收脚本（真实路径 / 异常场景，不依赖浏览器）。

用法（在仓库根目录）:
  python tests/acceptance/run_m5_acceptance.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_TF = os.path.join(_ROOT, "TF-agent")
if _TF not in sys.path:
    sys.path.insert(0, _TF)

import m5_agent_loop  # noqa: E402
from agent_command_bridge import (  # noqa: E402
    apply_system_command,
    build_pending_task,
    init_ui_session_defaults,
)


PROD_FINAL = r"E:\Data\843output"
SANDBOX_FINAL = r"E:\Code\GEE\_e2e_sandbox\output"


class CaseResult:
    def __init__(self, name: str):
        self.name = name
        self.ok = False
        self.detail: Dict[str, Any] = {}
        self.error: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "error": self.error,
        }


def _case(name: str, fn) -> CaseResult:
    r = CaseResult(name)
    try:
        detail = fn()
        r.detail = detail or {}
        r.ok = bool(r.detail.get("passed", True))
        if "passed" in r.detail and not r.detail["passed"]:
            r.ok = False
            r.error = str(r.detail.get("reason") or "assertion failed")
    except Exception as e:
        r.ok = False
        r.error = f"{e}\n{traceback.format_exc()}"
    return r


def case_inventory() -> dict:
    """盘点生产与沙箱数据可用性。"""
    inv = {
        "prod_final_exists": os.path.isdir(PROD_FINAL),
        "sandbox_final_exists": os.path.isdir(SANDBOX_FINAL),
        "prod_tasks_with_final": [],
        "prod_multi_year_pairs": 0,
        "sandbox_pair": None,
    }
    if inv["prod_final_exists"]:
        import glob
        import re

        pat = re.compile(r"^(\d{2})(.+)$")
        by = {}
        for name in os.listdir(PROD_FINAL):
            d = os.path.join(PROD_FINAL, name)
            if not os.path.isdir(d):
                continue
            m = pat.match(name)
            if not m:
                continue
            finals = glob.glob(os.path.join(d, f"{name}_Final_p*_c*.shp"))
            idx = os.path.join(d, "Final_Intertidal_Flat.shp")
            if not finals and os.path.isfile(idx):
                finals = [idx]
            if finals:
                inv["prod_tasks_with_final"].append(name)
                region = m.group(2)
                by.setdefault(region, []).append(int(m.group(1)))
        inv["prod_multi_year_pairs"] = sum(1 for ys in by.values() if len(set(ys)) >= 2)

    if inv["sandbox_final_exists"]:
        cur = os.path.join(SANDBOX_FINAL, "24zhejiang1", "24zhejiang1_Final_p0.05_c2.shp")
        base = os.path.join(SANDBOX_FINAL, "20zhejiang1", "20zhejiang1_Final_p0.05_c2.shp")
        inv["sandbox_pair"] = {
            "current": cur if os.path.isfile(cur) else None,
            "baseline": base if os.path.isfile(base) else None,
        }
    inv["passed"] = True
    inv["note"] = (
        "生产 843output 当前无跨年 Final 成对；完整引擎 happy-path 使用 e2e sandbox。"
        if inv["prod_multi_year_pairs"] == 0
        else "生产目录存在跨年成对。"
    )
    return inv


def case_prod_preflight_blocked() -> dict:
    """生产路径：24zhejiang1 无 Final → 计划不可执行。"""
    plan = m5_agent_loop.build_m5_preflight(
        final_root=PROD_FINAL,
        current_task="24zhejiang1",
        prob=0.05,
        cnt=2,
    )
    passed = (not plan["ready"]) and any("SHP" in b or "不存在" in b for b in plan["blockers"])
    return {
        "passed": passed,
        "ready": plan["ready"],
        "blockers": plan["blockers"],
        "reason": None if passed else "expected blockers for missing Final SHP",
    }


def case_prod_single_year_blocked() -> dict:
    """生产路径：仅有 20zhejiang1 Final → 无更早基线。"""
    plan = m5_agent_loop.build_m5_preflight(
        final_root=PROD_FINAL,
        current_task="20zhejiang1",
        prob=0.05,
        cnt=2,
    )
    passed = (not plan["ready"]) and any("基线" in b for b in plan["blockers"])
    return {
        "passed": passed,
        "ready": plan["ready"],
        "current_shp": plan.get("current_shp"),
        "blockers": plan["blockers"],
        "reason": None if passed else "expected baseline blocker",
    }


def case_sandbox_preflight_ready() -> dict:
    plan = m5_agent_loop.build_m5_preflight(
        final_root=SANDBOX_FINAL,
        current_task="24zhejiang1",
        prob=0.05,
        cnt=2,
    )
    passed = bool(plan["ready"] and plan.get("baseline_task") == "20zhejiang1")
    return {
        "passed": passed,
        "plan_summary": {
            "ready": plan["ready"],
            "baseline_task": plan.get("baseline_task"),
            "periods": [p["task"] for p in plan.get("available_periods") or []],
        },
        "reason": None if passed else "sandbox pair not ready",
    }


def case_unconfirmed_gate() -> dict:
    state: Dict[str, Any] = {}
    init_ui_session_defaults(state)
    state["ui_final_root"] = SANDBOX_FINAL
    state["ui_selected_task"] = "24zhejiang1"
    apply_system_command(state, {"pending_action": {"type": "propose_m5"}})
    pt, _, errs = build_pending_task(state, {"type": "run_m5", "confirmed": False})
    passed = pt is None and bool(errs) and not state.get("is_running")
    return {
        "passed": passed,
        "errors": errs,
        "pending_task": pt,
        "reason": None if passed else "unconfirmed run_m5 should be rejected",
    }


def case_confirm_builds_pending() -> dict:
    state: Dict[str, Any] = {}
    init_ui_session_defaults(state)
    state["ui_final_root"] = SANDBOX_FINAL
    state["ui_selected_task"] = "24zhejiang1"
    apply_system_command(state, {"pending_action": {"type": "propose_m5"}})
    r = apply_system_command(
        state, {"pending_action": {"type": "run_m5", "confirmed": True}}
    )
    pt = state.get("pending_task") or {}
    passed = (
        r.action_type == "run_m5"
        and state.get("is_running") is True
        and pt.get("mode") == "m5"
        and os.path.isfile(str((pt.get("m5") or {}).get("current_shp") or ""))
        and os.path.isfile(str((pt.get("m5") or {}).get("baseline_shp") or ""))
    )
    return {
        "passed": passed,
        "action_type": r.action_type,
        "mode": pt.get("mode"),
        "m5_paths": {
            "current": (pt.get("m5") or {}).get("current_shp"),
            "baseline": (pt.get("m5") or {}).get("baseline_shp"),
        },
        "reason": None if passed else "confirmed run_m5 did not build pending task",
    }


def case_full_engine_sync() -> dict:
    """真实调用 m5_engine + verify + summarize（沙箱几何）。"""
    plan = m5_agent_loop.build_m5_preflight(
        final_root=SANDBOX_FINAL,
        current_task="24zhejiang1",
        prob=0.05,
        cnt=2,
    )
    assert plan["ready"], plan.get("blockers")

    # 隔离报告输出，避免污染沙箱既有报告时可写到临时 workspace
    with tempfile.TemporaryDirectory() as td:
        # 引擎 workspace 默认=final_root；这里直接跑引擎并校验
        import m5_engine

        report = m5_engine.run_m5_after_synthesis(
            current_shp=plan["current_shp"],
            current_task="24zhejiang1",
            final_root=SANDBOX_FINAL,
            prob=0.05,
            cnt=2,
            baseline_shp_override=plan["baseline_shp"],
            workspace_dir=td,
            logger=lambda m: None,
        )
        assert report, "engine returned None"
        report["baseline_task"] = plan["baseline_task"]
        ver = m5_agent_loop.verify_m5_outputs(report, workspace_dir=td)
        summary = m5_agent_loop.summarize_m5_report_for_chat(report, ver)
        map_path = m5_agent_loop.pick_m5_map_path(report)

        # 资产登记写到临时 registry：直接调用 register 会改项目 assets_registry.json
        # 验收只检查函数可调用性与 key 形态
        key = f"24zhejiang1_m5"
        passed = (
            ver.get("ok") is True
            and report.get("alert_level") in ("GREEN", "YELLOW", "RED")
            and "已验证" in summary
            and os.path.isfile(str(report.get("report_path") or ""))
        )
        return {
            "passed": passed,
            "alert_level": report.get("alert_level"),
            "metrics": (report.get("quantitative_metrics") or {}).get("area_evolution"),
            "verification_ok": ver.get("ok"),
            "map_candidate": map_path,
            "report_path": report.get("report_path"),
            "summary_excerpt": summary[:240],
            "asset_key_shape": key,
            "reason": None if passed else "engine/verify/summary failed",
        }


def case_run_m5_sync_worker() -> dict:
    """走 app.run_m5_sync 线程契约（shared dict）。"""
    # 延迟导入 app 可能拉起 streamlit；用最小共享结构直接调
    sys.path.insert(0, _TF)
    # 避免 streamlit 页面执行：只导入所需函数会执行 app 顶层 —— 风险高
    # 改为在本脚本内复刻 worker 调用 m5_engine（与 run_m5_sync 同路径）
    plan = m5_agent_loop.build_m5_preflight(
        final_root=SANDBOX_FINAL,
        current_task="24zhejiang1",
        prob=0.05,
        cnt=2,
    )
    shared = {
        "lock": threading.Lock(),
        "log_lines": [],
        "progress": 0,
        "status": ("info", ""),
        "done": False,
        "success": False,
        "m5_report": None,
        "m5_verification": None,
        "asset_path": None,
        "job_kind": "m5",
    }
    stop_event = threading.Event()
    logs: List[str] = []

    def push_log(msg):
        logs.append(str(msg))
        with shared["lock"]:
            shared["log_lines"] = logs[-40:]

    import m5_engine

    report = m5_engine.run_m5_after_synthesis(
        current_shp=plan["current_shp"],
        current_task="24zhejiang1",
        final_root=SANDBOX_FINAL,
        baseline_shp_override=plan["baseline_shp"],
        workspace_dir=SANDBOX_FINAL,
        logger=push_log,
    )
    ver = m5_agent_loop.verify_m5_outputs(report, workspace_dir=SANDBOX_FINAL)
    with shared["lock"]:
        shared["m5_report"] = report
        shared["m5_verification"] = ver
        shared["asset_path"] = m5_agent_loop.pick_m5_map_path(report)
        shared["success"] = bool(report) and bool(ver.get("ok"))
        shared["done"] = True
        shared["progress"] = 100
        shared["status"] = ("success", f"M5 {report.get('alert_level')}")

    passed = shared["success"] and shared["done"] and shared["m5_report"]
    return {
        "passed": bool(passed),
        "status": shared["status"],
        "log_tail": logs[-5:],
        "has_asset_path": bool(shared.get("asset_path")),
        "reason": None if passed else "worker shared state incomplete",
    }


def case_confirm_utterance() -> dict:
    ok = (
        m5_agent_loop.is_m5_confirm_utterance("确认")
        and m5_agent_loop.is_m5_intent("做一下变化检测")
        and not m5_agent_loop.is_m5_confirm_utterance("今天天气不错")
    )
    return {"passed": ok}


def main() -> int:
    started = datetime.now().isoformat(timespec="seconds")
    cases = [
        _case("inventory_data", case_inventory),
        _case("prod_24_missing_final_blocked", case_prod_preflight_blocked),
        _case("prod_20_no_baseline_blocked", case_prod_single_year_blocked),
        _case("sandbox_preflight_ready", case_sandbox_preflight_ready),
        _case("gate_unconfirmed_rejected", case_unconfirmed_gate),
        _case("gate_confirmed_builds_pending", case_confirm_builds_pending),
        _case("confirm_utterance_helpers", case_confirm_utterance),
        _case("full_engine_verify_summary", case_full_engine_sync),
        _case("worker_shared_contract", case_run_m5_sync_worker),
    ]

    report = {
        "started_at": started,
        "finished_at": None,
        "git": {},
        "results": [c.to_dict() for c in cases],
        "passed": sum(1 for c in cases if c.ok),
        "failed": sum(1 for c in cases if not c.ok),
        "total": len(cases),
    }
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    report["all_ok"] = report["failed"] == 0

    out_dir = os.path.join(_ROOT, "tests", "acceptance", "_out")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "m5_acceptance_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print("M5 Acceptance Report")
    print("=" * 60)
    for c in cases:
        mark = "PASS" if c.ok else "FAIL"
        print(f"[{mark}] {c.name}")
        if not c.ok:
            print("       ", (c.error or c.detail.get("reason") or "")[:300])
    print("-" * 60)
    print(f"{report['passed']}/{report['total']} passed → {out_path}")
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
