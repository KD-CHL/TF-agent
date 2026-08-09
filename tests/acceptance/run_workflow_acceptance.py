# -*- coding: utf-8 -*-
"""
潮滩分析 Workflow 编排器验收脚本（真实引擎 / 沙箱路径，不依赖浏览器）。

覆盖链路（复用既有模块，不重新实现）:
  AOI → GEE(override 复用) → 本地推理(真实引擎) → E1 评价(真实引擎)
      → M5 变化检测(真实引擎) → PDF 报告(真实引擎) → 血缘 / 账本

前置条件（自动完成）:
  - 若 input/24zhejiang1 影像退化(<100KB)，从 input/acceptance_tidal 暂存真实影像。
  - e1 参考(师姐_2020)与 M5 基线(20zhejiang1)均在 _e2e_sandbox 内。

用法（在仓库根目录，gwx 环境）:
  D:\\anaconda3\\envs\\gwx\\python.exe tests/acceptance/run_workflow_acceptance.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import traceback
import uuid
from datetime import datetime
from typing import Any, Dict, List

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_TF = os.path.join(_ROOT, "TF-agent")
if _TF not in sys.path:
    sys.path.insert(0, _TF)

import workflow_orchestrator as wo          # noqa: E402
from aoi_context import aoi_from_bbox        # noqa: E402

SANDBOX = os.path.join(_ROOT, "_e2e_sandbox")
TASK_ID = "24zhejiang1"
REGION = "zhejiang1"
TARGET_YEAR = 2024
BASELINE_YEAR = 2022        # 用户意图：与 2022 年成果比较（沙箱基线 20zhejiang1 经兜底解析）
PROB = 0.05
CNT = 2

ROOT_DIR = os.path.join(SANDBOX, "input")                     # 含 24zhejiang1/ 任务目录
FINAL_ROOT = os.path.join(SANDBOX, "output")                  # 含 20zhejiang1 基线
MASK_ROOT = os.path.join(SANDBOX, "output", "wf_acceptance_masks")
MODEL_PATH = os.path.join(_ROOT, "best_train_loss_model_resnet50.pth")
SHP_PATH = os.path.join(SANDBOX, "vectors", "roi_zhejiang1.shp")
E1_DATA_ROOT = os.path.join(SANDBOX, "data_root")
E1_REFERENCE = "师姐_2020"
REGISTRY_PATH = os.path.join(SANDBOX, "assets_registry.json")
REPORT_OUT_DIR = os.path.join(SANDBOX, "post_out", "wf_acceptance")

REAL_SCENES_SRC = os.path.join(SANDBOX, "input", "acceptance_tidal")
TASK_INPUT_DIR = os.path.join(ROOT_DIR, TASK_ID)


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
    except Exception as e:  # noqa: BLE001
        r.ok = False
        r.error = f"{e}\n{traceback.format_exc()}"
    return r


# ---------------------------------------------------------------
#  全局：构建 / 确认 / 执行 的共享上下文
# ---------------------------------------------------------------
_G = {
    "wf": None,
    "state": None,
    "result": None,
    "exec_ctx": None,
    "aoi": None,
}


def _scene_footprint_bbox() -> Tuple[float, float, float, float]:
    """真实沙箱影像足迹（EPSG:4326）：AOI 与岸线裁剪 shp 必须与影像一致。

    之前硬编码 120.8/30.2（浙江）与真实影像（约 119.9/26.7，福建沿岸）
    不重叠，导致后处理岸线裁剪把全部潮滩像元裁掉 → 合成结果为空。
    这里以影像本身为准，保证“AOI=影像足迹=裁剪 shp”。
    """
    from rasterio.warp import transform_bounds  # noqa: PLC0415
    import rasterio  # noqa: PLC0415

    lon_lo, lat_lo = 180.0, 90.0
    lon_hi, lat_hi = -180.0, -90.0
    found = False
    for name in sorted(os.listdir(REAL_SCENES_SRC)):
        if not name.lower().endswith(".tif"):
            continue
        try:
            with rasterio.open(os.path.join(REAL_SCENES_SRC, name)) as src:
                lo0, la0, lo1, la1 = transform_bounds(
                    src.crs, "EPSG:4326", *src.bounds)
                lon_lo, lat_lo = min(lon_lo, lo0), min(lat_lo, la0)
                lon_hi, lat_hi = max(lon_hi, lo1), max(lat_hi, la1)
                found = True
        except Exception:  # noqa: BLE001
            continue
    if not found:
        return 120.8, 30.15, 121.05, 30.35
    pad = 0.02
    return (lon_lo - pad, lat_lo - pad, lon_hi + pad, lat_hi + pad)


def _ensure_aoi_shp() -> str:
    """按影像足迹重新生成 roi_zhejiang1.shp（EPSG:4326 面）。

    海岸线裁剪掩膜必须覆盖真实影像范围，否则合成结果被整体裁掉。
    """
    import geopandas as gpd  # noqa: PLC0415
    from shapely.geometry import box  # noqa: PLC0415

    lon_lo, lat_lo, lon_hi, lat_hi = _scene_footprint_bbox()
    gdf = gpd.GeoDataFrame(
        {"name": ["aoi"], "geometry": [box(lon_lo, lat_lo, lon_hi, lat_hi)]},
        crs="EPSG:4326",
    )
    os.makedirs(os.path.dirname(SHP_PATH), exist_ok=True)
    gdf.to_file(SHP_PATH, encoding="utf-8")
    return SHP_PATH


def _build_aoi() -> dict:
    lon_lo, lat_lo, lon_hi, lat_hi = _scene_footprint_bbox()
    aoi = aoi_from_bbox(
        lon_lo, lat_lo, lon_hi, lat_hi,
        source="acceptance_bbox", label=REGION,
    )
    return aoi.to_dict()


def _build_exec_ctx(aoi: dict) -> Dict[str, Any]:
    os.makedirs(MASK_ROOT, exist_ok=True)
    os.makedirs(REPORT_OUT_DIR, exist_ok=True)

    def _gee_override(step, workflow, exec_ctx):  # noqa: ANN001
        """GEE 下载步骤：复用既有沙箱影像，不重新下载。"""
        return {
            "success": True,
            "status": wo.STEP_REUSED,
            "outputs": {
                "note": "复用既有沙箱影像（不重新下载）",
                "dataset": "existing_sandbox_scenes",
                "scene_files": sorted(
                    f for f in os.listdir(TASK_INPUT_DIR)
                    if f.lower().endswith(".tif") and "_mask" not in f
                ),
            },
            "assets": [{
                "asset_id": uuid.uuid4().hex,
                "asset_type": wo.ASSET_DATASET,
            }],
            "metrics": {"scene_count": 2},
            "warnings": [],
            "error": None,
        }

    return {
        "aoi": aoi,
        "registry": {},                       # 空快照 → E1/M5 走 resolve_*_shp 兜底
        "registry_path": REGISTRY_PATH,
        "report_output_dir": REPORT_OUT_DIR,
        "push_progress": lambda p: None,       # noqa: E731
        "baseline_task": "20zhejiang1",
        "gee_executor": _gee_override,
    }


def case_scene_staging() -> dict:
    """若任务影像退化(<100KB)，用真实影像覆盖（保持任务目录与 M5 基线）。

    同时按影像足迹重建岸线裁剪 shp，保证后处理裁剪掩膜覆盖潮滩像元。
    """
    os.makedirs(TASK_INPUT_DIR, exist_ok=True)
    staged = []
    for name in ("scene_001.tif", "scene_002.tif"):
        dst = os.path.join(TASK_INPUT_DIR, name)
        src = os.path.join(REAL_SCENES_SRC, name)
        degenerate = (not os.path.isfile(dst)) or os.path.getsize(dst) < 100 * 1024
        if degenerate and os.path.isfile(src):
            shutil.copy2(src, dst)
            staged.append(name)
    sizes = {
        f: os.path.getsize(os.path.join(TASK_INPUT_DIR, f))
        for f in sorted(os.listdir(TASK_INPUT_DIR))
        if f.lower().endswith(".tif")
    }
    shp_ok = False
    try:
        _ensure_aoi_shp()
        shp_ok = os.path.isfile(SHP_PATH)
    except Exception as e:  # noqa: BLE001
        shp_ok = False
        staged.append(f"shp_regen_failed: {e}")
    ok = (
        all(sizes.get(f, 0) >= 100 * 1024 for f in ("scene_001.tif", "scene_002.tif"))
        and shp_ok
    )
    return {
        "passed": ok,
        "staged": staged,
        "shp_rebuilt": shp_ok,
        "task_input_dir": TASK_INPUT_DIR,
        "sizes": sizes,
        "reason": None if ok else "scene staging failed (scenes degenerate or shp not rebuilt)",
    }


def case_workflow_build() -> dict:
    os.makedirs(MASK_ROOT, exist_ok=True)
    os.makedirs(REPORT_OUT_DIR, exist_ok=True)
    aoi = _build_aoi()
    _G["aoi"] = aoi
    wf = wo.build_analysis_workflow(
        aoi=aoi,
        target_year=TARGET_YEAR,
        baseline_year=BASELINE_YEAR,
        task_id=TASK_ID,
        region=REGION,
        prob=PROB,
        cnt=CNT,
        root_dir=ROOT_DIR,
        final_root=FINAL_ROOT,
        mask_root=MASK_ROOT,
        model_path=MODEL_PATH,
        shp_path=SHP_PATH,
        e1_data_root=E1_DATA_ROOT,
        e1_reference=E1_REFERENCE,
        start_date="2024-01-01",
        end_date="2024-12-31",
        user_intent={"need_e1": None, "need_m5": None, "need_report": True},
    )
    _G["wf"] = wf
    ok, blockers, warnings = wo.validate_analysis_workflow(wf)
    step_ids = [s["step_id"] for s in wf["steps"]]
    conds = {s["step_id"]: s.get("condition") for s in wf["steps"]}
    return {
        "passed": ok,
        "workflow_id": wf["workflow_id"],
        "task_id": wf["task_id"],
        "step_ids": step_ids,
        "conditions": conds,
        "blockers": blockers,
        "warnings": warnings,
        "reason": None if ok else f"validate blocked: {blockers}",
    }


def case_workflow_confirm() -> dict:
    wf = _G["wf"]
    assert wf is not None, "workflow not built"
    state = {
        "_workflow_pending_plan": wf,
        "_workflow_plan_confirmed": set(),
    }
    wo.confirm_workflow(state, wf["workflow_id"])
    _G["state"] = state
    ok = (
        wf.get("confirmed") is True
        and wf.get("status") == wo.WF_CONFIRMED
        and wf.get("confirmation_source") == "parent_workflow"
    )
    return {
        "passed": ok,
        "confirmed": wf.get("confirmed"),
        "status": wf.get("status"),
        "confirmation_source": wf.get("confirmation_source"),
        "approved_params_keys": sorted(
            (wf.get("approved_params") or {}).keys()),
        "reason": None if ok else "workflow not confirmed",
    }


def case_workflow_run() -> dict:
    wf = _G["wf"]
    assert wf is not None, "workflow not built"
    _G["exec_ctx"] = _build_exec_ctx(_G["aoi"] or _build_aoi())
    stop_event = threading.Event()
    logs: List[str] = []

    result = wo.run_analysis_workflow(
        wf,
        exec_ctx=_G["exec_ctx"],
        push_log=lambda m: logs.append(str(m)),
        stop_event=stop_event,
    )
    _G["result"] = result
    steps = {
        s["step_id"]: {
            "tool": s.get("tool"),
            "status": s.get("status"),
            "required": s.get("required"),
            "plan_id": s.get("plan_id"),
            "asset_id": s.get("asset_id"),
            "error": s.get("error"),
        }
        for s in wf["steps"]
    }
    ok = result.get("status") in (
        wo.WF_SUCCEEDED, wo.WF_COMPLETED_WITH_WARNINGS,
    )
    return {
        "passed": ok,
        "workflow_status": result.get("status"),
        "steps": steps,
        "assets": result.get("assets") or {},
        "errors": result.get("errors") or [],
        "warnings": result.get("warnings") or [],
        "summary": (result.get("summary") or "")[:400],
        "log_tail": logs[-8:],
        "reason": None if ok else f"workflow status={result.get('status')}",
    }


def case_workflow_lineage() -> dict:
    wf = _G["wf"]
    result = _G["result"]
    assert wf is not None and result is not None, "run first"
    wo.record_workflow_lineage(wf)
    steps = {s["step_id"]: s for s in wf["steps"]}
    infer = steps.get("local_inference") or {}
    pred_id = infer.get("asset_id")
    lineage = wo.get_asset_lineage(pred_id) if pred_id else {"found": False}
    gee = steps.get("gee_download") or {}
    dataset_id = gee.get("asset_id")
    ok = bool(
        lineage.get("found")
        and lineage.get("workflow_id") == wf.get("workflow_id")
        and dataset_id in (lineage.get("derived_from") or [])
    )
    return {
        "passed": ok,
        "prediction_asset_id": pred_id,
        "dataset_asset_id": dataset_id,
        "lineage": lineage,
        "reason": None if ok else "lineage missing / not derived from dataset asset",
    }


def case_workflow_ledger() -> dict:
    wf = _G["wf"]
    result = _G["result"]
    assert wf is not None and result is not None, "run first"
    ledger = wo.load_workflow_ledger()
    row = ledger.get(wf["workflow_id"]) or {}
    ok = bool(
        row.get("workflow_id") == wf["workflow_id"]
        and row.get("status") in (wo.WF_SUCCEEDED, wo.WF_COMPLETED_WITH_WARNINGS)
        and bool(row.get("steps"))
    )
    return {
        "passed": ok,
        "ledger_row": row,
        "ledger_size": len(ledger),
        "reason": None if ok else "ledger row missing or stale",
    }


def case_report_artifact() -> dict:
    wf = _G["wf"]
    result = _G["result"]
    assert wf is not None and result is not None, "run first"
    assets = result.get("assets") or {}
    report_id = assets.get("report")
    # 报告路径登记在 pdf_report 步骤 result.outputs.report_path（assets["report"] 是资产 id）
    step = next((s for s in wf["steps"] if s.get("step_id") == "pdf_report"), {}) or {}
    step_res = step.get("result") or {}
    path = (step_res.get("outputs") or {}).get("report_path")
    ok = bool(
        report_id
        and path
        and os.path.isfile(str(path))
    )
    return {
        "passed": ok,
        "report_asset_id": report_id,
        "report_path": path,
        "report_asset": assets.get("report"),
        "reason": None if ok else "PDF report file missing",
    }


def main() -> int:
    started = datetime.now().isoformat(timespec="seconds")
    cases = [
        _case("scene_staging", case_scene_staging),
        _case("workflow_build_validate", case_workflow_build),
        _case("workflow_confirm", case_workflow_confirm),
        _case("workflow_run_full_chain", case_workflow_run),
        _case("workflow_lineage", case_workflow_lineage),
        _case("workflow_ledger", case_workflow_ledger),
        _case("report_artifact", case_report_artifact),
    ]

    report = {
        "started_at": started,
        "finished_at": None,
        "task": TASK_ID,
        "results": [c.to_dict() for c in cases],
        "passed": sum(1 for c in cases if c.ok),
        "failed": sum(1 for c in cases if not c.ok),
        "total": len(cases),
    }
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    report["all_ok"] = report["failed"] == 0

    out_dir = os.path.join(_ROOT, "tests", "acceptance", "_out")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "workflow_acceptance_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("=" * 64)
    print("Workflow Orchestrator Acceptance Report")
    print("=" * 64)
    for c in cases:
        mark = "PASS" if c.ok else "FAIL"
        print(f"[{mark}] {c.name}")
        if not c.ok:
            print("       ", (c.error or c.detail.get("reason") or "")[:400])
    print("-" * 64)
    print(f"{report['passed']}/{report['total']} passed → {out_path}")
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
