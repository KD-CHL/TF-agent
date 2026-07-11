"""
M5 时空异常检测与告警引擎。

在区域潮滩合成完成后，将当期 SHP 与往年同区域潮滩 SHP 对比，
输出告警级别、诊断结论及 loss/siltation 差异面。
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import geopandas as gpd
import numpy as np

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import cstf_ux as ux

_TASK_RE = re.compile(r"^(\d{2})(.+)$")


def parse_task_identity(task_name: str) -> Tuple[Optional[int], str]:
    """解析任务名，如 24zhejiang1 → (24, zhejiang1)。"""
    m = _TASK_RE.match(task_name or "")
    if not m:
        return None, task_name or ""
    return int(m.group(1)), m.group(2)


def find_final_shp_in_task_dir(
    task_dir: str,
    task_name: str,
    prob: Optional[float] = None,
    cnt: Optional[int] = None,
) -> Optional[str]:
    """在任务输出目录中查找最终潮滩 SHP（深度学习 / 指数法）。"""
    if not os.path.isdir(task_dir):
        return None
    if prob is not None and cnt is not None:
        preferred = os.path.join(task_dir, f"{task_name}_Final_p{prob:.2f}_c{cnt}.shp")
        if os.path.isfile(preferred):
            return preferred
    shps = sorted(glob.glob(os.path.join(task_dir, f"{task_name}_Final_p*_c*.shp")))
    if shps:
        return shps[-1]
    index_shp = os.path.join(task_dir, "Final_Intertidal_Flat.shp")
    if os.path.isfile(index_shp):
        return index_shp
    return None


def _iter_candidate_tasks(final_root: str, task_options: Optional[List[str]]) -> List[str]:
    names = set(task_options or [])
    if os.path.isdir(final_root):
        for name in os.listdir(final_root):
            if os.path.isdir(os.path.join(final_root, name)):
                names.add(name)
    return sorted(names)


def find_baseline_for_task(
    final_root: str,
    current_task: str,
    task_options: Optional[List[str]] = None,
    prob: Optional[float] = None,
    cnt: Optional[int] = None,
    logger: Callable = print,
) -> Tuple[Optional[str], Optional[str]]:
    """
    在 final_root 中查找「同区域、最近更早年份」的基线潮滩 SHP。
    返回 (baseline_task, baseline_shp_path)。
    """
    year, region = parse_task_identity(current_task)
    if year is None or not region:
        logger(f"[M5] 任务名 {current_task} 无法解析年份前缀，跳过自动基线匹配。")
        return None, None

    candidates: List[Tuple[int, str, str]] = []
    for task in _iter_candidate_tasks(final_root, task_options):
        y, r = parse_task_identity(task)
        if y is None or r != region or y >= year:
            continue
        task_dir = os.path.join(final_root, task)
        shp = find_final_shp_in_task_dir(task_dir, task, prob, cnt)
        if shp:
            candidates.append((y, task, shp))

    if not candidates:
        logger(f"[M5] 未找到区域 [{region}] 在 {year} 年之前的潮滩基线 SHP。")
        return None, None

    candidates.sort(key=lambda x: x[0], reverse=True)
    _y, base_task, base_shp = candidates[0]
    logger(f"[M5] 自动匹配基线: {base_task} → {os.path.basename(base_shp)}")
    return base_task, base_shp


def m5_report_path(workspace_dir: str, roi_name: str) -> str:
    out_dir = os.path.join(workspace_dir, "outputs_m5_advanced")
    return os.path.join(out_dir, f"ADVANCED_ALERT_REPORT_{roi_name}.json")


def load_m5_report(workspace_dir: str, roi_name: str) -> Optional[Dict]:
    path = m5_report_path(workspace_dir, roi_name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _utm_epsg_from_geometry(geom) -> int:
    """按几何质心自动选择 UTM 带，保证面积/距离单位为米。"""
    c = geom.centroid
    lon, lat = float(c.x), float(c.y)
    zone = int((lon + 180.0) / 6.0) + 1
    return (32600 + zone) if lat >= 0 else (32700 + zone)


class M5_AnomalyDetector:
    def __init__(self, workspace_dir: str, logger: Callable = print):
        self.logger = logger
        self.workspace = ux.ensure_dir(workspace_dir, logger=logger)
        self.output_dir = ux.ensure_dir(
            os.path.join(self.workspace, "outputs_m5_advanced"), logger=logger
        )

    def detect_anomalies(
        self,
        baseline_shp: str,
        current_shp: str,
        roi_name: str,
        thresh_reduction: float = -15.0,
        thresh_siltation: float = 10.0,
        thresh_compact_growth: float = 25.0,
        thresh_drift_dist_m: float = 1500.0,
        min_anomaly_area_m2: float = 5000.0,
        logger: Optional[Callable] = None,
    ) -> Dict:
        log = logger or self.logger
        baseline_shp = ux.normalize_path(baseline_shp, must_exist=True)
        current_shp = ux.normalize_path(current_shp, must_exist=True)
        roi_name = (roi_name or "roi").strip()

        log(f"\n🔍 [M5] 启动时空格局监测: [{roi_name}]")

        gdf_base = ux.repair_geometries(gpd.read_file(baseline_shp), logger=log)
        gdf_curr = ux.repair_geometries(gpd.read_file(current_shp), logger=log)
        if gdf_base.empty or gdf_curr.empty:
            ux.warn("基线期或监测期矢量为空，跳过指标计算。", log)
            return self._zero_overlap_report(roi_name, baseline_shp, current_shp, log, "empty_geometry")

        if gdf_base.crs != gdf_curr.crs:
            log("[M5] 两期坐标系不一致，正在重投影对齐…")
            gdf_curr = gdf_curr.to_crs(gdf_base.crs)

        union_for_zone = gdf_base.unary_union
        if gdf_base.crs and gdf_base.crs.is_geographic:
            utm_epsg = _utm_epsg_from_geometry(union_for_zone)
            log(f"[M5] 地理坐标系 → UTM EPSG:{utm_epsg}（面积/距离单位：米）")
            gdf_base = gdf_base.to_crs(epsg=utm_epsg)
            gdf_curr = gdf_curr.to_crs(epsg=utm_epsg)

        union_base = gdf_base.unary_union
        union_curr = gdf_curr.unary_union

        if not ux.geometries_have_overlap(union_base, union_curr):
            ux.warn(ux.zero_overlap_message_m5(), log)
            return self._zero_overlap_report(roi_name, baseline_shp, current_shp, log)

        area_base = float(union_base.area)
        area_curr = float(union_curr.area)
        change_rate = ux.safe_pct_change(area_curr, area_base, default=0.0)

        compactness_base = ux.safe_div(
            4 * np.pi * area_base, union_base.length ** 2 if union_base.length > 0 else 0, default=0.0
        )
        compactness_curr = ux.safe_div(
            4 * np.pi * area_curr, union_curr.length ** 2 if union_curr.length > 0 else 0, default=0.0
        )
        compact_change_rate = ux.safe_pct_change(compactness_curr, compactness_base, default=0.0)

        centroid_base = union_base.centroid
        centroid_curr = union_curr.centroid
        drift_distance = float(centroid_base.distance(centroid_curr))
        dx = centroid_curr.x - centroid_base.x
        dy = centroid_curr.y - centroid_base.y
        azimuth = (np.degrees(np.arctan2(dx, dy)) + 360.0) % 360.0

        log(
            f"[M5] 面积 {area_base / 1e6:.2f}→{area_curr / 1e6:.2f} km² "
            f"({change_rate:+.2f}%) | 重心漂移 {drift_distance:.1f} m"
        )

        log("[M5] 空间差集：定位萎缩/淤积区…")
        try:
            reduction_gdf = gpd.overlay(gdf_base, gdf_curr, how="difference")
            siltation_gdf = gpd.overlay(gdf_curr, gdf_base, how="difference")
        except Exception as e:
            ux.warn(f"差集运算异常，已按空变化图层处理: {e}", log)
            reduction_gdf = gpd.GeoDataFrame(geometry=[], crs=gdf_base.crs)
            siltation_gdf = gpd.GeoDataFrame(geometry=[], crs=gdf_base.crs)

        if not reduction_gdf.empty:
            reduction_gdf = reduction_gdf[reduction_gdf.geometry.area > min_anomaly_area_m2]
        if not siltation_gdf.empty:
            siltation_gdf = siltation_gdf[siltation_gdf.geometry.area > min_anomaly_area_m2]

        alert_level = "GREEN"
        reasons: List[str] = []

        if change_rate <= thresh_reduction:
            alert_level = "RED"
            reasons.append(f"潮滩绝对面积剧烈萎缩({change_rate:.2f}%)")

        if compact_change_rate >= thresh_compact_growth and change_rate < thresh_siltation:
            alert_level = "RED"
            reasons.append(
                f"边界不自然拉直异动：景观紧凑度剧烈跳变({compact_change_rate:.2f}%)，"
                f"疑似遭遇重大局部人工围垦"
            )

        if change_rate >= thresh_siltation and alert_level != "RED":
            alert_level = "YELLOW"
            reasons.append(
                f"面积出现爆发性扩张淤积({change_rate:.2f}%)，"
                f"需排查互花米草侵草或航道淤塞风险"
            )

        if drift_distance >= thresh_drift_dist_m:
            alert_level = (
                "RED"
                if alert_level == "RED" or compact_change_rate >= thresh_compact_growth
                else "YELLOW"
            )
            reasons.append(
                f"地理质心发生超限迁移(偏移距离: {drift_distance:.1f}米)，"
                f"海岸带格局发生空间非平衡相变"
            )

        alert_message = " | ".join(reasons) if reasons else "海岸带各空间测度指标处于正常动态平衡态。"
        log(f"[M5] 告警级别 [{alert_level}] → {alert_message}")

        reduction_path = os.path.join(self.output_dir, f"{roi_name}_loss_zones.shp")
        siltation_path = os.path.join(self.output_dir, f"{roi_name}_siltation_zones.shp")
        if not reduction_gdf.empty:
            reduction_gdf.to_file(reduction_path, encoding="utf-8")
        else:
            reduction_path = None
        if not siltation_gdf.empty:
            siltation_gdf.to_file(siltation_path, encoding="utf-8")
        else:
            siltation_path = None

        report = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "target_roi": roi_name,
            "baseline_task": None,
            "baseline_shp": baseline_shp,
            "current_shp": current_shp,
            "spatial_overlap": True,
            "alert_level": alert_level,
            "diagnostic_message": alert_message,
            "quantitative_metrics": {
                "area_evolution": {
                    "baseline_area_km2": round(area_base / 1e6, 3),
                    "current_area_km2": round(area_curr / 1e6, 3),
                    "change_rate_percentage": round(change_rate, 2),
                },
                "centroid_trajectory": {
                    "drift_distance_meters": round(float(drift_distance), 2),
                    "migration_azimuth_degrees": round(float(azimuth), 2),
                    "vector_displacement_dx_dy": [round(float(dx), 2), round(float(dy), 2)],
                },
                "landscape_compactness": {
                    "baseline_compactness_index": round(float(compactness_base), 5),
                    "current_compactness_index": round(float(compactness_curr), 5),
                    "complexity_change_rate_percentage": round(float(compact_change_rate), 2),
                },
            },
            "spatial_outputs": {
                "loss_shapefile_path": reduction_path or "None",
                "siltation_shapefile_path": siltation_path or "None",
            },
        }

        report_path = m5_report_path(self.workspace, roi_name)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=4)
        report["report_path"] = report_path
        log(f"[M5] 告警报告已保存: {report_path}")
        return report

    def _zero_overlap_report(
        self,
        roi_name: str,
        baseline_shp: str,
        current_shp: str,
        logger: Callable,
        reason: str = "no_overlap",
    ) -> Dict:
        report = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "target_roi": roi_name,
            "baseline_task": None,
            "baseline_shp": baseline_shp,
            "current_shp": current_shp,
            "spatial_overlap": False,
            "zero_overlap_reason": reason,
            "alert_level": "GREEN",
            "diagnostic_message": ux.zero_overlap_message_m5(),
            "quantitative_metrics": {
                "area_evolution": {
                    "baseline_area_km2": 0.0,
                    "current_area_km2": 0.0,
                    "change_rate_percentage": 0.0,
                },
                "centroid_trajectory": {
                    "drift_distance_meters": 0.0,
                    "migration_azimuth_degrees": 0.0,
                    "vector_displacement_dx_dy": [0.0, 0.0],
                },
                "landscape_compactness": {
                    "baseline_compactness_index": 0.0,
                    "current_compactness_index": 0.0,
                    "complexity_change_rate_percentage": 0.0,
                },
            },
            "spatial_outputs": {
                "loss_shapefile_path": "None",
                "siltation_shapefile_path": "None",
            },
        }
        report_path = m5_report_path(self.workspace, roi_name)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=4)
        report["report_path"] = report_path
        logger(f"[M5] 无重叠安全退出，报告: {report_path}")
        return report


def run_m5_after_synthesis(
    current_shp: str,
    current_task: str,
    final_root: str,
    task_options: Optional[List[str]] = None,
    prob: Optional[float] = None,
    cnt: Optional[int] = None,
    baseline_shp_override: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    logger: Callable = print,
    **detect_kwargs,
) -> Optional[Dict]:
    """
    合成完成后自动运行 M5：找基线 → 对比 → 写 JSON 报告。
    找不到基线或 current_shp 无效时返回 None（不阻断主流程）。
    """
    if not current_shp or not os.path.isfile(current_shp):
        logger("[M5] 当期潮滩 SHP 不存在，跳过异常检测。")
        return None

    current_shp = ux.normalize_path(current_shp) or current_shp
    final_root = ux.normalize_path(final_root) or final_root
    workspace = ux.normalize_path(workspace_dir or final_root) or (workspace_dir or final_root)
    baseline_task = None
    baseline_shp = (baseline_shp_override or "").strip() or None

    if baseline_shp:
        baseline_shp = ux.normalize_path(baseline_shp) or baseline_shp
        if not os.path.isfile(baseline_shp):
            logger(f"[M5] 指定的基线 SHP 不存在: {baseline_shp}")
            return None
        logger(f"[M5] 使用手动指定基线: {os.path.basename(baseline_shp)}")
    else:
        baseline_task, baseline_shp = find_baseline_for_task(
            final_root, current_task, task_options, prob, cnt, logger=logger
        )
        if not baseline_shp:
            return None

    detector = M5_AnomalyDetector(workspace_dir=workspace)
    try:
        report = detector.detect_anomalies(
            baseline_shp=baseline_shp,
            current_shp=current_shp,
            roi_name=current_task,
            logger=logger,
            **detect_kwargs,
        )
        report["baseline_task"] = baseline_task
        return report
    except Exception as e:
        logger(f"[M5] 异常检测失败: {e}")
        return None
