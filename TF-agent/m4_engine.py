"""
M4 GEE Sentinel-2 筛选与导出（由 jb/M4.py 移植，供 Streamlit 后台线程调用）。
"""
import contextlib
import json
import os
import re
from datetime import datetime, timedelta

import ee
import geemap
import geopandas as gpd

_EE_INIT_KEY = None
_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy",
)


def _resolve_ee_project(override=None):
    if override and str(override).strip():
        return str(override).strip()
    for key in ("EE_PROJECT", "GOOGLE_CLOUD_PROJECT", "EARTHENGINE_PROJECT"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    cfg_dir = os.path.join(os.path.expanduser("~"), ".config", "earthengine")
    for fname in ("project", "project_id"):
        p = os.path.join(cfg_dir, fname)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    text = f.read().strip()
                if text:
                    return text
            except OSError:
                pass
    cred_path = os.path.join(cfg_dir, "credentials")
    if os.path.isfile(cred_path):
        try:
            with open(cred_path, encoding="utf-8") as f:
                data = json.load(f)
            for k in ("project", "project_id", "cloud_project"):
                if data.get(k):
                    return str(data[k]).strip()
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return None


@contextlib.contextmanager
def gee_network_context(gee_proxy_url=None, push_log=None):
    """
    控制 GEE / Google OAuth 使用的网络环境。
    - gee_proxy_url 为空：临时移除系统 HTTP 代理（避免失效的 127.0.0.1:7890），适合 VPN 全局/TUN。
    - gee_proxy_url 非空：仅本次 GEE 调用使用该代理（如 http://127.0.0.1:7892）。
    """
    saved = {k: os.environ.get(k) for k in _PROXY_ENV_KEYS}
    url = (gee_proxy_url or "").strip()
    if push_log and not url:
        for k in _PROXY_ENV_KEYS:
            v = saved.get(k)
            if v and re.search(r"127\.0\.0\.1|localhost", str(v), re.I):
                push_log(f"[M4] 已忽略失效环境代理 {k}={v}，GEE 将直连（请确保 VPN 已开）")
                break
    try:
        for k in _PROXY_ENV_KEYS:
            os.environ.pop(k, None)
        if url:
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                os.environ[k] = url
            if push_log:
                push_log(f"[M4] GEE 使用显式代理: {url}")
        yield
    finally:
        for k in _PROXY_ENV_KEYS:
            if saved.get(k) is not None:
                os.environ[k] = saved[k]
            elif k in os.environ:
                os.environ.pop(k, None)


def ensure_ee_initialized(gee_proxy_url=None, gee_project_id=None, push_log=None):
    global _EE_INIT_KEY
    project = _resolve_ee_project(gee_project_id)
    init_key = (gee_proxy_url or "", project or "")
    if _EE_INIT_KEY == init_key:
        return
    with gee_network_context(gee_proxy_url, push_log=push_log):
        try:
            if project:
                if push_log:
                    push_log(f"[M4] ee.Initialize(project={project})")
                ee.Initialize(project=project)
            else:
                ee.Initialize()
        except Exception as init_err:
            msg = str(init_err).lower()
            if "no project found" in msg:
                hint = (
                    "新版 Earth Engine 必须指定 Cloud Project。"
                    "请在侧栏填写「GEE Cloud Project」，或在终端执行：\n"
                    "  earthengine set_project 你的项目ID\n"
                    "项目 ID 可在 https://code.earthengine.google.com 登录后右上角看到。"
                )
            elif "proxy" in msg or "127.0.0.1" in msg:
                hint = "请在侧栏「GEE 网络代理」填写 http://127.0.0.1:7892"
            elif "timeout" in msg or "timed out" in msg:
                hint = (
                    "连接 Google 超时：请在侧栏「GEE 网络代理」填写 http://127.0.0.1:7892，"
                    "并确认 Clash 系统代理已开启。"
                )
            else:
                hint = "请在终端执行: earthengine authenticate（需 VPN/代理）"
            raise RuntimeError(f"GEE 初始化失败：{hint}\n原始错误: {init_err}") from init_err
    _EE_INIT_KEY = init_key


def list_roi_names(roi_path: str):
    if not roi_path or not os.path.isfile(roi_path):
        return []
    gdf = gpd.read_file(roi_path)
    if "name" not in gdf.columns:
        return []
    return sorted(gdf["name"].astype(str).unique().tolist())


def _gee_error_hint(exc: Exception) -> str:
    msg = str(exc).lower()
    if any(k in msg for k in ("computation timed out", "timeout", "timed out", "deadline exceeded")):
        return (
            "云端计算超时：日期跨度较大或候选影像过多。"
            "请缩短日期范围、提高云量阈值，或缩小 ROI 后重试。"
        )
    if any(k in msg for k in ("proxy", "connection refused", "connection reset", "connect")):
        return "网络连接失败，请在侧栏填写 GEE 代理（如 http://127.0.0.1:7892）并确认 VPN 可用。"
    if any(k in msg for k in ("not authenticated", "credentials", "permission denied")):
        return "GEE 认证或项目权限异常，请执行 earthengine authenticate 并确认 Project ID。"
    return f"GEE 查询失败：{exc}"


def _date_chunks(start_date: str, end_date: str, chunk_days: int = 31):
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end < start:
        raise ValueError("结束日期不能早于开始日期")
    chunks = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end)
        chunks.append((cur.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        cur = chunk_end + timedelta(days=1)
    return chunks


def _load_roi_geometry(roi_path: str, roi_name: str):
    if roi_path.endswith(".shp") or roi_path.endswith(".geojson"):
        gdf = gpd.read_file(roi_path).to_crs("EPSG:4326")
        if roi_name and "name" in gdf.columns:
            sub = gdf[gdf["name"].astype(str) == str(roi_name)]
            if not sub.empty:
                gdf = sub
        if gdf.empty:
            raise ValueError(f"ROI 矢量中未找到 name={roi_name!r}")
        if len(gdf) > 1:
            gdf = gdf.dissolve()
        fc = geemap.gdf_to_ee(gdf)
        return fc.geometry()
    fc = ee.FeatureCollection(roi_path).filter(ee.Filter.eq("name", roi_name))
    geom = fc.geometry()
    if geom is None:
        raise ValueError(f"GEE Asset 中未找到 name={roi_name!r}")
    return geom


def run_m4_download(
    roi_path: str,
    roi_name: str,
    start_date: str,
    end_date: str,
    export_to: str = "drive",
    local_out_dir: str = "./M4_Downloads",
    bands=None,
    cloud_limit: int = 60,
    min_land_pct: float = 5.0,
    max_land_pct: float = 95.0,
    min_pixel_count: int = 1000,
    drive_folder: str = "GEE_Downloads",
    scale: int = 10,
    gee_proxy_url=None,
    gee_project_id=None,
    push_log=print,
    push_progress=None,
    stop_callback=None,
    on_task_started=None,
):
    """
    执行 M4 流水线。export_to: 'drive' | 'local'。
    返回 dict: image_count, export_to, local_out_dir, drive_folder, id_list
    on_task_started: 可选回调，drive 模式每个任务 task.start() 后调用 task_obj
    （供可信执行闭环记录 GEE 任务 id / 状态）。
    """
    if bands is None:
        bands = ["B8", "B4", "B3", "B2", "B11"]

    def _prog(pct):
        if push_progress:
            push_progress(int(min(100, max(0, pct))))

    def _stop():
        return bool(stop_callback and stop_callback())

    export_to = (export_to or "drive").lower()
    if export_to == "local":
        os.makedirs(local_out_dir, exist_ok=True)

    with gee_network_context(gee_proxy_url, push_log=push_log):
        ensure_ee_initialized(
            gee_proxy_url=gee_proxy_url,
            gee_project_id=gee_project_id,
            push_log=push_log,
        )

        push_log(f"[M4] 任务 {roi_name} | {start_date} ~ {end_date}")
        _prog(2)
        push_log("[M4] 加载 ROI…")
        roi_geom = _load_roi_geometry(roi_path, roi_name)

        def mask_s2clouds_qa60(image):
            qa = image.select("QA60")
            cloud_bit, cirrus_bit = 1 << 10, 1 << 11
            mask = qa.bitwiseAnd(cloud_bit).eq(0).And(qa.bitwiseAnd(cirrus_bit).eq(0))
            return (
                image.updateMask(mask)
                .divide(10000)
                .select("B.*")
                .copyProperties(image, ["system:time_start"])
            )

        def mask_zero(image):
            return image.updateMask(image.select("B8").gt(0))

        def apply_cloud_score_plus(image):
            mask = image.select("cs").lte(0.4)
            return image.updateMask(mask.Not())

        def calculate_indices_and_stats(image):
            mndwi = image.normalizedDifference(["B3", "B11"]).rename("mNDWI")
            mndwi = mndwi.updateMask(mndwi.gt(-1).And(mndwi.lt(1)))
            image = image.addBands(mndwi)
            pixels = (
                image.select("B3")
                .reduceRegion(
                    reducer=ee.Reducer.count(),
                    geometry=roi_geom,
                    scale=stats_scale,
                    maxPixels=1e13,
                )
                .get("B3")
            )
            image = image.set("pixel_count", ee.Number(pixels))
            land = image.updateMask(image.select("mNDWI").lte(0))
            land_pixels = (
                land.select("mNDWI")
                .reduceRegion(
                    reducer=ee.Reducer.count(),
                    geometry=roi_geom,
                    scale=stats_scale,
                    maxPixels=1e13,
                )
                .get("mNDWI")
            )
            land_percent = ee.Number(land_pixels).divide(ee.Number(pixels).max(1)).multiply(100)
            return image.set("land_percent", land_percent)

        def _filter_chunk_ids(chunk_start: str, chunk_end: str):
            data_col = (
                s2_sr.filterBounds(roi_geom)
                .filterDate(chunk_start, chunk_end)
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_limit))
                .map(mask_s2clouds_qa60)
                .map(mask_zero)
                .map(lambda img: img.clip(roi_geom))
            )
            cs_linked = data_col.linkCollection(s2_cs, "cs").map(apply_cloud_score_plus)
            final_col = (
                cs_linked.map(calculate_indices_and_stats)
                .filter(ee.Filter.gt("pixel_count", min_pixel_count))
                .filter(ee.Filter.lt("land_percent", max_land_pct))
                .filter(ee.Filter.gt("land_percent", min_land_pct))
            )
            try:
                return final_col.aggregate_array("system:index").getInfo() or []
            except Exception as e:
                raise RuntimeError(_gee_error_hint(e)) from e

        def _export_image(img_id: str):
            return (
                s2_sr.filter(ee.Filter.eq("system:index", img_id))
                .map(mask_s2clouds_qa60)
                .map(mask_zero)
                .map(lambda img: img.clip(roi_geom))
                .first()
            )

        # 与 GEE Code Editor 一致：pixel_count / land_percent 均在 export scale（默认 10m）上统计
        stats_scale = scale
        push_log("[M4] GEE 云端筛选（QA60 + Cloud Score+ + 水陆占比）…")
        _prog(5)
        s2_sr = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        s2_cs = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")

        chunks = _date_chunks(start_date, end_date)
        if len(chunks) > 1:
            push_log(f"[M4] 日期跨度较大，将分 {len(chunks)} 批按月查询 GEE…")

        all_ids = []
        for i, (chunk_start, chunk_end) in enumerate(chunks):
            if _stop():
                return None
            if len(chunks) > 1:
                push_log(f"[M4] 分批 {i + 1}/{len(chunks)}: {chunk_start} ~ {chunk_end}")
            chunk_ids = _filter_chunk_ids(chunk_start, chunk_end)
            push_log(f"  |-- 本批 {len(chunk_ids)} 景")
            all_ids.extend(chunk_ids)
            _prog(5 + int(20 * (i + 1) / len(chunks)))

        id_list = sorted(set(all_ids))
        image_count = len(id_list)
        push_log(f"[M4] 筛选完成，共 {image_count} 景")
        if image_count == 0:
            raise ValueError("未找到符合条件的影像，请放宽云量/水陆占比/像素阈值")

        _prog(25)
        if _stop():
            return None

        region_coords = roi_geom.getInfo()["coordinates"]
        n = len(id_list)

        if export_to == "drive":
            push_log(f"[M4] 向 Google Drive 提交 {n} 个导出任务…")
            for i, img_id in enumerate(id_list):
                if _stop():
                    push_log("[M4] 用户中止任务提交")
                    return None
                img = _export_image(img_id)
                task = ee.batch.Export.image.toDrive(
                    image=img.select(bands),
                    description=f"{roi_name}_{img_id}",
                    folder=drive_folder,
                    region=region_coords,
                    scale=scale,
                    maxPixels=1e13,
                    fileFormat="GeoTIFF",
                )
                task.start()
                if on_task_started:
                    try:
                        on_task_started(task)
                    except Exception as e:  # noqa: BLE001
                        push_log(f"  |-- 记录任务失败: {e}")
                if (i + 1) % 5 == 0 or i == n - 1:
                    push_log(f"  |-- 已提交 {i + 1}/{n}")
                _prog(25 + int(75 * (i + 1) / max(n, 1)))
            push_log("[M4] 全部任务已提交。请在 GEE Code Editor → Tasks 或 Google Drive 查看进度。")
        else:
            push_log(f"[M4] 本地下载至 {local_out_dir}")
            for i, img_id in enumerate(id_list):
                if _stop():
                    push_log("[M4] 用户中止下载")
                    return None
                out_tif = os.path.join(local_out_dir, f"{roi_name}_{img_id}.tif")
                if os.path.isfile(out_tif) and os.path.getsize(out_tif) > 0:
                    push_log(f"  |-- 跳过已存在: {os.path.basename(out_tif)}")
                else:
                    img = _export_image(img_id)
                    try:
                        geemap.ee_export_image(
                            img.select(bands),
                            filename=out_tif,
                            scale=scale,
                            region=roi_geom,
                            file_per_band=False,
                        )
                        push_log(f"  |-- 完成: {os.path.basename(out_tif)}")
                    except Exception as e:
                        push_log(f"  |-- 失败 [{img_id}]: {e}")
                _prog(25 + int(75 * (i + 1) / max(n, 1)))

        _prog(100)
        return {
            "image_count": image_count,
            "export_to": export_to,
            "local_out_dir": local_out_dir if export_to == "local" else None,
            "drive_folder": drive_folder if export_to == "drive" else None,
            "id_list": id_list,
            "roi_name": roi_name,
        }
