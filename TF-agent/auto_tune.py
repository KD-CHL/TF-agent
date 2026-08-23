"""
自适应合成参数优化引擎。

在已有的 NUMERATOR/DENOMINATOR 累加缓存上，网格搜索 (prob_threshold, min_count)
使潮滩合成图与参考真值（师姐 SHP 等）的 IoU / F1 最优。

典型调用（被 app.py 后台线程驱动）：
    from auto_tune import run_adaptive_tuning
    result = run_adaptive_tuning(
        source_folder=..., mask_folder=..., final_out_dir=...,
        task_name="20zhejiang1", reference_shp_path="...shp",
    )
"""

import glob
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from agent_context_policy import safe_error_summary

# 超过该像元数则不对 NUM/DEN 与参考真值做整幅驻内存（避免 guangdong4 等级别 OOM）；分块峰值约 TILE² 量级。
_MAX_PIXELS_FULL_RAM = 180_000_000
_TILE_AUTOTUNE = 4096
# 分块路径下，每个 cnt 对 prob 轴分批向量化，避免每块 500 次全图 count_nonzero（极慢）
_PROB_CHUNK = 20


def _find_accumulation_cache(final_out_dir: str) -> Tuple[Optional[str], Optional[str]]:
    """在 output 目录中搜索任何已存在的 NUMERATOR/DENOMINATOR 缓存对。"""
    candidates = sorted(glob.glob(os.path.join(final_out_dir, "*_NUMERATOR.tif")))
    for num in candidates:
        den = num.replace("_NUMERATOR.tif", "_DENOMINATOR.tif")
        if os.path.isfile(den):
            return num, den
    return None, None


def _build_accumulation_cache(
    source_folder: str,
    mask_folder: str,
    final_out_dir: str,
    task_name: str,
    shp_clip_path: Optional[str],
    logger: Callable,
    stop_callback: Optional[Callable],
) -> Tuple[Optional[str], Optional[str]]:
    """调用 post_engine 生成一次输出，附带产出 NUMERATOR/DENOMINATOR 缓存。"""
    import post_engine

    dummy_out = os.path.join(final_out_dir, f"{task_name}_Final_autotune_seed.shp")
    os.makedirs(final_out_dir, exist_ok=True)
    ok = post_engine.generate_double_constraint_complete(
        source_folder,
        mask_folder,
        dummy_out,
        shp_clip_path,
        prob_threshold=0.05,
        min_absolute_count=2,
        logger=logger,
        stop_callback=stop_callback,
    )
    num = os.path.join(final_out_dir, f"{task_name}_Final_autotune_seed_NUMERATOR.tif")
    den = os.path.join(final_out_dir, f"{task_name}_Final_autotune_seed_DENOMINATOR.tif")
    seed_shp = dummy_out
    if os.path.isfile(seed_shp):
        try:
            os.remove(seed_shp)
        except OSError:
            pass
    if ok and os.path.isfile(num) and os.path.isfile(den):
        return num, den
    for _p in (num, den):
        if os.path.isfile(_p):
            try:
                os.remove(_p)
            except OSError:
                pass
    return None, None


def _ensure_accumulation_cache(
    source_folder: str,
    mask_folder: str,
    final_out_dir: str,
    task_name: str,
    shp_clip_path: Optional[str],
    logger: Callable,
    stop_callback: Optional[Callable],
) -> Tuple[Optional[str], Optional[str]]:
    num, den = _find_accumulation_cache(final_out_dir)
    if num:
        logger(f"⚡ 复用现有累加缓存: {os.path.basename(num)}")
        return num, den
    logger("🐢 累加缓存不存在，构建中…")
    return _build_accumulation_cache(
        source_folder, mask_folder, final_out_dir, task_name,
        shp_clip_path, logger, stop_callback,
    )


def _load_reference_gdf(
    shp_path: str,
    crs,
    logger: Callable,
    aoi_shp_path: Optional[str] = None,
    task_name: Optional[str] = None,
):
    """读取并重投影参考真值 SHP；可选按任务 AOI 裁剪。"""
    import geopandas as gpd

    gdf = gpd.read_file(shp_path)
    if gdf.crs != crs:
        logger(f"  → 坐标系重投影: {gdf.crs} → {crs}")
        gdf = gdf.to_crs(crs)
    if aoi_shp_path and task_name:
        try:
            from evaluation_geo import clip_truth_to_task_aoi

            gdf = clip_truth_to_task_aoi(gdf, aoi_shp_path, task_name, logger=logger)
        except Exception as e:
            logger(f"  ⚠️ 任务 AOI 裁剪失败，使用未裁剪真值: {safe_error_summary(e)}")
    return gdf


def _rasterize_reference(
    shp_path: str,
    out_shape: tuple,
    transform,
    crs,
    logger: Callable,
    aoi_shp_path: Optional[str] = None,
    task_name: Optional[str] = None,
) -> np.ndarray:
    """将参考真值 SHP 栅格化到与 NUMERATOR 相同的网格；可选按任务 AOI 先裁剪师姐真值。"""
    from rasterio.features import rasterize as rio_rasterize

    logger(f"📐 栅格化参考真值: {os.path.basename(shp_path)} ({out_shape[0]}×{out_shape[1]}) …")
    gdf = _load_reference_gdf(shp_path, crs, logger, aoi_shp_path, task_name)
    truth = rio_rasterize(
        ((geom, 1) for geom in gdf.geometry),
        out_shape=out_shape,
        transform=transform,
        fill=0,
        dtype=np.uint8,
    )
    n = int(np.count_nonzero(truth))
    logger(f"  → 参考真值有效像元: {n:,} / {truth.size:,}")
    return truth


def _build_clip_mask(
    shp_path: Optional[str], out_shape: tuple, transform, crs, logger: Callable,
) -> Optional[np.ndarray]:
    """构建岸线裁剪掩膜（True = 被遮蔽区域）。无岸线 SHP 时返回 None。"""
    if not shp_path or not os.path.isfile(shp_path):
        return None
    import geopandas as gpd
    from rasterio.features import geometry_mask

    logger(f"✂️ 加载岸线裁剪: {os.path.basename(shp_path)} …")
    gdf = gpd.read_file(shp_path)
    if gdf.crs != crs:
        gdf = gdf.to_crs(crs)
    return geometry_mask(gdf.geometry, out_shape=out_shape, transform=transform)


# ------------------------------------------------------------------
# 主入口
# ------------------------------------------------------------------

def run_adaptive_tuning(
    source_folder: str,
    mask_folder: str,
    final_out_dir: str,
    task_name: str,
    reference_shp_path: str,
    shp_clip_path: Optional[str] = None,
    task_aoi_shp_path: Optional[str] = None,
    prob_range: Tuple[float, float] = (0.01, 0.50),
    prob_step: float = 0.01,
    cnt_range: Tuple[int, int] = (1, 10),
    objective: str = "iou_f1",
    logger: Callable = print,
    progress_callback: Optional[Callable[[int], None]] = None,
    stop_callback: Optional[Callable[[], bool]] = None,
) -> Optional[Dict[str, Any]]:
    """
    网格搜索 (prob_threshold × min_count)，返回最优参数与指标。

    Parameters
    ----------
    objective : "iou_f1" | "iou" | "f1"
    progress_callback : 接收 0–100 整数

    Returns
    -------
    dict  包含 best_prob, best_cnt, best_iou, best_f1, best_shp_path, trials, …
    None  失败或被中断时
    """
    t0 = time.time()

    def _prog(v: int):
        if progress_callback:
            progress_callback(v)

    # ── 1. 累加缓存 ──────────────────────────────────────
    _prog(82)
    num_path, den_path = _ensure_accumulation_cache(
        source_folder, mask_folder, final_out_dir, task_name,
        shp_clip_path, logger, stop_callback,
    )
    if stop_callback and stop_callback():
        logger("🚨 已中断，终止自适应优化。")
        return None
    if not num_path:
        logger("❌ 无法获取累加缓存，终止。")
        return None

    # ── 2–5. 网格搜索（小图整幅驻内存；超大图分块累加 tp/fp，避免 OOM）────────────────
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.features import rasterize as rio_rasterize
    from rasterio.windows import Window
    from rasterio.windows import transform as window_transform

    with rasterio.open(num_path) as src:
        meta = src.meta.copy()
        out_transform = src.transform
        out_crs = src.crs
        full_h, full_w = int(src.height), int(src.width)

    out_shape = (full_h, full_w)
    n_pix = full_h * full_w
    use_tiled = n_pix > _MAX_PIXELS_FULL_RAM

    prob_vals = np.arange(prob_range[0], prob_range[1] + prob_step * 0.5, prob_step)
    cnt_vals = list(range(cnt_range[0], cnt_range[1] + 1))
    total = len(prob_vals) * len(cnt_vals)
    logger(
        f"🔍 网格搜索: prob [{prob_range[0]:.2f}, {prob_range[1]:.2f}] step {prob_step} "
        f"× cnt [{cnt_range[0]}, {cnt_range[1]}] = {total} 组合"
    )

    trials: List[Dict[str, Any]] = []
    best: Dict[str, Any] = {"score": -1.0}
    done = 0

    def _score_from_counts(tp: int, fp: int, fn: int) -> Tuple[float, float, float, float, float]:
        denom_iou = tp + fp + fn
        iou = tp / denom_iou if denom_iou else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        if objective == "iou":
            score = iou
        elif objective == "f1":
            score = f1
        else:
            score = 0.5 * iou + 0.5 * f1
        return iou, f1, prec, rec, score

    if not use_tiled:
        logger("📊 加载累加缓存到内存（整幅）…")
        with rasterio.open(num_path) as src:
            E = src.read(1).astype(np.float32)
        with rasterio.open(den_path) as src:
            O = src.read(1).astype(np.float32)
        logger("🔢 计算概率矩阵…")
        probability = np.divide(E, O, out=np.zeros_like(E), where=O != 0)
        E_u16 = E.astype(np.uint16)

        _prog(84)
        truth = _rasterize_reference(
            reference_shp_path,
            out_shape,
            out_transform,
            out_crs,
            logger,
            aoi_shp_path=task_aoi_shp_path,
            task_name=task_name,
        )
        truth_bool = truth > 0
        not_truth = ~truth_bool
        truth_total = int(np.count_nonzero(truth_bool))
        if truth_total == 0:
            logger("⚠️ 参考真值在预测范围内无有效像元（可能区域不重叠），终止。")
            return None

        clip_mask = _build_clip_mask(shp_clip_path, out_shape, out_transform, out_crs, logger)

        for c in cnt_vals:
            if stop_callback and stop_callback():
                logger("🚨 中断。")
                return None

            cnt_mask = E_u16 >= c
            if clip_mask is not None:
                cnt_base = cnt_mask & (~clip_mask)
            else:
                cnt_base = cnt_mask

            best_in_c: Dict[str, Any] = {"score": -1.0}

            for p in prob_vals:
                pred = cnt_base & (probability >= p)
                tp = int(np.count_nonzero(pred & truth_bool))
                fp = int(np.count_nonzero(pred & not_truth))
                fn = truth_total - tp
                iou, f1, prec, rec, score = _score_from_counts(tp, fp, fn)

                trial = dict(
                    prob=round(float(p), 4),
                    cnt=c,
                    iou=round(iou, 6),
                    f1=round(f1, 6),
                    precision=round(prec, 6),
                    recall=round(rec, 6),
                    score=round(score, 6),
                    tp=tp,
                    fp=fp,
                    fn=fn,
                )
                trials.append(trial)
                if score > best_in_c["score"]:
                    best_in_c = trial.copy()
                if score > best["score"]:
                    best = trial.copy()
                done += 1

            pct = 85 + int(10 * done / total)
            _prog(pct)
            logger(
                f"  cnt={c} 完成 ({done}/{total}) | "
                f"本轮最优 p={best_in_c['prob']} IoU={best_in_c['iou']*100:.2f}% F1={best_in_c['f1']*100:.2f}% | "
                f"全局最优 p={best['prob']} c={best['cnt']} IoU={best['iou']*100:.2f}% F1={best['f1']*100:.2f}%"
            )
    else:
        logger(
            f"📊 栅格约 {full_h}×{full_w}（{n_pix:,} 像元），分块搜索（块 {_TILE_AUTOTUNE}，避免整幅 float32 驻内存）…"
        )
        _prog(84)
        gdf_ref = _load_reference_gdf(
            reference_shp_path, out_crs, logger,
            aoi_shp_path=task_aoi_shp_path, task_name=task_name,
        )
        gdf_coast = None
        if shp_clip_path and os.path.isfile(shp_clip_path):
            import geopandas as gpd

            logger(f"✂️ 岸线裁剪（分块）: {os.path.basename(shp_clip_path)} …")
            gdf_coast = gpd.read_file(shp_clip_path)
            if gdf_coast.crs != out_crs:
                gdf_coast = gdf_coast.to_crs(out_crs)
            logger("   → 岸线几何已载入；随后进入分块栅格扫描（最耗时，进度条在 84%–92% 间缓慢移动）")
        else:
            logger("   → 未配置岸线 SHP；直接进入分块栅格扫描（最耗时）…")

        n_c, n_p = len(cnt_vals), len(prob_vals)
        tp_acc = np.zeros((n_c, n_p), dtype=np.int64)
        fp_acc = np.zeros((n_c, n_p), dtype=np.int64)
        truth_total = 0
        tile = _TILE_AUTOTUNE
        n_ty = (full_h + tile - 1) // tile
        n_tx = (full_w + tile - 1) // tile
        total_tiles = n_ty * n_tx
        log_every = max(1, total_tiles // 30)
        logger(
            f"   → 共 {n_ty}×{n_tx}={total_tiles} 块；每块：栅格化真值 + 岸线 + "
            f"按 cnt×prob 批量向量化（prob 每批 {_PROB_CHUNK}，约 {n_c * ((n_p + _PROB_CHUNK - 1) // _PROB_CHUNK)} 批/块）"
        )

        prob_vals_arr = np.asarray(prob_vals, dtype=np.float32)
        tile_idx = 0
        with rasterio.open(num_path) as src_num, rasterio.open(den_path) as src_den:
            for row_off in range(0, full_h, tile):
                if stop_callback and stop_callback():
                    logger("🚨 中断。")
                    return None
                for col_off in range(0, full_w, tile):
                    th = min(tile, full_h - row_off)
                    tw = min(tile, full_w - col_off)
                    win = Window(col_off, row_off, tw, th)
                    aft = window_transform(win, out_transform)

                    cur = tile_idx + 1
                    if cur <= 2 or cur == total_tiles:
                        logger(f"   … 块 {cur}/{total_tiles} ({row_off},{col_off})：参考 SHP 栅格化 {th}×{tw} …")

                    truth = rio_rasterize(
                        ((geom, 1) for geom in gdf_ref.geometry),
                        out_shape=(th, tw),
                        transform=aft,
                        fill=0,
                        dtype=np.uint8,
                    )
                    truth_total += int(np.count_nonzero(truth))
                    truth_b = truth > 0
                    not_truth_b = ~truth_b

                    if cur <= 2:
                        logger(f"   … 块 {cur}/{total_tiles}：读取 NUM/DEN、算概率 …")

                    E = src_num.read(1, window=win)
                    O = src_den.read(1, window=win)
                    E_uint = E.astype(np.uint16)
                    Ef = E_uint.astype(np.float32)
                    Of = O.astype(np.float32)
                    prob = np.divide(Ef, Of, out=np.zeros_like(Ef), where=Of != 0)

                    if gdf_coast is not None:
                        if cur <= 2:
                            logger(f"   … 块 {cur}/{total_tiles}：岸线 geometry_mask …")
                        clip_tile = geometry_mask(
                            gdf_coast.geometry, out_shape=(th, tw), transform=aft
                        )
                    else:
                        clip_tile = None

                    if cur <= 2:
                        logger(f"   … 块 {cur}/{total_tiles}：cnt×prob 批量累加 …")

                    for ic, c in enumerate(cnt_vals):
                        cnt_mask = E_uint >= c
                        if clip_tile is not None:
                            cnt_base = cnt_mask & (~clip_tile)
                        else:
                            cnt_base = cnt_mask
                        cnt_3 = cnt_base[:, :, np.newaxis]

                        for p0 in range(0, n_p, _PROB_CHUNK):
                            p1 = min(n_p, p0 + _PROB_CHUNK)
                            pv = prob_vals_arr[p0:p1]
                            ge = prob[:, :, np.newaxis] >= pv
                            pred_c = cnt_3 & ge
                            t3 = truth_b[:, :, np.newaxis]
                            nt3 = not_truth_b[:, :, np.newaxis]
                            tp_acc[ic, p0:p1] += np.sum(pred_c & t3, axis=(0, 1)).astype(np.int64)
                            fp_acc[ic, p0:p1] += np.sum(pred_c & nt3, axis=(0, 1)).astype(np.int64)

                    tile_idx += 1
                    if tile_idx == 1 or tile_idx % log_every == 0 or tile_idx == total_tiles:
                        pct_tile = 100.0 * tile_idx / max(total_tiles, 1)
                        logger(
                            f"   … 分块栅格扫描 {tile_idx}/{total_tiles} "
                            f"({pct_tile:.1f}%) | 进度条≈分块阶段"
                        )
                        _prog(84 + int(8 * tile_idx / max(total_tiles, 1)))

        if truth_total == 0:
            logger("⚠️ 参考真值在预测范围内无有效像元（可能区域不重叠），终止。")
            return None

        logger("   → 分块累加完成，正在汇总 IoU/F1 网格（较快）…")
        _prog(92)

        for ic, c in enumerate(cnt_vals):
            if stop_callback and stop_callback():
                logger("🚨 中断。")
                return None
            best_in_c: Dict[str, Any] = {"score": -1.0}
            for ip, p in enumerate(prob_vals):
                tp = int(tp_acc[ic, ip])
                fp = int(fp_acc[ic, ip])
                fn = truth_total - tp
                iou, f1, prec, rec, score = _score_from_counts(tp, fp, fn)
                trial = dict(
                    prob=round(float(p), 4),
                    cnt=c,
                    iou=round(iou, 6),
                    f1=round(f1, 6),
                    precision=round(prec, 6),
                    recall=round(rec, 6),
                    score=round(score, 6),
                    tp=tp,
                    fp=fp,
                    fn=fn,
                )
                trials.append(trial)
                if score > best_in_c["score"]:
                    best_in_c = trial.copy()
                if score > best["score"]:
                    best = trial.copy()
                done += 1

            pct = 92 + int(6 * done / max(total, 1))
            _prog(min(98, pct))
            logger(
                f"  cnt={c} 完成 ({done}/{total}) | "
                f"本轮最优 p={best_in_c['prob']} IoU={best_in_c['iou']*100:.2f}% F1={best_in_c['f1']*100:.2f}% | "
                f"全局最优 p={best['prob']} c={best['cnt']} IoU={best['iou']*100:.2f}% F1={best['f1']*100:.2f}%"
            )

    # ── 6. 排行榜 + 生成最优 TIF ────────────────────────
    sorted_trials = sorted(trials, key=lambda t: t["score"], reverse=True)
    top_n = min(10, len(sorted_trials))
    logger(f"\n📊 Top-{top_n} 参数组合排行榜:")
    logger(f"  {'排名':>4}  {'prob':>6}  {'cnt':>4}  {'IoU%':>8}  {'F1%':>8}  {'Prec%':>8}  {'Rec%':>8}")
    for rank, t in enumerate(sorted_trials[:top_n], 1):
        marker = " 🏆" if rank == 1 else ""
        logger(
            f"  {rank:>4}  {t['prob']:>6.2f}  {t['cnt']:>4}  "
            f"{t['iou']*100:>7.2f}  {t['f1']*100:>7.2f}  "
            f"{t['precision']*100:>7.2f}  {t['recall']*100:>7.2f}{marker}"
        )

    bp, bc = best["prob"], best["cnt"]
    logger(f"\n🏆 最优参数: prob={bp}, cnt={bc}")
    logger(
        f"   IoU={best['iou']*100:.2f}%  F1={best['f1']*100:.2f}%  "
        f"Precision={best['precision']*100:.2f}%  Recall={best['recall']*100:.2f}%"
    )

    best_shp_name = f"{task_name}_Final_p{bp:.2f}_c{bc}.shp"
    best_shp_path = os.path.join(final_out_dir, best_shp_name)
    work_tif_path = os.path.join(final_out_dir, f"{task_name}_Final_p{bp:.2f}_c{bc}_work.tif")

    logger(f"💾 写入最优潮滩矢量: {best_shp_name}")
    out_meta = meta.copy()
    out_meta.update(dtype="uint8", compress="lzw")

    if not use_tiled:
        final_mask = (probability >= bp) & (E_u16 >= bc)
        if clip_mask is not None:
            final_mask &= ~clip_mask
        final_u8 = final_mask.astype(np.uint8) * 255
        with rasterio.open(work_tif_path, "w", **out_meta) as dst:
            dst.write(final_u8, 1)
    else:
        w_ty = (full_h + _TILE_AUTOTUNE - 1) // _TILE_AUTOTUNE
        w_tx = (full_w + _TILE_AUTOTUNE - 1) // _TILE_AUTOTUNE
        w_total = w_ty * w_tx
        w_i = 0
        with rasterio.open(num_path) as src_num, rasterio.open(den_path) as src_den, rasterio.open(
            work_tif_path, "w", **out_meta
        ) as dst:
            for row_off in range(0, full_h, _TILE_AUTOTUNE):
                if stop_callback and stop_callback():
                    logger("🚨 写入最优图时中断。")
                    for _p in (work_tif_path, best_shp_path):
                        if os.path.isfile(_p):
                            try:
                                os.remove(_p)
                            except OSError:
                                pass
                    return None
                for col_off in range(0, full_w, _TILE_AUTOTUNE):
                    th = min(_TILE_AUTOTUNE, full_h - row_off)
                    tw = min(_TILE_AUTOTUNE, full_w - col_off)
                    win = Window(col_off, row_off, tw, th)
                    aft = window_transform(win, out_transform)
                    E = src_num.read(1, window=win).astype(np.uint16)
                    O = src_den.read(1, window=win).astype(np.float32)
                    prob = np.divide(
                        E.astype(np.float32), O, out=np.zeros((th, tw), dtype=np.float32), where=O != 0
                    )
                    out = ((prob >= bp) & (E >= bc)).astype(np.uint8) * 255
                    if gdf_coast is not None:
                        cmask = geometry_mask(gdf_coast.geometry, out_shape=(th, tw), transform=aft)
                        out[cmask] = 0
                    dst.write(out, 1, window=win)
                    w_i += 1
                    if w_i == 1 or w_i == w_total or w_i % max(1, w_total // 20) == 0:
                        _prog(98 + int(2 * w_i / max(w_total, 1)))
                        logger(f"   … 写入临时栅格 {w_i}/{w_total} ({100.0 * w_i / max(w_total, 1):.1f}%)")

    from post_engine import raster_tidal_flat_to_shp

    if not raster_tidal_flat_to_shp(work_tif_path, best_shp_path, tidal_value=255, logger=logger):
        try:
            if os.path.isfile(work_tif_path):
                os.remove(work_tif_path)
        except OSError:
            pass
        return None
    try:
        os.remove(work_tif_path)
    except OSError:
        logger(f"   ⚠️ 未能删除临时栅格: {work_tif_path}")

    _prog(100)
    elapsed = time.time() - t0
    logger(f"✅ 自适应优化完成！耗时 {elapsed:.1f}s")

    return dict(
        best_prob=bp,
        best_cnt=bc,
        best_iou=best["iou"],
        best_f1=best["f1"],
        best_precision=best["precision"],
        best_recall=best["recall"],
        best_score=best["score"],
        best_shp_path=best_shp_path,
        best_tif_path=best_shp_path,
        trials=trials,
        total_trials=total,
        total_time_sec=round(elapsed, 1),
    )
