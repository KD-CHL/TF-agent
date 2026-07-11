# -*- coding: utf-8 -*-
"""M5 高级空间异常检测与告警（含空交集拦截、拓扑自愈、路径归一化）。"""
import json
import os
import sys
import warnings
from datetime import datetime

import geopandas as gpd
import numpy as np

warnings.filterwarnings("ignore")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import cstf_ux as ux


class M5_AnomalyDetector:
    def __init__(self, workspace_dir: str, logger=print):
        self.logger = logger
        self.workspace = ux.ensure_dir(workspace_dir, logger=logger)
        self.output_dir = ux.ensure_dir(
            os.path.join(self.workspace, "outputs_m5_advanced"), logger=logger
        )
        ux.banner("M5 时空异常检测与告警", logger=logger)
        self.logger(f"  工作目录: {self.output_dir}")

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
    ):
        baseline_shp = ux.normalize_path(baseline_shp, must_exist=True)
        current_shp = ux.normalize_path(current_shp, must_exist=True)
        roi_name = (roi_name or "roi").strip()

        self.logger(f"\n🔍 启动高维时空格局监测: [{roi_name}]")
        self.logger(f"  ├─ 基线期: {os.path.basename(baseline_shp)}")
        self.logger(f"  └─ 监测期: {os.path.basename(current_shp)}")

        gdf_base = ux.repair_geometries(gpd.read_file(baseline_shp), logger=self.logger)
        gdf_curr = ux.repair_geometries(gpd.read_file(current_shp), logger=self.logger)
        if gdf_base.empty or gdf_curr.empty:
            ux.warn("基线期或监测期矢量为空，无法计算变化指标。", self.logger)
            return self._zero_overlap_report(
                roi_name, baseline_shp, current_shp, reason="empty_geometry"
            )

        if gdf_base.crs != gdf_curr.crs:
            self.logger("  🔄 两期坐标系不一致，正在重投影对齐…")
            gdf_curr = gdf_curr.to_crs(gdf_base.crs)

        if gdf_base.crs and gdf_base.crs.is_geographic:
            self.logger("  🌐 地理坐标 → UTM 51N（面积/距离单位：米）")
            gdf_base = gdf_base.to_crs(epsg=32651)
            gdf_curr = gdf_curr.to_crs(epsg=32651)

        union_base = gdf_base.unary_union
        union_curr = gdf_curr.unary_union

        if not ux.geometries_have_overlap(union_base, union_curr):
            ux.warn(ux.zero_overlap_message_m5(), self.logger)
            return self._zero_overlap_report(roi_name, baseline_shp, current_shp)

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

        self.logger("  📐 地学指标")
        self.logger(f"  ├─ 基线面积: {area_base / 1e6:.2f} km² | 当期: {area_curr / 1e6:.2f} km²")
        self.logger(f"  ├─ 面积变化: {change_rate:+.2f}%")
        self.logger(f"  ├─ 紧凑度变化: {compact_change_rate:+.2f}%")
        self.logger(f"  └─ 重心漂移: {drift_distance:.1f} m，方位 {azimuth:.1f}°")

        self.logger("  ✂️ 空间差集：定位萎缩/淤积区…")
        try:
            reduction_gdf = gpd.overlay(gdf_base, gdf_curr, how="difference")
            siltation_gdf = gpd.overlay(gdf_curr, gdf_base, how="difference")
        except Exception as e:
            ux.warn(f"差集运算异常，已按空变化图层处理: {e}", self.logger)
            reduction_gdf = gpd.GeoDataFrame(geometry=[], crs=gdf_base.crs)
            siltation_gdf = gpd.GeoDataFrame(geometry=[], crs=gdf_base.crs)

        if not reduction_gdf.empty:
            reduction_gdf = reduction_gdf[reduction_gdf.geometry.area > min_anomaly_area_m2]
        if not siltation_gdf.empty:
            siltation_gdf = siltation_gdf[siltation_gdf.geometry.area > min_anomaly_area_m2]

        alert_level, alert_message = self._evaluate_alerts(
            change_rate,
            compact_change_rate,
            drift_distance,
            thresh_reduction,
            thresh_siltation,
            thresh_compact_growth,
            thresh_drift_dist_m,
        )
        self.logger(f"  🚦 告警级别 [{alert_level}] → {alert_message}")

        reduction_path, siltation_path = self._export_change_layers(
            roi_name, reduction_gdf, siltation_gdf
        )
        report = self._build_report(
            roi_name,
            baseline_shp,
            current_shp,
            alert_level,
            alert_message,
            area_base,
            area_curr,
            change_rate,
            compactness_base,
            compactness_curr,
            compact_change_rate,
            drift_distance,
            azimuth,
            dx,
            dy,
            reduction_path,
            siltation_path,
            spatial_overlap=True,
        )
        report_path = os.path.join(self.output_dir, f"ADVANCED_ALERT_REPORT_{roi_name}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=4)
        ux.success(f"告警报告已保存: {report_path}", self.logger)
        report["report_path"] = report_path
        return report

    def _zero_overlap_report(self, roi_name, baseline_shp, current_shp, reason="no_overlap"):
        reduction_path, siltation_path = self._export_change_layers(
            roi_name,
            gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"),
            gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"),
        )
        report = self._build_report(
            roi_name,
            baseline_shp,
            current_shp,
            "GREEN",
            ux.zero_overlap_message_m5(),
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            reduction_path,
            siltation_path,
            spatial_overlap=False,
            zero_overlap_reason=reason,
        )
        report_path = os.path.join(self.output_dir, f"ADVANCED_ALERT_REPORT_{roi_name}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=4)
        report["report_path"] = report_path
        return report

    @staticmethod
    def _evaluate_alerts(
        change_rate,
        compact_change_rate,
        drift_distance,
        thresh_reduction,
        thresh_siltation,
        thresh_compact_growth,
        thresh_drift_dist_m,
    ):
        alert_level = "GREEN"
        reasons = []
        if change_rate <= thresh_reduction:
            alert_level = "RED"
            reasons.append(f"潮滩绝对面积剧烈萎缩({change_rate:.2f}%)")
        if compact_change_rate >= thresh_compact_growth and change_rate < thresh_siltation:
            alert_level = "RED"
            reasons.append(
                f"边界不自然拉直异动：景观紧凑度剧烈跳变({compact_change_rate:.2f}%)"
            )
        if change_rate >= thresh_siltation and alert_level != "RED":
            alert_level = "YELLOW"
            reasons.append(f"面积爆发性扩张淤积({change_rate:.2f}%)")
        if drift_distance >= thresh_drift_dist_m:
            alert_level = (
                "RED"
                if alert_level == "RED" or compact_change_rate >= thresh_compact_growth
                else "YELLOW"
            )
            reasons.append(f"地理质心超限迁移({drift_distance:.1f}米)")
        msg = " | ".join(reasons) if reasons else "海岸带各空间测度指标处于正常动态平衡态。"
        return alert_level, msg

    def _export_change_layers(self, roi_name, reduction_gdf, siltation_gdf):
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
        return reduction_path, siltation_path

    @staticmethod
    def _build_report(
        roi_name,
        baseline_shp,
        current_shp,
        alert_level,
        alert_message,
        area_base,
        area_curr,
        change_rate,
        compactness_base,
        compactness_curr,
        compact_change_rate,
        drift_distance,
        azimuth,
        dx,
        dy,
        reduction_path,
        siltation_path,
        spatial_overlap=True,
        zero_overlap_reason=None,
    ):
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "target_roi": roi_name,
            "baseline_shp": baseline_shp,
            "current_shp": current_shp,
            "spatial_overlap": spatial_overlap,
            "zero_overlap_reason": zero_overlap_reason,
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


def _main():
    ws = ux.normalize_path(r"E:\Code\GEE\_e2e_sandbox\m5_ux_workspace") or r"E:\Code\GEE\_e2e_sandbox\m5_ux_workspace"
    base = ux.normalize_path(r"E:\Code\GEE\_e2e_sandbox\output\20zhejiang1\20zhejiang1_Final_p0.05_c2.shp")
    curr = ux.normalize_path(r"E:\Code\GEE\_e2e_sandbox\output\24zhejiang1\24zhejiang1_Final_p0.05_c2.shp")
    if not base or not curr or not os.path.isfile(base):
        ux.warn("请先在 _e2e_sandbox 生成测试数据，或修改 __main__ 中的路径。", print)
        return
    det = M5_AnomalyDetector(workspace_dir=ws)
    det.detect_anomalies(base, curr, "sandbox_m5")


if __name__ == "__main__":
    sys.exit(ux.run_cli_main(_main, "M5 异常检测"))
