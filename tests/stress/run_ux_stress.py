# -*- coding: utf-8 -*-
"""UX 极限压力测试：空交集、脏拓扑、路径容错、分母零防护。"""
from __future__ import annotations

import io
import json
import contextlib
import os
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SANDBOX = REPO_ROOT / "_e2e_sandbox"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "research" / "jb"))
sys.path.insert(0, str(REPO_ROOT / "TF-agent"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures"))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _manifest():
    p = SANDBOX / "sandbox_manifest.json"
    if not p.is_file():
        from generate_sandbox_data import generate_all
        generate_all()
    return json.loads(p.read_text(encoding="utf-8"))["assets"]


def _run(name, fn):
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            fn()
        print(f"  [PASS] {name}")
        return True
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        if os.environ.get("CSTF_DEBUG"):
            traceback.print_exc()
        return False


def test_m5_zero_overlap(a):
    from M5 import M5_AnomalyDetector
    import cstf_ux as ux

    ws = str(SANDBOX / "ux_m5_disjoint")
    det = M5_AnomalyDetector(ws, logger=lambda m: None)
    r = det.detect_anomalies(a["baseline_shp"], a["disjoint_shp"], "ux_disjoint")
    assert r.get("spatial_overlap") is False
    assert r["quantitative_metrics"]["area_evolution"]["change_rate_percentage"] == 0.0
    assert "无任何重叠" in r["diagnostic_message"] or "0.00%" in r["diagnostic_message"]


def test_m5_path_trailing_slash(a):
    from M5 import M5_AnomalyDetector
    import cstf_ux as ux

    base = a["baseline_shp"] + "\\"
    curr = a["current_shp"].replace("\\", "/") + "/"
    ws = str(SANDBOX / "ux_m5_path")
    det = M5_AnomalyDetector(ws, logger=lambda m: None)
    r = det.detect_anomalies(base, curr, "ux_path")
    assert r.get("alert_level")


def test_e1_zero_overlap(a):
    from E1 import E1_DataCleanerAndDiagnostic

    ws = str(SANDBOX / "ux_e1_disjoint")
    e1 = E1_DataCleanerAndDiagnostic(ws, data_root=a["data_root"])
    r = e1.run_pixel_comparison(
        reference="师姐_2020",
        target_path=a["disjoint_shp"],
        compare_sources=["FCS30_2020"],
        roi_path=a["roi_shp"],
        roi_name="ux_disjoint",
        export_rasters=False,
        export_disagreement_maps=False,
        export_multi_product_heatmap=False,
    )
    assert r["comparisons"], "comparisons 为空"
    assert any(
        c.get("skipped_raster_compare") or float(c.get("jaccard_iou", -1)) == 0.0
        for c in r["comparisons"].values()
    )


def test_e1_dirty_topology(a):
    from E1 import E1_DataCleanerAndDiagnostic

    e1 = E1_DataCleanerAndDiagnostic(str(SANDBOX / "ux_e1_dirty"), data_root=a["data_root"])
    gdf = e1.normalize_vector(a["dirty_shp"], "Dirty", save=False)
    assert not gdf.empty


def test_m5_engine_overlap(a):
    import m5_engine

    r = m5_engine.run_m5_after_synthesis(
        current_shp=a["current_shp"],
        current_task="24zhejiang1",
        final_root=a["final_root"],
        baseline_shp_override=a["disjoint_shp"],
        workspace_dir=str(SANDBOX / "ux_m5_engine"),
        logger=lambda m: None,
    )
    assert r is not None
    assert r.get("spatial_overlap") is False


def main():
    print("\n=== UX 极限压力测试 ===\n")
    a = _manifest()
    tests = [
        ("M5 零重叠安全退出", lambda: test_m5_zero_overlap(a)),
        ("M5 路径尾斜杠容错", lambda: test_m5_path_trailing_slash(a)),
        ("M5 engine 无重叠报告", lambda: test_m5_engine_overlap(a)),
        ("E1 零重叠 IoU=0 跳过", lambda: test_e1_zero_overlap(a)),
        ("E1 脏拓扑自愈", lambda: test_e1_dirty_topology(a)),
    ]
    ok = sum(_run(n, f) for n, f in tests)
    print(f"\n=== 完成: {ok}/{len(tests)} 通过 ===\n")
    return ok == len(tests)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
