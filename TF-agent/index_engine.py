"""
指数法潮滩提取：M1(mNDWI 海面) + M2(ACWI 频率) + 空间交集。
供 app.py 后台线程调用，通过 progress/log/stop 回调与 UI 联动。
"""
import glob
import os

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.errors import RasterioError
from rasterio.features import rasterize, shapes
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks
from skimage.measure import label


def _safe_remove(path):
    for p in (path, path + ".ovr"):
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass


def _write_band1(path, data, profile):
    _safe_remove(path)
    prof = profile.copy()
    prof.update(count=1, compress=prof.get("compress") or "lzw")
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(data, 1)


def _remedy_single_band(in_path, out_path):
    with rasterio.open(in_path) as src:
        data = src.read(1)
        profile = src.profile.copy()
    profile.update(count=1)
    _write_band1(out_path, data, profile)
    return out_path


def _m1_pipeline(input_folder, work_dir, points_shp, push_log, push_progress, check_stop, p0, p1):
    os.makedirs(work_dir, exist_ok=True)
    tif_files = glob.glob(os.path.join(input_folder, "*.tif"))
    if not tif_files:
        raise ValueError(f"未找到 TIF: {input_folder}")

    max_mndwi, profile, skipped = None, None, []
    n = len(tif_files)
    for i, file in enumerate(tif_files):
        if check_stop():
            return None
        push_progress(p0 + (p1 - p0) * 0.85 * (i / max(n, 1)))
        push_log(f"[M1] 合成 mNDWI ({i + 1}/{n}): {os.path.basename(file)}")
        try:
            with rasterio.open(file) as src:
                if profile is None:
                    profile = src.profile.copy()
                    profile.update(count=1, dtype=rasterio.float32, nodata=np.nan)
                b3 = src.read(3).astype(np.float32)
                b11 = src.read(5).astype(np.float32)
            den = b3 + b11
            valid = (den != 0) & (~np.isnan(b3)) & (~np.isnan(b11))
            mndwi = np.full(b3.shape, np.nan, dtype=np.float32)
            mndwi[valid] = (b3[valid] - b11[valid]) / den[valid]
            mndwi = np.where((mndwi > -1) & (mndwi < 1), mndwi, np.nan)
            max_mndwi = mndwi if max_mndwi is None else np.fmax(max_mndwi, mndwi)
        except (RasterioError, OSError, ValueError) as e:
            skipped.append((file, repr(e)))
            push_log(f"  |-- 跳过坏图: {e!r}")

    if max_mndwi is None:
        raise ValueError("M1: 无有效影像可读")

    mndwi_path = os.path.join(work_dir, "m1_mndwi_max.tif")
    _write_band1(mndwi_path, max_mndwi, profile)
    push_progress(p0 + (p1 - p0) * 0.88)

    with rasterio.open(mndwi_path) as src:
        mndwi_data = src.read(1)
        prof = src.profile.copy()

    valid_data = mndwi_data[~np.isnan(mndwi_data)]
    counts, bin_edges = np.histogram(valid_data, bins=200, range=(-1, 1))
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    smoothed = gaussian_filter1d(counts, sigma=2)
    derivative = np.gradient(smoothed, bin_centers)
    threshold = 0.0
    gt0 = np.where(bin_centers > 0)[0]
    if len(gt0) > 0:
        peaks, _ = find_peaks(smoothed[gt0])
        if len(peaks) > 0:
            fp = gt0[peaks[0]]
            d_before = derivative[:fp]
            sc = np.where((d_before[:-1] < 0) & (d_before[1:] > 0))[0]
            if len(sc) > 0:
                threshold = bin_centers[sc[-1]]
    push_log(f"[M1] 水体阈值: {threshold:.4f}")

    water_mask = np.zeros(mndwi_data.shape, dtype=np.uint8)
    water_mask[mndwi_data > threshold] = 1
    mask_path = os.path.join(work_dir, "m1_water_mask.tif")
    prof.update(dtype=rasterio.uint8, nodata=None)
    _write_band1(mask_path, water_mask, prof)
    push_progress(p0 + (p1 - p0) * 0.92)

    if check_stop():
        return None
    push_log("[M1] 散点筛选海洋面…")
    seed = gpd.read_file(points_shp)
    with rasterio.open(mask_path) as src:
        wm = src.read(1)
        transform, crs = src.transform, src.crs
    if seed.crs != crs:
        seed = seed.to_crs(crs)

    feats = [
        {"properties": {"class": v}, "geometry": s}
        for s, v in shapes(wm, mask=(wm == 1), transform=transform)
    ]
    gdf_water = gpd.GeoDataFrame.from_features(feats, crs=crs)
    if gdf_water.empty:
        raise ValueError("M1: 未提取到水体矢量")

    selected = gpd.sjoin(gdf_water, seed, how="inner", predicate="intersects")
    if selected.empty:
        push_log("[M1] 警告: 种子点未命中水体，使用全部水体兜底")
        final_vec = gdf_water[["geometry"]]
    else:
        final_vec = selected.drop_duplicates(subset="geometry")[["geometry"]]

    shp_path = os.path.join(work_dir, "m1_ocean_extent.shp")
    final_vec.to_file(shp_path)
    push_progress(p1)
    push_log(f"[M1] 完成: {shp_path}")
    return shp_path


def _m2_pipeline(input_folder, work_dir, push_log, push_progress, check_stop, p0, p1):
    os.makedirs(work_dir, exist_ok=True)
    tif_files = glob.glob(os.path.join(input_folder, "*.tif"))
    sum_land, count_obs, profile, skipped = None, None, None, []
    n = len(tif_files)

    for i, file in enumerate(tif_files):
        if check_stop():
            return None
        push_progress(p0 + (p1 - p0) * (i / max(n, 1)))
        push_log(f"[M2] ACWI ({i + 1}/{n}): {os.path.basename(file)}")
        try:
            with rasterio.open(file) as src:
                if profile is None:
                    profile = src.profile.copy()
                    sum_land = np.zeros((src.height, src.width), np.float32)
                    count_obs = np.zeros((src.height, src.width), np.float32)
                b8 = src.read(1).astype(np.float32)
                b4 = src.read(2).astype(np.float32)
                b3 = src.read(3).astype(np.float32)
                b2 = src.read(4).astype(np.float32)
                b11 = src.read(5).astype(np.float32)

            wt_num = 3 * b4 - b11 - b8 - b2 - b3
            wt_den = 5 * b8 * (np.abs(b8 - b4) + np.abs(b8 - b11))
            valid_mask = (wt_den != 0) & (~np.isnan(b8))
            wtftfi = np.full(b8.shape, np.nan, dtype=np.float32)
            wtftfi[valid_mask] = wt_num[valid_mask] / wt_den[valid_mask]
            wtftfi_valid = np.where((wtftfi > -10) & (wtftfi < 10), wtftfi, np.nan)
            valid_tfi = wtftfi_valid[~np.isnan(wtftfi_valid)]
            if len(valid_tfi) == 0:
                continue

            tfi_counts, tfi_bins = np.histogram(valid_tfi, bins=200, range=(-10, 10))
            tfi_centers = (tfi_bins[:-1] + tfi_bins[1:]) / 2
            tfi_smoothed = gaussian_filter1d(tfi_counts, sigma=2)
            max_tfi = np.nanmax(wtftfi_valid)
            if max_tfi < 0:
                tfi_threshold = 0.0
            else:
                peaks, _ = find_peaks(tfi_smoothed)
                gt0_peaks = [p for p in peaks if tfi_centers[p] >= 0]
                lt0_peaks = [p for p in peaks if tfi_centers[p] < 0]
                gt0_idx = gt0_peaks[0] if gt0_peaks else np.where(tfi_centers >= 0)[0][0]
                lt0_idx = lt0_peaks[-1] if lt0_peaks else np.where(tfi_centers < 0)[0][-1]
                if lt0_idx < gt0_idx:
                    valley = np.argmin(tfi_smoothed[lt0_idx : gt0_idx + 1])
                    tfi_threshold = tfi_centers[lt0_idx + valley]
                else:
                    tfi_threshold = 0.0

            swir_adaptive = np.where(wtftfi_valid > tfi_threshold, b11, b8)
            acwi_den = swir_adaptive + b3
            acwi = np.full(b8.shape, np.nan, dtype=np.float32)
            acwi_mask = (acwi_den != 0) & (~np.isnan(swir_adaptive))
            acwi[acwi_mask] = (swir_adaptive[acwi_mask] - b3[acwi_mask]) / acwi_den[acwi_mask]
            acwi = np.where((acwi > -1) & (acwi < 1), acwi, np.nan)
            valid_acwi = acwi[~np.isnan(acwi)]
            if len(valid_acwi) == 0:
                continue

            acwi_counts, acwi_bins = np.histogram(valid_acwi, bins=200, range=(-1, 1))
            acwi_centers = (acwi_bins[:-1] + acwi_bins[1:]) / 2
            acwi_smoothed = gaussian_filter1d(acwi_counts, sigma=2)
            derivative = np.gradient(acwi_smoothed, acwi_centers)
            gt_zero_idx = np.where(acwi_centers >= 0)[0]
            acwi_threshold = 0.0
            if len(gt_zero_idx) > 0:
                peaks, _ = find_peaks(acwi_smoothed[gt_zero_idx])
                if len(peaks) > 0:
                    fp = gt_zero_idx[peaks[0]]
                    d_before = derivative[:fp]
                    sc = np.where((d_before[:-1] < 0) & (d_before[1:] > 0))[0]
                    if len(sc) > 0:
                        acwi_threshold = acwi_centers[sc[-1]]

            land_mask = np.zeros(acwi.shape, dtype=np.float32)
            land_mask[acwi >= acwi_threshold] = 1.0
            sum_land[valid_mask] += land_mask[valid_mask]
            count_obs[valid_mask] += 1.0
        except (RasterioError, OSError, ValueError) as e:
            skipped.append((file, repr(e)))
            push_log(f"  |-- 跳过坏图: {e!r}")

    if sum_land is None:
        raise ValueError("M2: 无有效影像")

    probability = np.zeros(sum_land.shape, dtype=np.float32)
    valid_pixels = count_obs > 0
    probability[valid_pixels] = (sum_land[valid_pixels] / count_obs[valid_pixels]) * 100.0

    binary = (probability > 3).astype(np.uint8)
    labeled = label(binary, connectivity=2)
    unique, counts = np.unique(labeled, return_counts=True)
    small_labels = unique[counts <= 100]
    probability[probability <= 5] = 0
    probability[np.isin(labeled, small_labels)] = 0
    probability_uint8 = np.clip(np.round(probability), 0, 100).astype(np.uint8)

    prob_path = os.path.join(work_dir, "m2_probability.tif")
    profile.update(count=1, dtype=rasterio.uint8, nodata=0, compress="lzw")
    _write_band1(prob_path, probability_uint8, profile)
    fixed_path = os.path.join(work_dir, "m2_probability_fixed.tif")
    _remedy_single_band(prob_path, fixed_path)
    push_progress(p1)
    push_log(f"[M2] 完成: {fixed_path}")
    return fixed_path


def _fuse_and_rasterize(m1_shp, m2_tif, out_tif, push_log, push_progress, check_stop, p0, p1):
    if check_stop():
        return None
    push_log("[融合] M1 海面 ∩ M2 暴露频率 → 潮滩")
    gdf_m1 = gpd.read_file(m1_shp)
    if gdf_m1.empty:
        raise ValueError("M1 矢量为空")

    with rasterio.open(m2_tif) as src:
        prob_data = src.read(1)
        transform, crs, shape = src.transform, src.crs, (src.height, src.width)
        profile = src.profile.copy()

    mask = prob_data > 0
    feats = [
        {"properties": {"prob_val": int(v)}, "geometry": s}
        for s, v in shapes(prob_data, mask=mask, transform=transform)
    ]
    gdf_m2 = gpd.GeoDataFrame.from_features(feats, crs=crs)
    if gdf_m2.empty:
        raise ValueError("M2 无概率>0 区域")

    if gdf_m1.crs != gdf_m2.crs:
        gdf_m2 = gdf_m2.to_crs(gdf_m1.crs)

    push_progress(p0 + (p1 - p0) * 0.5)
    tidal = gpd.overlay(gdf_m1, gdf_m2, how="intersection")
    if tidal.empty:
        raise ValueError("M1 与 M2 空间交集为空，请检查范围与种子点")

    tidal = tidal.dissolve()
    shp_out = os.path.join(os.path.dirname(out_tif), "Final_Intertidal_Flat.shp")
    tidal.to_file(shp_out)
    push_log(f"[融合] 矢量已保存: {shp_out}")

    if check_stop():
        return None
    geoms = [(g, 1) for g in tidal.geometry if g is not None and not g.is_empty]
    flat_mask = rasterize(geoms, out_shape=shape, transform=transform, fill=0, dtype=np.uint8)
    display = np.where(flat_mask > 0, prob_data, 0).astype(np.uint8)

    profile.update(count=1, dtype=rasterio.uint8, nodata=0, compress="lzw")
    _write_band1(out_tif, display, profile)
    push_progress(p1)
    push_log(f"[融合] 地图展示栅格: {out_tif}")
    return out_tif


def run_index_pipeline(
    input_dir,
    output_tif,
    points_shp,
    work_dir=None,
    push_log=print,
    push_progress=None,
    stop_callback=None,
):
    """
    完整指数法流水线。返回 output_tif 路径；失败抛异常或 stop 时返回 None。
    """
    if work_dir is None:
        work_dir = os.path.join(os.path.dirname(output_tif), "index_work")
    os.makedirs(work_dir, exist_ok=True)

    def _prog(pct):
        if push_progress:
            push_progress(int(min(100, max(0, pct))))

    def _stop():
        return bool(stop_callback and stop_callback())

    if not os.path.isdir(input_dir):
        raise FileNotFoundError(f"输入目录不存在: {input_dir}")
    if not os.path.isfile(points_shp):
        raise FileNotFoundError(f"海洋种子点不存在: {points_shp}")

    if os.path.isfile(output_tif) and os.path.getsize(output_tif) > 0:
        push_log(f"⚡ 指数法结果已存在，跳过计算: {output_tif}")
        _prog(100)
        return output_tif

    _prog(2)
    push_log(">>> [指数法 Phase 1/3] M1: mNDWI 最大海面")
    m1_shp = _m1_pipeline(input_dir, os.path.join(work_dir, "m1"), points_shp, push_log, _prog, _stop, 2, 38)
    if m1_shp is None:
        return None

    _prog(40)
    push_log(">>> [指数法 Phase 2/3] M2: ACWI 暴露频率")
    m2_tif = _m2_pipeline(input_dir, os.path.join(work_dir, "m2"), push_log, _prog, _stop, 40, 82)
    if m2_tif is None:
        return None

    _prog(84)
    push_log(">>> [指数法 Phase 3/3] 空间融合与栅格化")
    result = _fuse_and_rasterize(m1_shp, m2_tif, output_tif, push_log, _prog, _stop, 84, 100)
    return result
