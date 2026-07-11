# -*- coding: utf-8 -*-
"""
E2E 诊断沙盒：生成全套模拟潮滩解译测试资产（不依赖 E:\\ 盘物理数据）。
输出目录: <repo>/_e2e_sandbox
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box

ROOT = Path(__file__).resolve().parent.parent.parent / "_e2e_sandbox"
CRS_WGS84 = "EPSG:4326"
CRS_UTM = "EPSG:32651"

# 杭州湾附近小范围（WGS84），各资产均有交集
BOUNDS = (120.80, 30.15, 121.05, 30.35)
ORIGIN_LON, ORIGIN_LAT = 120.80, 30.35
PIXEL_SIZE_DEG = 0.0003  # ~30m at this latitude


def _ensure_dirs() -> dict[str, Path]:
    paths = {
        "root": ROOT,
        "rasters": ROOT / "rasters",
        "vectors": ROOT / "vectors",
        "output": ROOT / "output",
        "data_root": ROOT / "data_root",
        "sis": ROOT / "data_root" / "师姐数据集",
        "fcs_dir": ROOT / "data_root" / "FCS30",
        "dctf_dir": ROOT / "data_root" / "DCTF_18N",
        "input": ROOT / "input" / "24zhejiang1",
        "registry_file": ROOT / "assets_registry.json",
    }
    for key, p in paths.items():
        if key == "registry_file":
            p.parent.mkdir(parents=True, exist_ok=True)
        else:
            p.mkdir(parents=True, exist_ok=True)
    return paths


def _write_shp(gdf: gpd.GeoDataFrame, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(path, encoding="utf-8")
    return str(path)


def _make_poly(offset_lon: float, offset_lat: float, w: float = 0.04, h: float = 0.03) -> Polygon:
    cx, cy = 120.92 + offset_lon, 30.24 + offset_lat
    return box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def _make_bowtie() -> Polygon:
    """自相交脏多边形（拓扑无效）。"""
    return Polygon([(120.90, 30.20), (120.95, 30.25), (120.90, 30.25), (120.95, 30.20)])


def _write_binary_tif(path: Path, fill_ratio: float, nodata_band: bool = False) -> str:
    """生成二值潮滩 TIF，像素值 1=潮滩。"""
    width, height = 80, 60
    transform = from_origin(ORIGIN_LON, ORIGIN_LAT, PIXEL_SIZE_DEG, PIXEL_SIZE_DEG)
    arr = np.zeros((height, width), dtype=np.uint8)
    fh, fw = int(height * fill_ratio), int(width * fill_ratio)
    arr[:fh, :fw] = 1
    if nodata_band:
        arr[fh // 2 :, fw // 2 :] = 0  # 大面积背景

    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "uint8",
        "crs": CRS_WGS84,
        "transform": transform,
        "nodata": 0,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr, 1)
    return str(path)


def _write_assets_registry(paths: dict[str, Path], shp_24: str, tif_index: str) -> str:
    registry = {
        "24zhejiang1_p0.05_c2": {
            "task": "24zhejiang1",
            "prob_threshold": 0.05,
            "min_count": 2,
            "file_path": shp_24,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "file_size_mb": 0.01,
        },
        "24zhejiang1_index": {
            "task": "24zhejiang1",
            "method": "index",
            "prob_threshold": None,
            "min_count": None,
            "file_path": tif_index,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "file_size_mb": 0.05,
        },
    }
    with open(paths["registry_file"], "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)
    return str(paths["registry_file"])


def generate_all() -> dict[str, str]:
    paths = _ensure_dirs()
    assets: dict[str, str] = {}

    # ---- 矢量：基线期 / 当期 / ROI / 参考 ----
    gdf_base = gpd.GeoDataFrame({"id": [1]}, geometry=[_make_poly(0, 0, 0.06, 0.04)], crs=CRS_WGS84)
    gdf_curr = gpd.GeoDataFrame({"id": [1]}, geometry=[_make_poly(0.01, 0.005, 0.05, 0.035)], crs=CRS_WGS84)
    gdf_roi = gpd.GeoDataFrame(
        {"name": ["zhejiang1"], "task": ["zhejiang1"]},
        geometry=[box(*BOUNDS)],
        crs=CRS_WGS84,
    )
    gdf_ref = gpd.GeoDataFrame({"class": ["TF"]}, geometry=[_make_poly(-0.005, -0.005, 0.055, 0.038)], crs=CRS_WGS84)
    gdf_fcs = gpd.GeoDataFrame({"gridcode": [187]}, geometry=[_make_poly(0.008, 0.002, 0.045, 0.032)], crs=CRS_WGS84)
    gdf_dctf = gpd.GeoDataFrame({"class": ["TidalFlats"]}, geometry=[_make_poly(-0.002, 0.003, 0.048, 0.03)], crs=CRS_WGS84)

    assets["baseline_shp"] = _write_shp(gdf_base, paths["output"] / "20zhejiang1" / "20zhejiang1_Final_p0.05_c2.shp")
    assets["current_shp"] = _write_shp(gdf_curr, paths["output"] / "24zhejiang1" / "24zhejiang1_Final_p0.05_c2.shp")
    assets["roi_shp"] = _write_shp(gdf_roi, paths["vectors"] / "roi_zhejiang1.shp")
    assets["ref_2020_shp"] = _write_shp(gdf_ref, paths["sis"] / "china_tidal_flat_projected_2020.shp")
    assets["fcs_shp"] = _write_shp(gdf_fcs, paths["fcs_dir"] / "FCS30_china_2020_flat_clip.shp")
    assets["dctf_shp"] = _write_shp(gdf_dctf, paths["dctf_dir"] / "DCTF_China_2020.shp")

    # UTM 坐标当期（测试 CRS 自动对齐）
    gdf_utm = gdf_curr.to_crs(CRS_UTM)
    assets["current_utm_shp"] = _write_shp(gdf_utm, paths["vectors"] / "current_utm.shp")

    # 自相交脏多边形
    gdf_dirty = gpd.GeoDataFrame({"id": [1]}, geometry=[_make_bowtie()], crs=CRS_WGS84)
    assets["dirty_shp"] = _write_shp(gdf_dirty, paths["vectors"] / "dirty_bowtie.shp")

    # 空交集：完全不相交的多边形
    gdf_far = gpd.GeoDataFrame({"id": [1]}, geometry=[box(125.0, 35.0, 125.1, 35.1)], crs=CRS_WGS84)
    assets["disjoint_shp"] = _write_shp(gdf_far, paths["vectors"] / "disjoint_far.shp")

    # ---- 栅格：2020 / 2024 二值 TIF ----
    assets["tif_2020"] = _write_binary_tif(paths["rasters"] / "test2020_final.tif", fill_ratio=0.55)
    assets["tif_2024"] = _write_binary_tif(paths["rasters"] / "test2024_final.tif", fill_ratio=0.65)
    assets["tif_nodata"] = _write_binary_tif(paths["rasters"] / "test_nodata.tif", fill_ratio=0.4, nodata_band=True)
    assets["tif_index"] = _write_binary_tif(paths["output"] / "24zhejiang1" / "24zhejiang1_Index_Final.tif", fill_ratio=0.5)

    # 模拟推理输入 + mask 成对数据（post_engine 需要 *_mask.tif 且值 >128）
    src_tif = paths["input"] / "scene_001.tif"
    _write_binary_tif(src_tif, fill_ratio=0.3)
    mask_arr_path = paths["input"] / "scene_001_mask.tif"
    with rasterio.open(src_tif) as src:
        prof = src.profile.copy()
        arr = np.full((src.height, src.width), 200, dtype=np.uint8)
        arr[10:30, 10:40] = 0
        with rasterio.open(mask_arr_path, "w", **prof) as dst:
            dst.write(arr, 1)
    _write_binary_tif(paths["input"] / "scene_002.tif", fill_ratio=0.25)
    with rasterio.open(paths["input"] / "scene_002.tif") as src:
        prof = src.profile.copy()
        arr = np.full((src.height, src.width), 220, dtype=np.uint8)
        with rasterio.open(paths["input"] / "scene_002_mask.tif", "w", **prof) as dst:
            dst.write(arr, 1)
    assets["input_dir"] = str(paths["input"])

    assets["registry"] = _write_assets_registry(paths, assets["current_shp"], assets["tif_index"])
    assets["sandbox_root"] = str(ROOT)
    assets["data_root"] = str(paths["data_root"])
    assets["final_root"] = str(paths["output"])

    meta = {"generated_at": datetime.now().isoformat(), "bounds_wgs84": BOUNDS, "assets": assets}
    meta_path = ROOT / "sandbox_manifest.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    assets["manifest"] = str(meta_path)

    print(f"[OK] 沙盒已生成: {ROOT}")
    for k, v in assets.items():
        print(f"  {k}: {v}")
    return assets


if __name__ == "__main__":
    generate_all()
