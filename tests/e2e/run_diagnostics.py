# -*- coding: utf-8 -*-
"""潮滩系统 E2E 诊断执行器。"""
from __future__ import annotations

import io
import json
import os
import sys
import traceback
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SANDBOX = REPO_ROOT / "_e2e_sandbox"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "TF-agent"))
sys.path.insert(0, str(REPO_ROOT / "research" / "jb"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures"))

# Windows 控制台 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


@dataclass
class TestResult:
    name: str
    passed: bool
    message: str = ""
    traceback: str = ""


@dataclass
class DiagnosticReport:
    results: List[TestResult] = field(default_factory=list)
    bugs_fixed: List[dict] = field(default_factory=list)

    def run(self, name: str, fn: Callable[[], Any]) -> None:
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                fn()
            self.results.append(TestResult(name, True, "OK"))
            print(f"  [PASS] {name}")
        except Exception as e:
            tb = traceback.format_exc()
            self.results.append(TestResult(name, False, str(e), tb))
            print(f"  [FAIL] {name}: {e}")

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total_count(self) -> int:
        return len(self.results)


def _assets(manifest: dict) -> dict:
    return manifest["assets"]


def _load_manifest() -> dict:
    p = SANDBOX / "sandbox_manifest.json"
    if not p.is_file():
        raise FileNotFoundError(f"请先运行 tests/fixtures/generate_sandbox_data.py，缺少 {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def test_asset_registry(manifest: dict) -> None:
    reg = json.loads(Path(_assets(manifest)["registry"]).read_text(encoding="utf-8"))
    assert "24zhejiang1_p0.05_c2" in reg
    assert os.path.isfile(reg["24zhejiang1_p0.05_c2"]["file_path"])
    assert "24zhejiang1_index" in reg
    assert reg["24zhejiang1_index"]["file_path"].endswith(".tif")
    # Final TIF 路径拦截：带 _NUMERATOR 的不应出现在沙盒注册表
    for k, v in reg.items():
        fp = v.get("file_path", "")
        assert "_NUMERATOR" not in fp and "_DENOMINATOR" not in fp


def test_m5_jb(manifest: dict) -> None:
    from M5 import M5_AnomalyDetector

    ws = str(SANDBOX / "m5_workspace_jb")
    det = M5_AnomalyDetector(workspace_dir=ws)
    r = det.detect_anomalies(
        baseline_shp=_assets(manifest)["baseline_shp"],
        current_shp=_assets(manifest)["current_shp"],
        roi_name="sandbox_zhejiang1",
        thresh_drift_dist_m=50.0,
    )
    assert r["alert_level"] in ("GREEN", "YELLOW", "RED")
    assert os.path.isfile(
        os.path.join(ws, "outputs_m5_advanced", "ADVANCED_ALERT_REPORT_sandbox_zhejiang1.json")
    )


def test_m5_engine(manifest: dict) -> None:
    import m5_engine

    r = m5_engine.run_m5_after_synthesis(
        current_shp=_assets(manifest)["current_shp"],
        current_task="24zhejiang1",
        final_root=_assets(manifest)["final_root"],
        task_options=["20zhejiang1", "24zhejiang1"],
        prob=0.05,
        cnt=2,
        workspace_dir=str(SANDBOX / "m5_workspace_engine"),
        logger=lambda m: None,
    )
    assert r is not None and r.get("alert_level")


def test_m5_crs_align(manifest: dict) -> None:
    from M5 import M5_AnomalyDetector

    det = M5_AnomalyDetector(workspace_dir=str(SANDBOX / "m5_edge"))
    r = det.detect_anomalies(
        baseline_shp=_assets(manifest)["baseline_shp"],
        current_shp=_assets(manifest)["current_utm_shp"],
        roi_name="crs_align",
        thresh_drift_dist_m=99999,
    )
    assert r["quantitative_metrics"]["area_evolution"]["baseline_area_km2"] >= 0


def test_m5_disjoint(manifest: dict) -> None:
    """空交集边界：完全不相交时 M5 仍应完成（面积为 0 变化）。"""
    from M5 import M5_AnomalyDetector

    det = M5_AnomalyDetector(workspace_dir=str(SANDBOX / "m5_disjoint"))
    r = det.detect_anomalies(
        baseline_shp=_assets(manifest)["baseline_shp"],
        current_shp=_assets(manifest)["disjoint_shp"],
        roi_name="disjoint_test",
    )
    assert r is not None


def test_e1_pixel(manifest: dict) -> None:
    from E1 import E1_DataCleanerAndDiagnostic

    ws = str(SANDBOX / "e1_workspace")
    e1 = E1_DataCleanerAndDiagnostic(workspace_dir=ws, data_root=_assets(manifest)["data_root"])
    assert "师姐_2020" in e1.list_datasets()
    r = e1.run_pixel_comparison(
        reference="师姐_2020",
        target_path=_assets(manifest)["current_shp"],
        target_name="YYnet_Product",
        compare_sources=["FCS30_2020", "DCTF_2020"],
        roi_path=_assets(manifest)["roi_shp"],
        roi_name="sandbox_zhejiang1",
        export_rasters=False,
        export_disagreement_maps=False,
        export_multi_product_heatmap=False,
    )
    assert r["comparisons"], "E1 comparisons 为空"


def test_e1_engine(manifest: dict) -> None:
    import e1_engine

    r = e1_engine.run_e1_after_synthesis(
        target_shp=_assets(manifest)["current_shp"],
        roi_name="sandbox_zhejiang1",
        workspace_dir=str(SANDBOX / "e1_engine_ws"),
        data_root=_assets(manifest)["data_root"],
        reference="师姐_2020",
        compare_sources=["FCS30_2020", "DCTF_2020"],
        roi_path=_assets(manifest)["roi_shp"],
        export_disagreement_maps=False,
        export_multi_product_heatmap=False,
        logger=lambda m: None,
    )
    assert r is not None and r.get("comparisons")


def test_e1_dirty_polygon(manifest: dict) -> None:
    from E1 import E1_DataCleanerAndDiagnostic

    e1 = E1_DataCleanerAndDiagnostic(
        workspace_dir=str(SANDBOX / "e1_dirty"), data_root=_assets(manifest)["data_root"]
    )
    gdf = e1.normalize_vector(_assets(manifest)["dirty_shp"], "Dirty_Test", save=False)
    assert not gdf.empty


def test_evaluation_geo(manifest: dict) -> None:
    import geopandas as gpd
    from evaluation_geo import clip_truth_to_task_aoi, filter_aoi_for_task

    aoi = gpd.read_file(_assets(manifest)["roi_shp"])
    sub = filter_aoi_for_task(aoi, "24zhejiang1")
    assert not sub.empty
    ref = gpd.read_file(_assets(manifest)["ref_2020_shp"])
    clipped = clip_truth_to_task_aoi(ref, _assets(manifest)["roi_shp"], "24zhejiang1", logger=None)
    assert not clipped.empty


def test_combine(manifest: dict) -> None:
    import io
    import contextlib
    from combine import evaluate_tif_vs_shp

    with contextlib.redirect_stdout(io.StringIO()):
        iou, precision, recall = evaluate_tif_vs_shp(
            _assets(manifest)["tif_2024"],
            _assets(manifest)["ref_2020_shp"],
            task_aoi_shp_path=_assets(manifest)["roi_shp"],
            task_name="24zhejiang1",
        )
    assert 0.0 <= float(iou) <= 1.0


def test_post_engine(manifest: dict) -> None:
    import importlib
    import os
    import shutil
    import sys

    os.environ["TQDM_DISABLE"] = "1"
    # combine 测试会把旧版 YYnet 插到 sys.path 首位，需强制使用 YYnet-main
    _main = str(REPO_ROOT / "TF-agent")
    if _main in sys.path:
        sys.path.remove(_main)
    sys.path.insert(0, _main)
    post_engine = importlib.import_module("post_engine")
    if "post_engine" in sys.modules:
        sys.modules["post_engine"] = post_engine
    a = _assets(manifest)
    out_dir = SANDBOX / "post_out" / "24zhejiang1"
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_shp = out_dir / "24zhejiang1_Final_p0.05_c2.shp"
    ok = post_engine.generate_double_constraint_complete(
        source_folder=a["input_dir"],
        mask_folder=a["input_dir"],
        output_path=str(out_shp),
        shp_path=a["roi_shp"],
        prob_threshold=0.05,
        min_absolute_count=1,
        logger=lambda m: None,
    )
    if not ok:
        raise AssertionError("generate_double_constraint_complete 返回 False")
    if not out_shp.is_file():
        raise AssertionError(f"输出 SHP 不存在: {out_shp}")


def test_globe_server() -> None:
    import globe_server

    port = globe_server.ensure_running(preferred_port=18765)
    assert globe_server._server_healthy(port)


def test_pipeline_task_guard() -> None:
    src = (REPO_ROOT / "TF-agent" / "app.py").read_text(encoding="utf-8")
    assert "if not actual_task or not root_dir:" in src


def main() -> DiagnosticReport:
    from generate_sandbox_data import generate_all

    if not (SANDBOX / "sandbox_manifest.json").is_file():
        generate_all()

    manifest = _load_manifest()
    report = DiagnosticReport()

    print("\n=== E2E 诊断开始 ===\n")
    tests = [
        ("资产账本 JSON 读写与路径拦截", lambda: test_asset_registry(manifest)),
        ("M5 jb 三维度告警", lambda: test_m5_jb(manifest)),
        ("M5 engine 自动基线", lambda: test_m5_engine(manifest)),
        ("M5 边界: CRS 自动对齐", lambda: test_m5_crs_align(manifest)),
        ("M5 边界: 空交集", lambda: test_m5_disjoint(manifest)),
        ("E1 像元级 IoU", lambda: test_e1_pixel(manifest)),
        ("E1 engine 封装", lambda: test_e1_engine(manifest)),
        ("E1 边界: 脏多边形", lambda: test_e1_dirty_polygon(manifest)),
        ("evaluation_geo AOI 裁剪", lambda: test_evaluation_geo(manifest)),
        ("jb/combine TIF vs SHP", lambda: test_combine(manifest)),
        ("post_engine 时空合成", lambda: test_post_engine(manifest)),
        ("globe_server 健康检查", lambda: test_globe_server()),
        ("app.py 空 task 防御", lambda: test_pipeline_task_guard()),
    ]
    for name, fn in tests:
        report.run(name, fn)

    print(f"\n=== 完成: {report.passed_count}/{report.total_count} 通过 ===\n")
    return report


if __name__ == "__main__":
    r = main()
    out = SANDBOX / "e2e_results.json"
    out.write_text(
        json.dumps(
            {
                "passed": r.passed_count,
                "total": r.total_count,
                "results": [{"name": x.name, "passed": x.passed, "message": x.message} for x in r.results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    sys.exit(0 if r.passed_count == r.total_count else 1)
