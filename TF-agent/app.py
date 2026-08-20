import streamlit as st
import leafmap.foliumap as leafmap
import streamlit.components.v1 as components
from streamlit_folium import st_folium
import sidebar_ui as sbui
import ui_labels as uil
import hashlib
import os
import re
import glob
import time
import io

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# Clash 默认混合代理端口（可在 .env 用 GEE_PROXY_URL 覆盖）
DEFAULT_CLASH_PROXY = (os.environ.get("GEE_PROXY_URL") or "http://127.0.0.1:7892").strip()

# localtileserver 访问本机瓦片服务时若走系统代理(如 127.0.0.1:7892)会加载失败
_NO_PROXY = "127.0.0.1,localhost,::1"
for _pk in ("NO_PROXY", "no_proxy"):
    _cur = os.environ.get(_pk, "")
    if _cur:
        if "127.0.0.1" not in _cur:
            os.environ[_pk] = f"{_cur},{_NO_PROXY}"
    else:
        os.environ[_pk] = _NO_PROXY

import torch
import datetime
import json
import math
import contextlib
import threading
import traceback
import numpy as np


@contextlib.contextmanager
def _local_tile_no_proxy():
    """加载地图瓦片时临时禁用 HTTP 代理，避免 localhost 被转到 Clash 端口。"""
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

DEBUG_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_debug.log")


def _append_debug_log(message: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {message}\n")


def _format_agent_exception(exc: Exception) -> str:
    parts = [f"{type(exc).__name__}: {exc}"]
    status = getattr(exc, "status_code", None)
    code = getattr(exc, "code", None)
    if status is not None:
        parts.append(f"status_code={status}")
    if code:
        parts.append(f"code={code}")
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            parts.append(f"response={resp.text}")
        except Exception:
            pass
    return " | ".join(parts)


def _chat_preview_uint8(rgb: np.ndarray) -> np.ndarray:
    """Stretch raster preview to uint8 for chat display."""
    out = np.zeros(rgb.shape, dtype=np.uint8)
    valid = np.isfinite(rgb).all(axis=2)
    if not valid.any():
        return out
    for c in range(rgb.shape[2]):
        ch = rgb[..., c].astype(np.float32)
        vals = ch[valid]
        if vals.size == 0:
            continue
        lo = np.percentile(vals, 2)
        hi = np.percentile(vals, 98)
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo = np.min(vals)
            hi = np.max(vals)
        if hi <= lo:
            continue
        norm = np.clip((ch - lo) / (hi - lo), 0.0, 1.0)
        norm = np.where(np.isfinite(norm), norm, 0.0)
        out[..., c] = (norm * 255.0).astype(np.uint8)
    return out


def _save_chat_image_preview(uploaded_file):
    """Save a lightweight PNG preview for chat history rendering."""
    if uploaded_file is None:
        return None, None
    name = os.path.basename(getattr(uploaded_file, "name", "") or "upload_image")
    ext = os.path.splitext(name)[1].lower()
    safe = "".join(c for c in name if c.isalnum() or c in "._-") or "upload_image"
    yy_dir = os.path.dirname(os.path.abspath(__file__))
    preview_dir = os.path.join(yy_dir, "_chat_upload_tmp", "_preview_cache")
    os.makedirs(preview_dir, exist_ok=True)
    preview_path = os.path.join(preview_dir, f"preview_{os.getpid()}_{int(time.time() * 1000)}_{safe}.png")
    raw = uploaded_file.getbuffer()

    try:
        from PIL import Image

        if ext in (".tif", ".tiff"):
            import rasterio
            from rasterio.io import MemoryFile

            with MemoryFile(bytes(raw)) as mem:
                with mem.open() as ds:
                    data = ds.read(masked=True)
            if data.shape[0] >= 3:
                rgb = np.moveaxis(data[:3, :, :], 0, -1)
            elif data.shape[0] == 2:
                two = np.moveaxis(data[:2, :, :], 0, -1)
                rgb = np.concatenate([two, two[..., 1:2]], axis=-1)
            else:
                one = data[0]
                rgb = np.repeat(one[:, :, None], 3, axis=2)

            rgb_plain = np.ma.filled(rgb, np.nan) if np.ma.isMaskedArray(rgb) else rgb
            valid = np.isfinite(rgb_plain).all(axis=2)
            if valid.any() and float(valid.mean()) < 0.70:
                ys, xs = np.where(valid)
                y0, y1 = ys.min(), ys.max()
                x0, x1 = xs.min(), xs.max()
                pad = 16
                y0 = max(0, y0 - pad)
                x0 = max(0, x0 - pad)
                y1 = min(rgb_plain.shape[0] - 1, y1 + pad)
                x1 = min(rgb_plain.shape[1] - 1, x1 + pad)
                rgb_plain = rgb_plain[y0 : y1 + 1, x0 : x1 + 1, :]

            preview = Image.fromarray(_chat_preview_uint8(rgb_plain), mode="RGB")
        else:
            preview = Image.open(io.BytesIO(bytes(raw))).convert("RGB")

        preview.thumbnail((1400, 1400), Image.Resampling.BILINEAR)
        preview.save(preview_path, format="PNG")
        return preview_path, name
    except Exception as e:
        _append_debug_log(f"save_chat_preview_failed: {e}; file={name}")
        return None, name


def _nodata_safe_for_tile_api(value):
    """
    GeoTIFF 的 nodata 常为 nan；传给 localtileserver 的 query 会序列化失败或行为异常，导致整段加载报错。
    返回可安全传入 API 的数，或 None 表示不传参（由服务端按文件元数据处理）。
    """
    if value is None:
        return None
    try:
        if isinstance(value, (float, np.floating)) and (np.isnan(value) or np.isinf(value)):
            return None
    except (TypeError, ValueError):
        return None
    return value


# Agent 暗号解析：须兼容 ① 标准竖线 ② 模型常瞎写的括号逗号 ③ 省略 zoom（默认 8）
_RE_CMD_MAP_PIPE = re.compile(
    r"COMMAND_UPDATE_MAP\s*\|\s*([-\d.]+)\s*\|\s*([-\d.]+)\s*\|\s*(\d+)",
    re.IGNORECASE,
)
_RE_CMD_MAP_PAREN = re.compile(
    r"COMMAND_UPDATE_MAP\s*[\(:（]\s*([-\d.]+)\s*[,，]\s*([-\d.]+)(?:\s*[,，]\s*(\d+))?\s*[\)）]?",
    re.IGNORECASE,
)
_RE_CMD_PIPELINE = re.compile(
    r"COMMAND_RUN_PIPELINE\s*\|\s*([^|\n]+?)\s*\|\s*([-\d.]+)\s*\|\s*(\d+)",
    re.IGNORECASE,
)
# 模型常只写自然语言坐标而未附 SYSTEM_COMMAND / COMMAND_UPDATE_MAP
_RE_MAP_COORDS_NSEW = re.compile(
    r"([-\d.]+)\s*[°º]?\s*[Nn北]\s*[,，/]\s*([-\d.]+)\s*[°º]?\s*[Ee东]",
)
_RE_MAP_COORDS_PLAIN = re.compile(
    r"(?:中心点|中心|坐标|定位(?:至|到)?|跳转(?:至|到)?|视角)\s*"
    r"[（(]?\s*([-\d.]+)\s*[,，]\s*([-\d.]+)\s*[)）]?",
)
_RE_MAP_ZOOM = re.compile(
    r"(?:缩放(?:级别|等级)?|zoom)\s*(?:为|到|=|：|:)?\s*(\d{1,2})",
    re.IGNORECASE,
)
_RE_MAP_INTENT = re.compile(
    r"(已定位|已跳转|已将地图|地图视角|视角已|飞到|定位到|跳转到|挪到|中心点)",
)


def _parse_agent_map_command(reply: str):
    """解析地图跳转：标准暗号，或模型自然语言中的坐标+缩放。"""
    stripped = re.sub(r"[`\*_]+", " ", reply or "")
    flat = re.sub(r"[\n\r]+", " ", stripped)
    for text in (flat, stripped, reply or ""):
        m = _RE_CMD_MAP_PIPE.search(text)
        if m:
            try:
                return float(m.group(1)), float(m.group(2)), int(m.group(3)), m.group(0)
            except (ValueError, TypeError):
                pass
        m = _RE_CMD_MAP_PAREN.search(text)
        if m:
            try:
                lat = float(m.group(1))
                lon = float(m.group(2))
                zoom = int(m.group(3)) if m.group(3) else 8
                return lat, lon, zoom, m.group(0)
            except (ValueError, TypeError):
                pass

    # 自然语言回退：仅在明确“定位/跳转”语境下提取，避免误伤普通问答
    if not _RE_MAP_INTENT.search(flat):
        return None
    lat = lon = None
    span = ""
    m = _RE_MAP_COORDS_NSEW.search(flat)
    if m:
        try:
            lat, lon = float(m.group(1)), float(m.group(2))
            span = m.group(0)
        except (ValueError, TypeError):
            lat = lon = None
    if lat is None:
        m = _RE_MAP_COORDS_PLAIN.search(flat)
        if m:
            try:
                lat, lon = float(m.group(1)), float(m.group(2))
                span = m.group(0)
            except (ValueError, TypeError):
                lat = lon = None
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    zoom = 9
    mz = _RE_MAP_ZOOM.search(flat)
    if mz:
        try:
            zoom = max(1, min(18, int(mz.group(1))))
        except (ValueError, TypeError):
            zoom = 9
    return lat, lon, zoom, span or f"{lat},{lon},{zoom}"


def _strip_map_command_from_reply(reply: str) -> str:
    """从回复中去掉地图暗号片段（含 Markdown 包裹）。"""
    t = reply
    for pat in (_RE_CMD_MAP_PIPE, _RE_CMD_MAP_PAREN):
        t = pat.sub("", t, count=1)
    t = re.sub(r"[`\*_]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _parse_agent_pipeline_command(reply: str):
    flat = re.sub(r"[\n\r]+", " ", reply)
    m = _RE_CMD_PIPELINE.search(flat) or _RE_CMD_PIPELINE.search(reply)
    if not m:
        return None
    try:
        task = m.group(1).strip()
        prob = float(m.group(2))
        cnt = int(m.group(3))
        return task, prob, cnt, m.group(0)
    except (ValueError, TypeError):
        return None

# =======================================================
#  0. 导入后端引擎与智能体大脑
# =======================================================
try:
    import pre_engine
    import post_engine
except ImportError:
    st.error("⚠️ 未找到后端引擎文件 (pre_engine.py, post_engine.py)。")

# =======================================================
#  数据资产管理
# =======================================================
ASSET_REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets_registry.json")


def load_asset_registry():
    if os.path.exists(ASSET_REGISTRY_PATH):
        try:
            with open(ASSET_REGISTRY_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_asset_registry(registry):
    with open(ASSET_REGISTRY_PATH, 'w', encoding='utf-8') as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)


def register_asset(task, prob, cnt, file_path):
    registry = load_asset_registry()
    asset_key = f"{task}_p{prob:.2f}_c{cnt}"
    file_path = os.path.normpath(os.path.abspath(str(file_path).strip().strip('"').strip("'")))
    size_mb = 0
    if os.path.exists(file_path):
        if file_path.lower().endswith(".shp"):
            stem = os.path.splitext(file_path)[0]
            for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                side = stem + ext
                if os.path.isfile(side):
                    size_mb += os.path.getsize(side)
        else:
            size_mb = os.path.getsize(file_path)
    registry[asset_key] = {
        "task": task,
        "prob_threshold": prob,
        "min_count": cnt,
        "file_path": file_path,
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "file_size_mb": round(size_mb / (1024 ** 2), 2) if size_mb else 0
    }
    save_asset_registry(registry)
    return asset_key


def find_asset(task, prob, cnt):
    registry = load_asset_registry()
    asset_key = f"{task}_p{prob:.2f}_c{cnt}"
    entry = registry.get(asset_key)
    if entry and os.path.exists(entry.get("file_path", "")):
        return entry
    return None


def register_index_asset(task, file_path):
    registry = load_asset_registry()
    asset_key = f"{task}_index"
    file_path = os.path.normpath(os.path.abspath(str(file_path).strip().strip('"').strip("'")))
    registry[asset_key] = {
        "task": task,
        "method": "index",
        "prob_threshold": None,
        "min_count": None,
        "file_path": file_path,
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "file_size_mb": round(os.path.getsize(file_path) / (1024 ** 2), 2) if os.path.exists(file_path) else 0,
    }
    save_asset_registry(registry)
    return asset_key


def register_m5_asset(task, report: dict):
    """将 M5 报告与差异面登记到资产账本。"""
    import m5_agent_loop

    registry = load_asset_registry()
    asset_key = f"{task}_m5"
    map_path = m5_agent_loop.pick_m5_map_path(report)
    spatial = (report or {}).get("spatial_outputs") or {}
    loss = spatial.get("loss_shapefile_path")
    silt = spatial.get("siltation_shapefile_path")
    if loss and str(loss) == "None":
        loss = None
    if silt and str(silt) == "None":
        silt = None
    size_mb = 0.0
    for p in (map_path, loss, silt, (report or {}).get("report_path")):
        if not p or not os.path.isfile(str(p)):
            continue
        try:
            size_mb += os.path.getsize(str(p)) / (1024 ** 2)
        except OSError:
            pass
    registry[asset_key] = {
        "task": task,
        "method": "m5",
        "file_path": os.path.normpath(map_path) if map_path else "",
        "report_path": (report or {}).get("report_path"),
        "loss_shp": loss if loss and os.path.isfile(str(loss)) else None,
        "siltation_shp": silt if silt and os.path.isfile(str(silt)) else None,
        "baseline_task": (report or {}).get("baseline_task"),
        "alert_level": (report or {}).get("alert_level"),
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "file_size_mb": round(size_mb, 2),
    }
    save_asset_registry(registry)
    return asset_key


def find_m5_asset(task):
    registry = load_asset_registry()
    entry = registry.get(f"{task}_m5")
    if not entry:
        return None
    rp = entry.get("report_path") or entry.get("file_path")
    if rp and os.path.exists(str(rp)):
        return entry
    if entry.get("file_path") and os.path.exists(entry["file_path"]):
        return entry
    return None


def register_e1_asset(task, report: dict):
    """将 E1 报告与可选热力/分歧图登记到资产账本。"""
    import e1_agent_loop

    registry = load_asset_registry()
    asset_key = f"{task}_e1"
    map_path = e1_agent_loop.pick_e1_map_path(report)
    size_mb = 0.0
    for p in (map_path, (report or {}).get("report_path"), (report or {}).get("report_md_path")):
        if not p or not os.path.isfile(str(p)):
            continue
        try:
            size_mb += os.path.getsize(str(p)) / (1024 ** 2)
        except OSError:
            pass
    registry[asset_key] = {
        "task": task,
        "method": "e1",
        "file_path": os.path.normpath(map_path) if map_path else "",
        "report_path": (report or {}).get("report_path"),
        "report_md_path": (report or {}).get("report_md_path"),
        "reference": (report or {}).get("reference"),
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "file_size_mb": round(size_mb, 2),
    }
    save_asset_registry(registry)
    return asset_key


def find_index_asset(task):
    registry = load_asset_registry()
    entry = registry.get(f"{task}_index")
    if entry and os.path.exists(entry.get("file_path", "")):
        return entry
    return None


def get_task_assets(task):
    registry = load_asset_registry()
    return {k: v for k, v in registry.items()
            if v.get("task") == task and os.path.exists(v.get("file_path", ""))}


def scan_and_register_existing(final_root):
    """首次启动时扫描输出目录，将已有的 Final SHP/TIF 自动注册到资产库。"""
    if not os.path.exists(final_root):
        return
    registry = load_asset_registry()
    changed = False
    for task_dir in os.listdir(final_root):
        task_path = os.path.join(final_root, task_dir)
        if not os.path.isdir(task_path):
            continue
        for f in os.listdir(task_path):
            fpath = os.path.join(task_path, f)
            if f.endswith("_Index_Final.tif"):
                task_name = f.replace("_Index_Final.tif", "")
                key = f"{task_name}_index"
                if key not in registry:
                    registry[key] = {
                        "task": task_name,
                        "method": "index",
                        "prob_threshold": None,
                        "min_count": None,
                        "file_path": fpath,
                        "created_at": datetime.datetime.fromtimestamp(
                            os.path.getmtime(fpath)).strftime("%Y-%m-%d %H:%M:%S"),
                        "file_size_mb": round(os.path.getsize(fpath) / (1024 ** 2), 2),
                    }
                    changed = True
                continue
            if not (f.endswith(".tif") and "_Final_" in f):
                if not (f.endswith(".shp") and "_Final_" in f):
                    continue
            if "_NUMERATOR" in f or "_DENOMINATOR" in f or f.endswith("_work.tif"):
                continue
            try:
                base = f.replace(".tif", "").replace(".shp", "")
                parts = base.split("_Final_")
                task_name = parts[0]
                param_str = parts[1]
                prob = float(param_str.split("_c")[0].replace("p", ""))
                cnt = int(param_str.split("_c")[1])
                key = f"{task_name}_p{prob:.2f}_c{cnt}"
                if key not in registry:
                    registry[key] = {
                        "task": task_name,
                        "prob_threshold": prob,
                        "min_count": cnt,
                        "file_path": fpath,
                        "created_at": datetime.datetime.fromtimestamp(
                            os.path.getmtime(fpath)).strftime("%Y-%m-%d %H:%M:%S"),
                        "file_size_mb": round(os.path.getsize(fpath) / (1024 ** 2), 2)
                    }
                    changed = True
            except Exception:
                continue
    if changed:
        save_asset_registry(registry)


def _zoom_fit_lonlat(left, bottom, right, top, viewport_px=960, margin=0.06):
    """
    按视口宽度估算 Leaflet/WebMercator 缩放级，使给定经纬度范围尽量占满地图（略留边距）。
    margin：相对四边各扩展的比例，避免贴边裁切。
    """
    lat_mid = (bottom + top) / 2.0
    lon_mid = (left + right) / 2.0
    cos_lat = max(abs(math.cos(math.radians(lat_mid))), 0.01)
    lon_span = max(abs(right - left), 1e-7)
    lat_span = max(abs(top - bottom), 1e-7)
    lon_span *= 1.0 + 2.0 * margin
    lat_span *= 1.0 + 2.0 * margin
    # 与 256px 瓦片、360° 经度范围对齐的常见拟合式（经向考虑纬度缩短）
    z_lon = math.log2(360.0 * viewport_px / (256.0 * lon_span * cos_lat))
    # 纬度方向 Web Mercator 约 ±85°，有效跨度按 ~170° 量级估算
    z_lat = math.log2(170.0 * viewport_px / (256.0 * lat_span))
    zoom = int(math.floor(min(z_lon, z_lat)))
    return lat_mid, lon_mid, max(5, min(19, zoom))


def _view_from_raster_path(path: str):
    """
    从 GeoTIFF 得到 WGS84 中心与缩放：优先用「有效像元」外接框（潮滩条带不会被整幅研究区拉大），
    否则退回文件 bounds。供 st_folium 与 add_raster 对齐视角。
    """
    try:
        import rasterio
        from rasterio.warp import transform_bounds
        from rasterio.windows import Window
        from rasterio.transform import array_bounds
        from rasterio.enums import Resampling
    except ImportError:
        return None
    if not path or not os.path.exists(path):
        return None
    try:
        with rasterio.open(path) as src:
            H, W = int(src.height), int(src.width)
            left, bottom, right, top = src.bounds.left, src.bounds.bottom, src.bounds.right, src.bounds.top
            crs = src.crs

            # 降采样读一屏，找非背景像元（掩膜/概率图常见：整幅外框很大、有效仅沿岸一线）
            if H < 2 or W < 2:
                arr = src.read(1)
                th, tw = int(arr.shape[0]), int(arr.shape[1])
            else:
                tw = min(512, W)
                th = min(512, H)
                arr = src.read(1, out_shape=(th, tw), resampling=Resampling.nearest)

            nd = src.nodata
            if arr.dtype.kind == "f":
                valid = np.isfinite(arr) & (arr > 1e-6)
            elif nd is not None:
                valid = arr != nd
            else:
                valid = arr != 0

            if np.any(valid):
                ys, xs = np.where(valid)
                r0, r1 = ys.min(), ys.max()
                c0, c1 = xs.min(), xs.max()
                # 缩略图坐标 → 全图像素窗口（略扩 1 格以免裁到边）
                sy = H / float(th)
                sx = W / float(tw)
                col0 = max(0, int(c0 * sx) - 1)
                row0 = max(0, int(r0 * sy) - 1)
                col1 = min(W, int((c1 + 1) * sx) + 1)
                row1 = min(H, int((r1 + 1) * sy) + 1)
                win = Window(col0, row0, col1 - col0, row1 - row0)
                aff = rasterio.windows.transform(win, src.transform)
                left, bottom, right, top = array_bounds(win.height, win.width, aff)

            if crs is not None:
                try:
                    left, bottom, right, top = transform_bounds(
                        crs, "EPSG:4326", left, bottom, right, top
                    )
                except Exception:
                    pass
    except Exception:
        return None

    return _zoom_fit_lonlat(left, bottom, right, top, viewport_px=960, margin=0.05)


def _view_from_vector_path(path: str):
    """从 Shapefile 得到 WGS84 中心与缩放，供 st_folium 对齐视角。"""
    try:
        import geopandas as gpd
    except ImportError:
        return None
    if not path or not os.path.exists(path):
        return None
    try:
        gdf = gpd.read_file(path)
        if gdf.empty or gdf.crs is None:
            return None
        gdf_wgs = gdf.to_crs(4326)
        minx, miny, maxx, maxy = gdf_wgs.total_bounds
        return _zoom_fit_lonlat(minx, miny, maxx, maxy, viewport_px=960, margin=0.05)
    except Exception:
        return None


def _view_from_asset_path(path: str):
    """按扩展名从栅格或矢量成果推断地图视角。"""
    if not path:
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext == ".shp":
        return _view_from_vector_path(path)
    return _view_from_raster_path(path)


def _cached_view_for_asset_path(session_dict, path: str):
    """同一成果在 session 内按 mtime 缓存视角，减少反复打开大文件。"""
    if not path or not os.path.exists(path):
        return None
    abs_p = os.path.normpath(os.path.abspath(path))
    try:
        mt = os.path.getmtime(abs_p)
    except OSError:
        return _view_from_asset_path(abs_p)
    cache = session_dict.setdefault("_asset_view_cache", {})
    prev = cache.get(abs_p)
    if prev and prev[0] == mt:
        return prev[1]
    v = _view_from_asset_path(abs_p)
    if v is not None:
        cache[abs_p] = (mt, v)
        while len(cache) > 24:
            cache.pop(next(iter(cache)))
    return v


def _cached_view_for_raster_path(session_dict, path: str):
    """兼容旧调用：栅格或矢量成果均可。"""
    return _cached_view_for_asset_path(session_dict, path)


def _add_result_raster_to_map(m, path: str, layer_name: str, opacity: float = 0.5):
    """
    使用 leafmap.add_raster（localtileserver + folium）。
    「Reds」色图会把未标记为 nodata 的 0 值渲成白色；单波段成果若文件未写 nodata，默认按 0 作为透明背景。
    opacity：成果层整体透明度（0~1），由侧栏滑块控制。
    """
    norm = os.path.normpath(os.path.abspath(path))
    if not os.path.exists(norm):
        return False, f"文件不存在: {norm}"
    _nd_api = None
    _nb = 1
    try:
        import rasterio

        with rasterio.open(norm) as _ds:
            _nb = int(_ds.count)
            _nd_api = _nodata_safe_for_tile_api(_ds.nodata)
            # 单波段整型掩膜/概率图常见 0=背景；未声明 nodata 时 localtileserver 仍会对 0 上色 → 大块白底
            if _nb == 1 and _nd_api is None:
                dt = str(_ds.dtypes[0])
                if dt.startswith(("uint", "int")) or "float" in dt:
                    _nd_api = 0
    except Exception as e:
        return False, f"无法读取栅格元数据: {e}"

    op = float(max(0.05, min(1.0, opacity)))
    kw = dict(
        layer_name=layer_name,
        colormap="Reds",
        opacity=op,
        client_args={"cors_all": True},
    )
    if _nb == 1:
        kw["indexes"] = 1
    if _nd_api is not None:
        kw["nodata"] = _nd_api
    try:
        with _local_tile_no_proxy():
            m.add_raster(norm, **kw)
        return True, None
    except Exception as e:
        return False, str(e)


def _add_result_vector_to_map(m, path: str, layer_name: str, opacity: float = 0.5):
    """在 Folium 地图上叠加潮滩 Shapefile 成果层。"""
    norm = os.path.normpath(os.path.abspath(path))
    if not os.path.exists(norm):
        return False, f"文件不存在: {norm}"
    try:
        import geopandas as gpd
        import folium
    except ImportError as e:
        return False, f"缺少 geopandas/folium: {e}"

    try:
        gdf = gpd.read_file(norm)
        if gdf.empty:
            return False, "Shapefile 为空"
        if gdf.crs is not None:
            try:
                epsg = gdf.crs.to_epsg()
            except Exception:
                epsg = None
            if epsg != 4326:
                gdf = gdf.to_crs(4326)
        op = float(max(0.05, min(1.0, opacity)))
        folium.GeoJson(
            gdf,
            name=layer_name,
            style_function=lambda _x: {
                "fillColor": "#e41a1c",
                "color": "#b71c1c",
                "weight": 1,
                "fillOpacity": op,
            },
        ).add_to(m)
        return True, None
    except Exception as e:
        return False, str(e)


def _add_result_to_map(m, path: str, layer_name: str, opacity: float = 0.5):
    """按扩展名选择栅格或矢量方式加载潮滩成果。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".shp":
        return _add_result_vector_to_map(m, path, layer_name, opacity=opacity)
    return _add_result_raster_to_map(m, path, layer_name, opacity=opacity)


def _run_m5_phase(ctx, shared, current_shp, actual_task, prob, cnt, push_log, check_stop):
    """合成完成后执行时空异常检测（失败不阻断主流程）。"""
    if not ctx.get("m5_enabled", True):
        return None
    if check_stop():
        return None
    push_log(">>> [Phase 3]  潮滩变化分析…")
    try:
        import m5_engine

        report = m5_engine.run_m5_after_synthesis(
            current_shp=current_shp,
            current_task=actual_task,
            final_root=ctx["final_root"],
            task_options=ctx.get("task_options"),
            prob=prob,
            cnt=cnt,
            baseline_shp_override=(ctx.get("m5_baseline_shp") or "").strip() or None,
            workspace_dir=ctx["final_root"],
            logger=push_log,
        )
        if report:
            with shared["lock"]:
                shared["m5_report"] = report
            lvl = report.get("alert_level", "GREEN")
            push_log(f"[M5] 变化分析完成，告警级别: {lvl}")
        else:
            push_log("[M5] 未生成变化告警（可能缺少往年同区域基线）。")
        return report
    except Exception as e:
        push_log(f"[M5] 变化分析异常: {e}")
        return None


def run_m5_sync(ctx, shared, stop_event):
    """独立 M5 闭环：仅调用现有 M5 引擎，不跑推理/GEE。"""
    logs_local = []

    def check_stop():
        return stop_event.is_set()

    def push_log(msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] root@m5: {msg}"
        logs_local.append(line)
        with shared["lock"]:
            shared["log_lines"] = logs_local[-40:]
        print(msg)

    def push_progress(pct):
        with shared["lock"]:
            shared["progress"] = int(min(100, max(0, pct)))

    def push_status(kind, text):
        with shared["lock"]:
            shared["status"] = (kind, text)

    push_progress(5)
    push_status("info", "潮滩变化分析启动…")
    m5_cfg = ctx.get("m5") or {}
    task = ctx.get("task") or m5_cfg.get("plan", {}).get("current_task")
    current_shp = m5_cfg.get("current_shp")
    baseline_shp = m5_cfg.get("baseline_shp")
    push_log(f"TASK: {task}")
    push_log(f"CURRENT: {current_shp}")
    push_log(f"BASELINE: {baseline_shp} ({m5_cfg.get('baseline_task') or '—'})")

    if check_stop():
        push_status("warning", "变化分析已中断")
        return False
    if not current_shp or not os.path.isfile(str(current_shp)):
        push_status("error", "当期潮滩 SHP 不存在")
        push_log(f"[ERROR] 当期 SHP 无效: {current_shp}")
        return False
    if not baseline_shp or not os.path.isfile(str(baseline_shp)):
        push_status("error", "基线潮滩 SHP 不存在")
        push_log(f"[ERROR] 基线 SHP 无效: {baseline_shp}")
        return False

    push_progress(30)
    try:
        import m5_engine
        import m5_agent_loop

        report = m5_engine.run_m5_after_synthesis(
            current_shp=current_shp,
            current_task=task,
            final_root=ctx["final_root"],
            task_options=ctx.get("task_options"),
            prob=ctx.get("prob"),
            cnt=ctx.get("cnt"),
            baseline_shp_override=baseline_shp,
            workspace_dir=ctx["final_root"],
            logger=push_log,
        )
        push_progress(80)
        if not report:
            push_status("warning", "变化分析未生成结果")
            return False
        report["baseline_task"] = report.get("baseline_task") or m5_cfg.get("baseline_task")
        verification = m5_agent_loop.verify_m5_outputs(report, workspace_dir=ctx["final_root"])
        map_path = verification.get("map_candidate") or m5_agent_loop.pick_m5_map_path(report)
        try:
            register_m5_asset(task, report)
            push_log(f"[M5] 已登记资产 {task}_m5")
        except Exception as reg_e:
            push_log(f"[M5] 资产登记失败（不影响报告）: {reg_e}")

        with shared["lock"]:
            shared["m5_report"] = report
            shared["m5_verification"] = verification
            shared["asset_path"] = map_path
            shared["job_kind"] = "m5"

        if verification.get("ok"):
            push_status(
                "success",
                f"变化分析完成 · 告警 {report.get('alert_level', '—')}",
            )
        else:
            push_status("warning", "变化分析已完成但输出校验未完全通过")
        push_progress(100)
        push_log(m5_agent_loop.summarize_m5_report_for_chat(report, verification).replace("\n", " | "))
        return True
    except Exception as e:
        push_log(f"[ERROR] {e}")
        push_status("error", f"变化分析异常: {e}")
        import traceback

        traceback.print_exc()
        return False


def _m5_worker_entry(ctx, shared, stop_event):
    ok = False
    try:
        ok = run_m5_sync(ctx, shared, stop_event)
    except Exception as e:
        tb_lines = traceback.format_exc().split("\n")[:25]
        with shared["lock"]:
            lines = list(shared.get("log_lines") or [])
            lines.append(f"[CRASH] {e}")
            lines.extend(tb_lines)
            shared["log_lines"] = lines[-40:]
            shared["status"] = ("error", str(e))
    finally:
        with shared["lock"]:
            shared["success"] = ok
            shared["done"] = True


def run_e1_sync(ctx, shared, stop_event):
    """独立 E1 闭环：仅调用 e1_engine，不跑推理/GEE。"""
    logs_local = []

    def check_stop():
        return stop_event.is_set()

    def push_log(msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] root@e1: {msg}"
        logs_local.append(line)
        with shared["lock"]:
            shared["log_lines"] = logs_local[-40:]
        print(msg)

    def push_progress(pct):
        with shared["lock"]:
            shared["progress"] = int(min(100, max(0, pct)))

    def push_status(kind, text):
        with shared["lock"]:
            shared["status"] = (kind, text)

    push_progress(5)
    push_status("info", "潮滩精度评价启动…")
    e1_cfg = ctx.get("e1") or {}
    task = ctx.get("task") or e1_cfg.get("plan", {}).get("current_task")
    target_shp = e1_cfg.get("target_shp")
    push_log(f"TASK: {task}")
    push_log(f"TARGET: {target_shp}")
    push_log(f"REF: {e1_cfg.get('reference')} | DATA: {e1_cfg.get('data_root')}")

    if check_stop():
        push_status("warning", "精度评价已中断")
        return False
    if not target_shp or not os.path.isfile(str(target_shp)):
        push_status("error", "当期潮滩 SHP 不存在")
        push_log(f"[ERROR] 目标 SHP 无效: {target_shp}")
        return False

    push_progress(25)
    try:
        import e1_engine
        import e1_agent_loop

        roi_path = e1_engine.resolve_task_roi_path(
            e1_cfg.get("task_aoi_shp") or ctx.get("task_aoi_shp"),
            task,
            ctx["final_root"],
            logger=push_log,
        )
        workspace = e1_cfg.get("workspace_dir") or e1_engine.workspace_for_task(
            ctx["final_root"], task
        )
        report = e1_engine.run_e1_after_synthesis(
            target_shp=target_shp,
            roi_name=task,
            workspace_dir=workspace,
            data_root=e1_cfg.get("data_root") or e1_engine.DEFAULT_E1_DATA_ROOT,
            reference=e1_cfg.get("reference") or "师姐_2020",
            compare_sources=e1_cfg.get("compare_sources"),
            roi_path=roi_path,
            export_disagreement_maps=bool(e1_cfg.get("export_disagreement_maps", True)),
            export_multi_product_heatmap=bool(e1_cfg.get("export_multi_product_heatmap", True)),
            logger=push_log,
        )
        push_progress(80)
        if not report:
            push_status("warning", "精度评价未生成结果")
            return False
        verification = e1_agent_loop.verify_e1_outputs(report)
        map_path = verification.get("map_candidate") or e1_agent_loop.pick_e1_map_path(report)
        try:
            register_e1_asset(task, report)
            push_log(f"[E1] 已登记资产 {task}_e1")
        except Exception as reg_e:
            push_log(f"[E1] 资产登记失败（不影响报告）: {reg_e}")

        with shared["lock"]:
            shared["e1_report"] = report
            shared["e1_verification"] = verification
            shared["asset_path"] = map_path
            shared["job_kind"] = "e1"

        n = len(report.get("comparisons") or {})
        if verification.get("ok"):
            push_status("success", f"精度评价完成 · {n} 组对比")
        else:
            push_status("warning", "精度评价已完成但输出校验未完全通过")
        push_progress(100)
        push_log(e1_agent_loop.summarize_e1_report_for_chat(report, verification).replace("\n", " | "))
        return True
    except Exception as e:
        push_log(f"[ERROR] {e}")
        push_status("error", f"精度评价异常: {e}")
        import traceback as _tb

        _tb.print_exc()
        return False


def _e1_worker_entry(ctx, shared, stop_event):
    ok = False
    try:
        ok = run_e1_sync(ctx, shared, stop_event)
    except Exception as e:
        tb_lines = traceback.format_exc().split("\n")[:25]
        with shared["lock"]:
            lines = list(shared.get("log_lines") or [])
            lines.append(f"[CRASH] {e}")
            lines.extend(tb_lines)
            shared["log_lines"] = lines[-40:]
            shared["status"] = ("error", str(e))
    finally:
        with shared["lock"]:
            shared["success"] = ok
            shared["done"] = True


def _run_e1_phase(ctx, shared, current_shp, actual_task, push_log, check_stop):
    """合成完成后执行多源潮滩一致性诊断（失败不阻断主流程）。"""
    if not ctx.get("e1_enabled", False):
        return None
    if check_stop():
        return None
    push_log(">>> [Phase 4]  潮滩精度评价…")
    try:
        import e1_engine

        roi_path = e1_engine.resolve_task_roi_path(
            ctx.get("task_aoi_shp"),
            actual_task,
            ctx["final_root"],
            logger=push_log,
        )
        workspace = e1_engine.workspace_for_task(ctx["final_root"], actual_task)
        compare_sources = ctx.get("e1_compare_sources") or None
        if compare_sources == []:
            compare_sources = None

        report = e1_engine.run_e1_after_synthesis(
            target_shp=current_shp,
            roi_name=actual_task,
            workspace_dir=workspace,
            data_root=ctx.get("e1_data_root") or e1_engine.DEFAULT_E1_DATA_ROOT,
            reference=ctx.get("e1_reference") or "师姐_2020",
            compare_sources=compare_sources,
            roi_path=roi_path,
            export_disagreement_maps=bool(ctx.get("e1_export_maps", True)),
            export_multi_product_heatmap=bool(ctx.get("e1_export_heatmap", True)),
            logger=push_log,
        )
        if report:
            with shared["lock"]:
                shared["e1_report"] = report
            push_log(f"[E1] 精度评价完成，对比 {len(report.get('comparisons') or {})} 组产品。")
        else:
            push_log("[E1] 未生成精度评价结果。")
        return report
    except Exception as e:
        push_log(f"[E1] 精度评价异常: {e}")
        return None


def run_pipeline_sync(ctx, shared, stop_event):
    """在后台线程中执行推理；只写 shared / 文件，不调用任何 Streamlit API。"""
    logs_local = []

    def check_stop():
        return stop_event.is_set()

    def push_log(msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] root@cstf: {msg}"
        logs_local.append(line)
        with shared["lock"]:
            shared["log_lines"] = logs_local[-30:]
        print(msg)

    def push_progress(pct):
        v = int(min(100, max(0, pct)))
        with shared["lock"]:
            shared["progress"] = v

    def push_status(kind, text):
        with shared["lock"]:
            shared["status"] = (kind, text)

    push_progress(0)
    push_status("info", "初始化系统环境...")

    task = ctx["task"]
    prob = ctx["prob"]
    cnt = ctx["cnt"]
    root_dir = ctx["root_dir"]
    mask_root = ctx["mask_root"]
    final_root = ctx["final_root"]
    model_path = ctx["model_path"]
    shp_path = ctx["shp_path"]
    task_options = ctx["task_options"]

    actual_task = task
    for opt in task_options:
        if task in opt:
            actual_task = opt
            break

    if not actual_task or not root_dir:
        push_status("error", "❌ 未选择有效目标任务，或原始影像目录未配置。请在侧栏选择任务后再运行推理。")
        return False

    input_dir = os.path.join(root_dir, actual_task)
    mask_out_dir = os.path.join(mask_root, actual_task)
    final_out_dir = os.path.join(final_root, actual_task)
    current_final_shp = os.path.join(final_out_dir, f"{actual_task}_Final_p{prob:.2f}_c{cnt}.shp")

    cached = find_asset(actual_task, prob, cnt)
    if cached:
        push_log(f"⚡ 缓存命中: {os.path.basename(cached['file_path'])}")
        cached_shp = cached["file_path"]
        if cached_shp.lower().endswith(".tif"):
            _stem = os.path.splitext(cached_shp)[0]
            _alt = _stem + ".shp"
            if os.path.isfile(_alt):
                cached_shp = _alt
        _run_m5_phase(ctx, shared, cached_shp, actual_task, prob, cnt, push_log, check_stop)
        _run_e1_phase(ctx, shared, cached_shp, actual_task, push_log, check_stop)
        push_status("success", "⚡ 发现已有资产！直接加载，无需重新计算")
        push_progress(100)
        with shared["lock"]:
            shared["asset_path"] = cached["file_path"]
        return True

    push_log(f"INIT TASK: {actual_task} | PROB: {prob} | CNT: {cnt}")

    if not os.path.exists(input_dir):
        push_status("error", f"❌ 找不到原始影像输入目录：{input_dir}")
        return False

    os.makedirs(mask_out_dir, exist_ok=True)
    os.makedirs(final_out_dir, exist_ok=True)

    all_tifs = glob.glob(os.path.join(input_dir, "*.tif"))
    raw_tifs = [f for f in all_tifs if "_mask" not in f and "Final" not in f]
    total = len(raw_tifs)

    if total == 0:
        push_status("warning", "没有找到可以处理的 TIF 影像。")
        return False

    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        push_status("info", "正在载入深度学习模型...")
        model = pre_engine.load_model(model_path, device)
        push_progress(10)
    except Exception as e:
        push_status("error", f"模型加载失败: {e}")
        return False

    push_log(">>> [Phase 1] 开始深度学习推理...")
    success_count = 0

    for idx, tif_path in enumerate(raw_tifs):
        if check_stop():
            push_log("[SYSTEM] 🚨 检测到中断信号，安全终止。")
            push_status("warning", "任务已被手动中止。")
            return False

        fname = os.path.basename(tif_path)
        save_name = fname.replace(".tif", "_mask.tif")
        save_path = os.path.join(mask_out_dir, save_name)

        push_status("info", f"[推理阶段] 处理中: {fname} ({idx + 1}/{total})")

        if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
            success_count += 1
        else:
            try:
                res = pre_engine.process_geotiff(
                    model, tif_path, save_path, device,
                    current_idx=idx + 1, total_batch=total, stop_callback=check_stop
                )
                if res is False:
                    if check_stop():
                        push_log(f"  |-- [STOP] {fname}: 用户已请求中断推理。")
                    else:
                        push_log(
                            f"  |-- [FAIL] {fname}: 单景处理失败（非「中断跑图」；"
                            f"可能为 CUDA/显存/读写出错，见控制台 traceback）。"
                        )
                    return False
                success_count += 1
            except Exception as e:
                push_log(f"  |-- [FAIL] {fname}: {e}")

        push_progress(int(10 + (success_count / total) * 70))

    if check_stop():
        return False

    push_log(">>> [Phase 2] 开始时空频次合成...")
    push_progress(80)
    push_status("info", "正在执行合成算法...")

    def bridge_logger(msg):
        push_log(msg)

    try:
        success = post_engine.generate_double_constraint_complete(
            source_folder=input_dir, mask_folder=mask_out_dir, output_path=current_final_shp,
            shp_path=shp_path, prob_threshold=prob, min_absolute_count=cnt,
            logger=bridge_logger, stop_callback=check_stop
        )
        if success:
            register_asset(actual_task, prob, cnt, current_final_shp)
            _run_m5_phase(ctx, shared, current_final_shp, actual_task, prob, cnt, push_log, check_stop)
            _run_e1_phase(ctx, shared, current_final_shp, actual_task, push_log, check_stop)
            push_progress(100)
            push_status("success", "🎉 全流程完毕！结果已生成并注册到资产库。")
            with shared["lock"]:
                shared["asset_path"] = current_final_shp
            time.sleep(1.5)
            return True
        push_log("[SYSTEM] 🚨 合成阶段被强行终止。")
        return False
    except Exception as e:
        push_log(f"[SYSTEM] 合成异常: {e}\n{traceback.format_exc()}")
        push_status("error", f"合成算法崩溃: {e}")
        return False


def run_index_pipeline_sync(ctx, shared, stop_event):
    """指数法潮滩提取（M1 mNDWI + M2 ACWI + 空间融合）。"""
    import index_engine

    logs_local = []

    def check_stop():
        return stop_event.is_set()

    def push_log(msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        logs_local.append(f"[{ts}] root@cstf: {msg}")
        with shared["lock"]:
            shared["log_lines"] = logs_local[-30:]
        print(msg)

    def push_progress(pct):
        with shared["lock"]:
            shared["progress"] = int(min(100, max(0, pct)))

    def push_status(kind, text):
        with shared["lock"]:
            shared["status"] = (kind, text)

    task = ctx["task"]
    root_dir = ctx["root_dir"]
    final_root = ctx["final_root"]
    points_shp = ctx["points_shp"]
    task_options = ctx["task_options"]

    actual_task = task
    for opt in task_options:
        if task in opt:
            actual_task = opt
            break

    if not actual_task or not root_dir:
        push_status("error", "❌ 未选择有效目标任务，或原始影像目录未配置。请在侧栏选择任务后再运行指数法推理。")
        return False

    input_dir = os.path.join(root_dir, actual_task)
    final_out_dir = os.path.join(final_root, actual_task)
    output_tif = os.path.join(final_out_dir, f"{actual_task}_Index_Final.tif")
    work_dir = os.path.join(final_out_dir, "index_work")

    cached = find_index_asset(actual_task)
    if cached and not ctx.get("force_rerun"):
        push_log(f"⚡ 指数法缓存命中: {os.path.basename(cached['file_path'])}")
        index_shp = os.path.join(final_out_dir, "Final_Intertidal_Flat.shp")
        if os.path.isfile(index_shp):
            _run_m5_phase(ctx, shared, index_shp, actual_task, None, None, push_log, check_stop)
            _run_e1_phase(ctx, shared, index_shp, actual_task, push_log, check_stop)
        push_status("success", "⚡ 发现已有指数法成果，直接加载")
        push_progress(100)
        with shared["lock"]:
            shared["asset_path"] = cached["file_path"]
        return True

    push_progress(0)
    push_status("info", "启动指数法推理…")
    push_log(f"INIT INDEX TASK: {actual_task}")

    if not os.path.exists(input_dir):
        push_status("error", f"❌ 找不到输入目录：{input_dir}")
        return False
    if not os.path.isfile(points_shp):
        push_status("error", f"❌ 找不到海洋种子点 SHP：{points_shp}")
        return False

    os.makedirs(final_out_dir, exist_ok=True)

    def on_status(msg):
        push_status("info", msg)

    try:
        result = index_engine.run_index_pipeline(
            input_dir=input_dir,
            output_tif=output_tif,
            points_shp=points_shp,
            work_dir=work_dir,
            push_log=push_log,
            push_progress=push_progress,
            stop_callback=check_stop,
        )
        if check_stop():
            push_status("warning", "任务已被手动中止。")
            return False
        if not result or not os.path.isfile(result):
            push_status("error", "指数法未生成有效结果文件。")
            return False
        register_index_asset(actual_task, result)
        index_shp = os.path.join(final_out_dir, "Final_Intertidal_Flat.shp")
        if os.path.isfile(index_shp):
            _run_m5_phase(ctx, shared, index_shp, actual_task, None, None, push_log, check_stop)
            _run_e1_phase(ctx, shared, index_shp, actual_task, push_log, check_stop)
        push_progress(100)
        push_status("success", "🎉 指数法潮滩提取完成！")
        with shared["lock"]:
            shared["asset_path"] = result
        return True
    except Exception as e:
        push_log(f"[SYSTEM] 指数法异常: {e}\n{traceback.format_exc()}")
        push_status("error", f"指数法失败: {e}")
        return False


def run_m4_download_sync(ctx, shared, stop_event):
    """M4 GEE 数据下载（Drive 提交或本地下载）。"""
    import m4_engine

    logs_local = []

    def check_stop():
        return stop_event.is_set()

    def push_log(msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        logs_local.append(f"[{ts}] root@cstf: {msg}")
        with shared["lock"]:
            shared["log_lines"] = logs_local[-30:]
        print(msg)

    def push_progress(pct):
        with shared["lock"]:
            shared["progress"] = int(min(100, max(0, pct)))

    def push_status(kind, text):
        with shared["lock"]:
            shared["status"] = (kind, text)

    cfg = ctx.get("m4") or {}
    push_progress(0)
    push_status("info", "正在连接 Google Earth Engine…")

    try:
        result = m4_engine.run_m4_download(
            roi_path=cfg["roi_path"],
            roi_name=cfg["roi_name"],
            start_date=cfg["start_date"],
            end_date=cfg["end_date"],
            export_to=cfg["export_to"],
            local_out_dir=cfg["local_out_dir"],
            bands=cfg.get("bands"),
            cloud_limit=int(cfg.get("cloud_limit", 60)),
            min_land_pct=float(cfg.get("min_land_pct", 5.0)),
            max_land_pct=float(cfg.get("max_land_pct", 95.0)),
            min_pixel_count=int(cfg.get("min_pixel_count", 1000)),
            drive_folder=cfg.get("drive_folder", "GEE_Downloads"),
            scale=int(cfg.get("scale", 10)),
            gee_proxy_url=(cfg.get("gee_proxy_url") or "").strip() or None,
            gee_project_id=(cfg.get("gee_project_id") or "").strip() or None,
            push_log=push_log,
            push_progress=push_progress,
            stop_callback=check_stop,
        )
        if check_stop():
            push_status("warning", "影像获取已中止。")
            return False
        if not result:
            return False
        with shared["lock"]:
            shared["m4_result"] = result
        n = result["image_count"]
        if result["export_to"] == "drive":
            push_status(
                "success",
                f"已提交 {n} 个 Drive 任务 → 文件夹「{result['drive_folder']}」。请在影像平台任务列表 / 云盘查看。",
            )
        else:
            push_status("success", f"本地下载完成 {n} 景 → {result['local_out_dir']}")
        push_progress(100)
        return True
    except Exception as e:
        push_log(f"[SYSTEM] M4 异常: {e}\n{traceback.format_exc()}")
        push_status("error", f"影像获取失败: {e}")
        return False


def _pipeline_worker_entry(ctx, shared, stop_event):
    ok = False
    try:
        mode = ctx.get("mode", "dl")
        if mode == "m4":
            ok = run_m4_download_sync(ctx, shared, stop_event)
        elif mode == "index":
            ok = run_index_pipeline_sync(ctx, shared, stop_event)
        else:
            ok = run_pipeline_sync(ctx, shared, stop_event)
    except Exception as e:
        tb_lines = traceback.format_exc().split("\n")[:25]
        with shared["lock"]:
            lines = list(shared.get("log_lines") or [])
            lines.append(f"[CRASH] {e}")
            lines.extend(tb_lines)
            shared["log_lines"] = lines[-30:]
            shared["status"] = ("error", str(e))
        ok = False
    finally:
        with shared["lock"]:
            shared["success"] = ok
            shared["done"] = True


def _workflow_worker_entry(ctx, shared, stop_event):
    """端到端潮滩分析 Workflow 后台线程：只调用 workflow_orchestrator（复用子闭环）。

    不调用 Streamlit API；只写 shared / 文件。任何一步失败不伪造成功。
    """
    import time as _time

    ok = False
    try:
        import workflow_orchestrator as _wo

        wf = ctx.get("workflow_plan")
        if not isinstance(wf, dict) or not wf.get("workflow_id"):
            with shared["lock"]:
                shared["status"] = ("error", "一键潮滩分析计划未就绪，无法执行。")
            return

        def push_log(msg):
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            with shared["lock"]:
                lines = list(shared.get("log_lines") or [])
                lines.append(f"[{ts}] root@workflow: {msg}")
                shared["log_lines"] = lines[-40:]
            print(msg)

        def push_progress(pct):
            with shared["lock"]:
                shared["progress"] = int(min(100, max(0, pct)))

        def push_status(kind, text):
            with shared["lock"]:
                shared["status"] = (kind, text)

        push_progress(2)
        push_status("info", "潮滩分析 Workflow 启动…")
        push_log(f"WORKFLOW: {wf.get('workflow_id')} | TASK: {wf.get('task_id')}")

        exec_ctx = {
            "aoi": ctx.get("aoi"),
            "root_dir": ctx.get("root_dir"),
            "final_root": ctx.get("final_root"),
            "mask_root": ctx.get("mask_root"),
            "model_path": ctx.get("model_path"),
            "shp_path": ctx.get("shp_path"),
            "e1_data_root": ctx.get("e1_data_root"),
            "e1_reference": ctx.get("e1_reference"),
            "registry": ctx.get("registry"),
            "registry_path": ctx.get("registry_path"),
            "report_output_dir": ctx.get("report_output_dir"),
            "baseline_task": ctx.get("baseline_task"),
            "push_progress": push_progress,
        }
        result = _wo.run_analysis_workflow(
            wf, exec_ctx=exec_ctx, push_log=push_log, stop_event=stop_event,
        )
        with shared["lock"]:
            shared["workflow_result"] = result
        final_status = result.get("status")
        ok = final_status in ("SUCCEEDED", "COMPLETED_WITH_WARNINGS")
        summary = result.get("summary") or ""
        if ok:
            push_status(
                "success" if final_status == "SUCCEEDED" else "warning",
                f"一键潮滩分析完成 · {uil.get_status_label(final_status)}",
            )
        else:
            push_status("error", f"一键潮滩分析未完成 · {uil.get_status_label(final_status)}")
        push_log(summary.replace("\n", " | "))
        push_progress(100)
        return
    except Exception as e:
        tb_lines = traceback.format_exc().split("\n")[:25]
        with shared["lock"]:
            lines = list(shared.get("log_lines") or [])
            lines.append(f"[CRASH] {e}")
            lines.extend(tb_lines)
            shared["log_lines"] = lines[-40:]
            shared["status"] = ("error", str(e))
        ok = False
    finally:
        with shared["lock"]:
            shared["success"] = ok
            shared["done"] = True


def _inference_worker_entry(ctx, shared, stop_event):
    """本地潮滩推理可信执行闭环后台线程（只写 shared / 文件，不调用 Streamlit API）。

    顺序：真实推理 → 真实后处理 → 磁盘校验 → 验证通过才登记资产。
    任何一步失败：不登记、不伪报完成；shared['inference_result'] 保留真实失败信息。
    """
    import time as _time

    ok = False
    try:
        import inference_agent_loop as ial

        plan = ctx.get("inference_plan")
        if not isinstance(plan, dict) or not plan.get("ready"):
            with shared["lock"]:
                shared["status"] = ("error", "推理计划未就绪，无法执行。")
            return

        task_id = plan.get("task_id") or ctx.get("task") or "unknown"

        def check_stop():
            return stop_event.is_set()

        def push_log(msg):
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            with shared["lock"]:
                lines = list(shared.get("log_lines") or [])
                lines.append(f"[{ts}] root@cstf: {msg}")
                shared["log_lines"] = lines[-30:]
            print(msg)

        def push_progress(pct):
            with shared["lock"]:
                shared["progress"] = int(min(100, max(0, pct)))

        def push_status(kind, text):
            with shared["lock"]:
                shared["status"] = (kind, text)

        started = _time.time()
        push_status("info", "正在启动潮滩智能提取…")
        push_log(f"PLAN: {plan.get('plan_id')} | TASK: {task_id} | "
                 f"P={plan.get('prob_threshold')} C={plan.get('count_threshold')} | "
                 f"DEVICE={plan.get('device') or plan.get('device_policy')}")

        result = ial.execute_local_inference(
            plan,
            stop_event=stop_event,
            push_log=push_log,
            push_progress=push_progress,
        )
        if not result or result.get("success") is not True:
            err = (result or {}).get("error") or "提取失败"
            push_status("error", f"❌ {err}")
            with shared["lock"]:
                shared["inference_result"] = result or {}
            return

        push_status("info", "提取完成，正在校验磁盘成果…")
        verification = ial.verify_inference_outputs(plan, result, started_at=started)
        if not verification or verification.get("ok") is not True:
            failed = [c.get("name") for c in (verification or {}).get("checks") or []
                      if not c.get("passed")]
            push_status("error", f"❌ 成果校验未通过: {', '.join(failed) or '未知'}")
            with shared["lock"]:
                shared["inference_result"] = result
                shared["inference_verification"] = verification or {}
            return

        asset_id = ial.register_inference_asset(plan, result, verification)
        if not asset_id:
            push_status("error", "❌ 校验通过但资产登记失败（未登记成果）。")
            with shared["lock"]:
                shared["inference_result"] = result
                shared["inference_verification"] = verification
            return

        final_tif = (result.get("outputs") or {}).get("final_tif") or ""
        final_shp = (result.get("outputs") or {}).get("final_shp") or ""
        push_log(f"✅ 提取闭环完成 | asset_id={asset_id} | Final TIF={os.path.basename(str(final_tif))} | "
                 f"Final SHP={os.path.basename(str(final_shp))}")
        push_status("success", "🎉 潮滩智能提取完成：成果已验证并登记。")
        with shared["lock"]:
            shared["inference_result"] = result
            shared["inference_verification"] = verification
            shared["asset_id"] = asset_id
            # 绝对路径（供地图加载与资产登记使用）
            _abs_map = os.path.abspath(str(final_shp or final_tif or ""))
            shared["asset_path"] = _abs_map if os.path.isfile(_abs_map) else None
            shared["progress"] = 100
        ok = True
    except Exception as e:
        tb_lines = traceback.format_exc().split("\n")[:25]
        with shared["lock"]:
            lines = list(shared.get("log_lines") or [])
            lines.append(f"[CRASH] {e}")
            lines.extend(tb_lines)
            shared["log_lines"] = lines[-30:]
            shared["status"] = ("error", f"推理线程异常: {e}")
        ok = False
    finally:
        with shared["lock"]:
            shared["success"] = ok
            shared["done"] = True


def _gee_worker_entry(ctx, shared, stop_event):
    """GEE 影像下载可信执行闭环后台线程（B 阶段）。

    顺序：真实 m4_engine 下载 → 磁盘/远程校验 → 验证通过才登记 dataset asset。
    任何一步失败：不登记、不伪报完成；shared['gee_result'] 保留真实失败信息。
    """
    import time as _time

    ok = False
    try:
        import gee_agent_loop as gal

        plan = ctx.get("gee_plan")
        if not isinstance(plan, dict) or not plan.get("ready"):
            with shared["lock"]:
                shared["status"] = ("error", "GEE 下载计划未就绪，无法执行。")
            return

        task_id = plan.get("task_id") or ctx.get("task") or "unknown"

        def check_stop():
            return stop_event.is_set()

        def push_log(msg):
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            with shared["lock"]:
                lines = list(shared.get("log_lines") or [])
                lines.append(f"[{ts}] root@cstf: {msg}")
                shared["log_lines"] = lines[-30:]
            print(msg)

        def push_progress(pct):
            with shared["lock"]:
                shared["progress"] = int(min(100, max(0, pct)))

        def push_status(kind, text):
            with shared["lock"]:
                shared["status"] = (kind, text)

        started = _time.time()
        push_status("info", "正在执行影像获取（可信执行闭环）…")
        push_log(f"PLAN: {plan.get('plan_id')} | TASK: {task_id} | "
                 f"BANDS={plan.get('bands')} | EXPORT={plan.get('export_to')} | "
                 f"COLLECTION={plan.get('collection')}")

        result = gal.execute_gee_download(
            plan,
            stop_event=stop_event,
            push_log=push_log,
            push_progress=push_progress,
        )
        if not result or result.get("success") is not True:
            err = (result or {}).get("error") or "影像获取失败"
            push_status("error", f"❌ {err}")
            with shared["lock"]:
                shared["gee_result"] = result or {}
            return

        push_status("info", "下载结束，正在校验成果…")
        verification = gal.verify_gee_outputs(plan, result, started_at=started)
        if not verification or verification.get("ok") is not True:
            failed = [c.get("name") for c in (verification or {}).get("checks") or []
                      if not c.get("passed")]
            push_status("error", f"❌ 成果校验未通过: {', '.join(failed) or '未知'}")
            with shared["lock"]:
                shared["gee_result"] = result
                shared["gee_verification"] = verification or {}
            return

        asset_id = gal.register_gee_dataset_asset(plan, result, verification)
        if not asset_id:
            push_status("error", "❌ 校验通过但资产登记失败（未登记数据集）。")
            with shared["lock"]:
                shared["gee_result"] = result
                shared["gee_verification"] = verification
            return

        n_tifs = len(verification.get("local_tifs") or [])
        push_log(f"✅ 影像获取闭环完成 | dataset_id={asset_id} | "
                 f"scene_count={result.get('metrics', {}).get('scene_count')} | "
                 f"local_tifs={n_tifs}")
        push_status("success", "🎉 影像获取完成：影像数据已验证并登记。提取不会自动启动。")
        with shared["lock"]:
            shared["gee_result"] = result
            shared["gee_verification"] = verification
            shared["dataset_id"] = asset_id
            shared["progress"] = 100
        ok = True
    except Exception as e:
        tb_lines = traceback.format_exc().split("\n")[:25]
        with shared["lock"]:
            lines = list(shared.get("log_lines") or [])
            lines.append(f"[CRASH] {e}")
            lines.extend(tb_lines)
            shared["log_lines"] = lines[-30:]
            shared["status"] = ("error", f"GEE 下载线程异常: {e}")
        ok = False
    finally:
        with shared["lock"]:
            shared["success"] = ok
            shared["done"] = True


# =======================================================
#  1. 页面全局配置与状态机初始化 (Session State)
# =======================================================
st.set_page_config(
    page_title="CSTF-Cloud | 遥感智能监测平台",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 🌟 初始化系统状态：控制运行/中断的红绿灯
if "is_running" not in st.session_state:
    st.session_state.is_running = False
if "stop_requested" not in st.session_state:
    st.session_state.stop_requested = False
if "pending_task" not in st.session_state:
    st.session_state.pending_task = None
if "asset_override" not in st.session_state:
    st.session_state.asset_override = None
if "assets_scanned" not in st.session_state:
    st.session_state.assets_scanned = False
if "_param_key" not in st.session_state:
    st.session_state._param_key = None
if "asset_just_loaded" not in st.session_state:
    st.session_state.asset_just_loaded = False
if "executing_pipeline" not in st.session_state:
    st.session_state.executing_pipeline = False
if "pipeline_log_snapshot" not in st.session_state:
    st.session_state.pipeline_log_snapshot = []
if "pipeline_progress_value" not in st.session_state:
    st.session_state.pipeline_progress_value = 0
if "pipeline_thread_started" not in st.session_state:
    st.session_state.pipeline_thread_started = False

# 🌟 初始化地图状态：控制视角飞跃
if "map_center" not in st.session_state:
    st.session_state.map_center = [35.0, 105.0]
if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = 3
if "_map_view_synced_for" not in st.session_state:
    st.session_state._map_view_synced_for = None
if "result_overlay_opacity_pct" not in st.session_state:
    st.session_state.result_overlay_opacity_pct = 50
if "globe_show_e1" not in st.session_state:
    st.session_state.globe_show_e1 = True
if "use_2d_map_fallback" not in st.session_state:
    st.session_state.use_2d_map_fallback = False
if "_globe_tile_clients" not in st.session_state:
    st.session_state._globe_tile_clients = {}
if "_asset_pinned" not in st.session_state:
    st.session_state._asset_pinned = False
if "_globe_rev" not in st.session_state:
    st.session_state._globe_rev = 0
if "_globe_iframe_cache_sig" not in st.session_state:
    st.session_state._globe_iframe_cache_sig = None
if "_globe_iframe_url" not in st.session_state:
    st.session_state._globe_iframe_url = None
if "_globe_warn_token" not in st.session_state:
    st.session_state._globe_warn_token = None
if "m5_report" not in st.session_state:
    st.session_state.m5_report = None
if "e1_report" not in st.session_state:
    st.session_state.e1_report = None

from agent_command_bridge import (
    init_ui_session_defaults,
    process_agent_reply,
    build_agent_sidebar_context,
    flush_pending_agent_commands,
    queue_agent_command,
    _aoi_state_to_dict,
)

import map_protocol as _map_proto


# ---- Phase D: 地图 AOI 双向交互（Cesium 绘制 → server → 校验回声 → Copilot 上下文）----
def _send_globe_message(payload):
    """向已加载的 Cesium iframe 发送任意 CSTF_MAP_V1 消息（同 FLY 的 targetOrigin 收紧逻辑）。"""
    import json as _json

    _msg_js = _json.dumps(payload, ensure_ascii=False)
    try:
        components.html(
            f"""
<script>
(() => {{
  const win = window.parent || window;
  const doc = win.document;
  const msg = {_msg_js};
  let origin = "*";
  try {{
    const iframes = doc.querySelectorAll("iframe");
    iframes.forEach((ifr) => {{
      const src = ifr.getAttribute("src") || "";
      if (src.indexOf("/globe") >= 0 || src.indexOf(":8765") >= 0) {{
        try {{ origin = new URL(src, win.location.href).origin; }} catch (e) {{}}
      }}
    }});
  }} catch (e) {{}}
  const send = () => {{
    const iframes = doc.querySelectorAll("iframe");
    let sent = false;
    iframes.forEach((ifr) => {{
      const src = ifr.getAttribute("src") || "";
      if (!src) return;
      if (src.indexOf("/globe") >= 0 || src.indexOf(":8765") >= 0) {{
        try {{
          ifr.contentWindow.postMessage(msg, origin);
          sent = true;
        }} catch (e) {{}}
      }}
    }});
    return sent;
  }};
  if (!send()) {{
    let n = 0;
    const t = setInterval(() => {{
      if (send() || ++n > 40) clearInterval(t);
    }}, 120);
  }}
}})();
</script>
            """,
            height=0,
        )
    except Exception:
        pass


def _poll_aoi_messages():
    """消费 Cesium iframe 的 AOI 消息：校验 → 回声图层 → 注入 Copilot 上下文。"""
    try:
        import aoi_context as _aoi_ctx
        import aoi_map_bridge as _aoi_bridge
    except Exception:
        return
    try:
        import globe_server as _gsrv

        _since = int(st.session_state.get("_aoi_poll_seq") or 0)
        _res = _gsrv.take_aoi_pending(_since)
    except Exception:
        return
    if _res.get("last_seq") is not None:
        st.session_state["_aoi_poll_seq"] = int(_res.get("last_seq") or _since)
    for _m in _res.get("messages") or []:
        _kind = _m.get("kind") or "selected"
        try:
            if _kind == "cleared":
                _r = _aoi_bridge.process_aoi_cleared(st.session_state)
            else:
                _r = _aoi_bridge.process_aoi_selected(
                    st.session_state,
                    geometry=_m.get("geometry"),
                    source=_m.get("source") or "map_polygon",
                    label=_m.get("label"),
                )
        except Exception as _ae:
            _r = {"ok": False, "errors": [str(_ae)], "echo": None}
        _echo = _r.get("echo")
        if isinstance(_echo, list):
            for _e in _echo:
                if isinstance(_e, dict):
                    _send_globe_message(_e)
        elif isinstance(_echo, dict):
            _send_globe_message(_echo)
        if not _r.get("ok"):
            st.warning("研究区域无效：" + "; ".join(_r.get("errors") or []))


def _aoi_sidebar_context():
    """AOI 摘要（供 Agent System Prompt）：仅包含紧凑摘要 + 推荐，不含 GeoJSON。"""
    try:
        import aoi_context as _aoi_ctx
        import aoi_map_bridge as _aoi_bridge
    except Exception:
        return ""
    _aoi = st.session_state.get("_active_aoi")
    if not _aoi:
        return ""
    try:
        _cap = st.session_state.get("_capability_reg")
        _caps = {}
        if _cap is not None:
            _snap = _cap.snapshot_for_agent()
            _caps = {cid: v.get("status") for cid, v in _snap.items()}
        return _aoi_bridge.aoi_recommendation_text(_aoi, _caps)
    except Exception:
        return ""


# ---- Phase C: 统一任务执行时间线（惰性单例 + 原子账本）----
def _get_task_timeline():
    tl = st.session_state.get("_task_timeline")
    if tl is None:
        import task_timeline as _tt

        _ledger = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data", "timeline_ledger.json"
        )
        tl = _tt.TimelineStore(ledger_path=_ledger)
        try:
            tl.load()
        except Exception:
            pass
        st.session_state._task_timeline = tl
    return tl


def _tl_add(task_id, phase, message, *, status="PENDING", plan_id=None, tool=None,
            progress=None, details=None, artifacts=None, error=None):
    """记录时间线事件并原子落盘（失败静默，不阻塞主流程）。"""
    try:
        tl = _get_task_timeline()
        ev = tl.add(
            task_id, phase, message, status=status, plan_id=plan_id,
            tool=tool, progress=progress, details=details or {},
            artifacts=artifacts or [], error=error,
        )
        try:
            tl.save()
        except Exception:
            pass
        return ev
    except Exception:
        return None


def _tl_update(event_id, *, status=None, progress=None, message=None, error=None):
    try:
        tl = _get_task_timeline()
        _ok, ev = tl.update(
            event_id, status=status, progress=progress, message=message, error=error
        )
        try:
            tl.save()
        except Exception:
            pass
        return ev
    except Exception:
        return None


init_ui_session_defaults(st.session_state)
_agent_flush = flush_pending_agent_commands(st.session_state)
if _agent_flush.applied and _agent_flush.errors:
    for _afe in _agent_flush.errors:
        st.warning(_afe)
if _agent_flush.applied and _agent_flush.m5_plan_text:
    st.session_state._m5_plan_notice = _agent_flush.m5_plan_text
    # 将可验证计划写入对话，便于用户确认
    _msgs = list(st.session_state.get("messages") or [])
    _last = (_msgs[-1].get("content") if _msgs else "") or ""
    if "潮滩变化分析 · 执行计划" not in str(_last):
        _msgs.append({"role": "assistant", "content": _agent_flush.m5_plan_text})
        st.session_state.messages = _msgs
if _agent_flush.applied and _agent_flush.action_type == "run_m5":
    try:
        st.toast("潮滩变化分析已确认，正在执行…", icon="🛰️")
    except Exception:
        pass
if _agent_flush.applied and _agent_flush.e1_plan_text:
    st.session_state._e1_plan_notice = _agent_flush.e1_plan_text
    _msgs_e1 = list(st.session_state.get("messages") or [])
    _last_e1 = (_msgs_e1[-1].get("content") if _msgs_e1 else "") or ""
    if "潮滩精度评价 · 执行计划" not in str(_last_e1):
        _msgs_e1.append({"role": "assistant", "content": _agent_flush.e1_plan_text})
        st.session_state.messages = _msgs_e1
if _agent_flush.applied and _agent_flush.action_type == "run_e1":
    try:
        st.toast("潮滩精度评价已确认，正在执行…", icon="📊")
    except Exception:
        pass
if _agent_flush.applied and _agent_flush.inference_plan_text:
    st.session_state._inference_plan_notice = _agent_flush.inference_plan_text
    _msgs_inf = list(st.session_state.get("messages") or [])
    _last_inf = (_msgs_inf[-1].get("content") if _msgs_inf else "") or ""
    if "潮滩智能提取 · 执行计划" not in str(_last_inf):
        _msgs_inf.append({"role": "assistant", "content": _agent_flush.inference_plan_text})
        st.session_state.messages = _msgs_inf
if _agent_flush.applied and _agent_flush.action_type == "run_inference":
    try:
        st.toast("潮滩智能提取已确认，正在执行…", icon="🌊")
    except Exception:
        pass
if _agent_flush.applied and _agent_flush.gee_plan_text:
    st.session_state._gee_plan_notice = _agent_flush.gee_plan_text
    _msgs_gee = list(st.session_state.get("messages") or [])
    _last_gee = (_msgs_gee[-1].get("content") if _msgs_gee else "") or ""
    if "获取卫星影像 · 执行计划" not in str(_last_gee):
        _msgs_gee.append({"role": "assistant", "content": _agent_flush.gee_plan_text})
        st.session_state.messages = _msgs_gee
if _agent_flush.applied and _agent_flush.action_type == "run_gee_download":
    try:
        st.toast("获取卫星影像已确认，正在执行…", icon="🛰️")
    except Exception:
        pass

# =======================================================
#  🌟 AIE 风格 CSS 深度定制
# =======================================================
st.markdown("""
<style>
    [data-testid="stAppViewContainer"] { background-color: #0e0e0e; }
    [data-testid="stHeader"] { background-color: rgba(14, 14, 14, 0); } 
    h1, h2, h3, p, span, div { color: #cccccc !important; }
    [data-testid="stSidebar"] { background-color: #1b1b1d !important; border-right: 1px solid #333333; }
    [data-testid="stSidebar"] * { color: #cccccc !important; }
    .stTextInput>div>div>input, .stSelectbox>div>div>div { background-color: #252526 !important; color: #eeeeee !important; border: 1px solid #3d3d3d !important; border-radius: 2px !important; }
    .stTextInput>div>div>input:focus, .stSelectbox>div>div>div:focus { border-color: #3A62D7 !important; box-shadow: none !important; }
    div.stButton > button { border-radius: 2px !important; font-weight: 600 !important; letter-spacing: 1px; padding: 0.5rem 1rem !important; }
    [data-testid="stExpander"] { background-color: #1b1b1d !important; border: 1px solid #333333 !important; border-radius: 2px !important; }
    [data-testid="stVerticalBlock"] > div.element-container > div.stMarkdown > div > pre { background-color: #000000 !important; border: 1px solid #333333 !important; color: #00ff00 !important; }
    .main-title { font-size: 1.8rem; font-weight: 600; color: #eeeeee !important; margin-bottom: 0px; border-left: 4px solid #3A62D7; padding-left: 10px;}
    .sub-title { font-size: 0.9rem; color: #888888 !important; margin-bottom: 15px; margin-top: 5px; padding-left: 14px;}
    .stProgress > div > div > div > div { background-color: #3A62D7 !important; }
    .msg-role {
        display: inline-block;
        padding: 0.1rem 0.45rem;
        border-radius: 999px;
        font-size: 0.74rem;
        font-weight: 700;
        margin-bottom: 0.35rem;
        letter-spacing: 0.2px;
    }
    .msg-role-user {
        background: #1f355a;
        color: #cfe1ff !important;
        border: 1px solid #3a62d7;
    }
    .msg-role-assistant {
        background: #23452f;
        color: #cbf1d4 !important;
        border: 1px solid #4ea56a;
    }
    [data-testid="stChatMessage"] {
        background: linear-gradient(180deg, #141a25 0%, #10151f 100%);
        border: 1px solid #2c3649;
        border-radius: 12px;
        padding: 0.45rem 0.65rem;
        margin-bottom: 0.45rem;
    }
    [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p {
        color: #e6ecf6 !important;
        line-height: 1.5;
    }
    :root {
        --workbench-h: calc(100vh - 3.5rem);
    }
    /* 工作台固定视口：禁止整页滚动（不用 position:fixed，避免上次压缩问题） */
    html, body {
        overflow: hidden !important;
        height: 100% !important;
        overscroll-behavior: none !important;
    }
    .stApp,
    [data-testid="stAppViewContainer"],
    section[data-testid="stMain"],
    div[data-testid="stMainBlockContainer"] {
        overflow: hidden !important;
        max-height: 100vh !important;
        overscroll-behavior: none !important;
    }
    div[data-testid="stMainBlockContainer"] > div[data-testid="stVerticalBlock"]:has([data-testid="stHorizontalBlock"]:has(.cockpit-map-col)) {
        height: var(--workbench-h) !important;
        max-height: var(--workbench-h) !important;
        overflow: hidden !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.cockpit-map-col) {
        height: var(--workbench-h) !important;
        max-height: var(--workbench-h) !important;
        overflow: hidden !important;
        align-items: stretch !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.cockpit-map-col) > div[data-testid="stColumn"] {
        height: var(--workbench-h) !important;
        max-height: var(--workbench-h) !important;
        overflow: hidden !important;
        align-self: stretch !important;
    }
    div[data-testid="stColumn"]:has(.cockpit-map-col) > div[data-testid="stVerticalBlock"] {
        height: 100% !important;
        max-height: var(--workbench-h) !important;
        overflow: hidden !important;
    }
    .cockpit-map-col,
    .cockpit-chat-anchor,
    .cockpit-copilot-zone-start {
        display: none !important;
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        border: none !important;
        overflow: hidden !important;
    }
    section[data-testid="stMain"] > div,
    section[data-testid="stMain"] .block-container,
    div[data-testid="stMainBlockContainer"] {
        padding-top: 0.25rem;
        padding-bottom: 0.25rem;
        padding-left: 0.5rem;
        padding-right: 0.5rem;
        max-width: 100% !important;
        width: 100% !important;
    }
    /* 三维地球 iframe 撑满主区域（仅地图列） */
    div[data-testid="stColumn"]:has(.cockpit-map-col) [data-testid="stIFrame"],
    div[data-testid="stColumn"]:has(.cockpit-map-col) [data-testid="stHtml"],
    div[data-testid="stColumn"]:has(.cockpit-map-col) div[data-testid="stElementContainer"]:has(iframe.yy-globe-frame) {
        width: 100% !important;
        max-width: 100% !important;
    }
    div[data-testid="stColumn"]:has(.cockpit-map-col) iframe.yy-globe-frame,
    div[data-testid="stColumn"]:has(.cockpit-map-col) [data-testid="stIFrame"] > iframe {
        border: none !important;
        width: 100% !important;
        max-width: 100% !important;
        height: var(--workbench-h) !important;
        min-height: var(--workbench-h) !important;
        max-height: var(--workbench-h) !important;
        display: block !important;
        background: #0a1628;
    }
    div[data-testid="stColumn"]:has(.cockpit-map-col) [data-testid="stIFrame"] {
        height: var(--workbench-h) !important;
        min-height: var(--workbench-h) !important;
        max-height: var(--workbench-h) !important;
    }
    .command-deck {
        border-top: 1px solid #333;
        padding-top: 12px;
        margin-top: 4px;
    }
    .command-deck-side {
        border-left: 1px solid #2a3548;
        padding-left: 10px;
        margin-left: 2px;
        height: 100%;
    }
    /* 右侧：上状态日志 / 中对话 / 底输入框（Streamlit 1.3x 使用 stLayoutWrapper） */
    div[data-testid="stColumn"]:has(.command-deck-side) > div[data-testid="stVerticalBlock"] {
        display: flex !important;
        flex-direction: column !important;
        height: var(--workbench-h) !important;
        max-height: var(--workbench-h) !important;
        overflow: hidden !important;
        gap: 0.3rem !important;
    }
    div[data-testid="stColumn"]:has(.command-deck-side) > div[data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has([data-testid="stProgress"]),
    div[data-testid="stColumn"]:has(.command-deck-side) > div[data-testid="stVerticalBlock"] > [data-testid="stElementContainer"]:has(.deck-section-title) {
        flex: 0 0 auto !important;
    }
    div[data-testid="stColumn"]:has(.command-deck-side) > div[data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has([data-testid="stChatMessage"]):not(:has([data-testid="stForm"])) {
        flex: 1 1 auto !important;
        min-height: 140px !important;
        overflow-x: hidden !important;
        overflow-y: auto !important;
    }
    div[data-testid="stColumn"]:has(.command-deck-side) > div[data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has([data-testid="stForm"] input[aria-label="chat_input"]) {
        flex: 0 0 auto !important;
        margin-top: auto !important;
    }
    div[data-testid="stColumn"]:has(.command-deck-side) > div[data-testid="stVerticalBlock"] > [data-testid="stElementContainer"]:has([data-testid="stVerticalBlockBorderWrapper"] [data-testid="stChatMessage"]) {
        flex: 1 1 auto !important;
        min-height: 140px !important;
        overflow: hidden !important;
        display: flex !important;
        flex-direction: column !important;
    }
    div[data-testid="stColumn"]:has(.command-deck-side) [data-testid="stVerticalBlockBorderWrapper"]:has([data-testid="stChatMessage"]) {
        flex: 1 1 auto !important;
        min-height: 0 !important;
        overflow-y: auto !important;
        overflow-x: hidden !important;
    }
    div[data-testid="stColumn"]:has(.command-deck-side) > div[data-testid="stVerticalBlock"] > [data-testid="stElementContainer"]:has([data-testid="stForm"] input[aria-label="chat_input"]) {
        flex: 0 0 auto !important;
        margin-top: auto !important;
    }
    div[data-testid="stColumn"]:has(.command-deck-side) > div[data-testid="stVerticalBlock"] > [data-testid="stElementContainer"]:has(iframe),
    div[data-testid="stColumn"]:has(.command-deck-side) > div[data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has(iframe) {
        flex: 0 0 0 !important;
        height: 0 !important;
        min-height: 0 !important;
        overflow: hidden !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    div[data-testid="stColumn"]:has(.command-deck-side) {
        background: linear-gradient(180deg, #0f141c 0%, #0b1018 100%);
        border-radius: 8px;
        padding: 8px 6px 8px 4px !important;
    }
    div[data-testid="stColumn"]:has(.cockpit-map-col) {
        padding-right: 4px !important;
    }
    .cstf-log-panel-host-marker,
    div[data-testid="stElementContainer"]:has(> .cstf-copilot-dock:empty),
    div[data-testid="stElementContainer"]:has(> .cstf-chat-compose-host:empty) {
        display: none !important;
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
    }
    /* CSTF-Copilot 输入区：文字在上，下方 + 号附件 */
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) {
        border: 1px solid #2e384c !important;
        border-radius: 18px !important;
        padding: 10px 12px 8px !important;
        background: linear-gradient(180deg, #151b28 0%, #121720 100%) !important;
        margin-top: 4px !important;
    }
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) [data-testid="stHorizontalBlock"] {
        align-items: center !important;
        gap: 0.35rem !important;
    }
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) input[aria-label="chat_input"] {
        border-radius: 14px !important;
        border: 1px solid #2a3548 !important;
        background: #0c1018 !important;
        color: #e8edf7 !important;
        padding: 11px 14px !important;
        font-size: 0.92rem !important;
        min-height: 2.65rem !important;
    }
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) input[aria-label="chat_input"]::placeholder {
        color: #6b7a94 !important;
    }
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) input[aria-label="chat_input"]:focus {
        border-color: #4a6cf0 !important;
        box-shadow: 0 0 0 1px rgba(74, 108, 240, 0.35) !important;
    }
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) [data-testid="stFormSubmitButton"] button {
        border-radius: 50% !important;
        width: 2.65rem !important;
        height: 2.65rem !important;
        min-width: 2.65rem !important;
        padding: 0 !important;
        font-size: 1.05rem !important;
        background: #2a3f7a !important;
        border: 1px solid #3d56a8 !important;
        color: #e8eeff !important;
    }
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) [data-testid="stFormSubmitButton"] button:hover {
        background: #3552a0 !important;
        border-color: #5a7fd4 !important;
    }
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) [data-testid="stFileUploader"] {
        margin: 0 !important;
        padding: 0 !important;
        min-height: 0 !important;
    }
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) [data-testid="stFileUploaderDropzone"],
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) [data-testid="stFileUploader"] > label,
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) [data-testid="stFileUploader"] section,
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) [data-testid="stFileUploader"] small {
        display: none !important;
    }
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) [data-testid="stFileUploader"] input[type="file"] {
        position: absolute !important;
        width: 1px !important;
        height: 1px !important;
        opacity: 0 !important;
        overflow: hidden !important;
        clip: rect(0, 0, 0, 0) !important;
    }
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) .cstf-attach-bar {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 8px;
        padding-left: 2px;
        min-height: 32px;
    }
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) .cstf-plus-btn {
        width: 32px;
        height: 32px;
        border-radius: 50%;
        border: 1px solid #3d4a63;
        background: transparent;
        color: #d0daf0;
        font-size: 1.4rem;
        font-weight: 300;
        line-height: 1;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 0;
        flex-shrink: 0;
        transition: background 0.15s, border-color 0.15s;
    }
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) .cstf-plus-btn:hover {
        background: #1e2838;
        border-color: #5a6d92;
        color: #ffffff;
    }
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) .cstf-attach-name {
        font-size: 0.78rem;
        color: #8fa3c4;
        max-width: 220px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    div[data-testid="stForm"]:has(input[aria-label="chat_input"]) .cstf-attach-hint {
        font-size: 0.72rem;
        color: #5c6b82;
    }
    .deck-section-title {
        font-size: 0.95rem !important;
        font-weight: 600 !important;
        color: #d0d0d0 !important;
        margin-bottom: 6px !important;
        border-left: 3px solid #3A62D7;
        padding-left: 8px;
    }
    .header-badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 600;
        border: 1px solid #3d3d3d;
        background: #1b1b1d;
        color: #aaa !important;
    }
    .header-badge-running {
        border-color: #3A62D7;
        color: #8ab4ff !important;
        background: #1a2540;
    }
</style>
""", unsafe_allow_html=True)

# =======================================================
#  2. 侧边栏：任务管理中心
# =======================================================
with st.sidebar:
    sbui.inject_sidebar_css()
    map_display_path = None

    _wf_options = ["潮滩推理", "GEE 数据下载"]
    if st.session_state.ui_workflow not in _wf_options:
        st.session_state.ui_workflow = "潮滩推理"
    workflow = st.radio(
        "工作台",
        _wf_options,
        format_func=lambda x: "潮滩智能提取" if x == "潮滩推理" else "获取卫星影像",
        horizontal=True,
        key="ui_workflow",
        help="潮滩智能提取：用模型或指数法从本地影像提取潮滩；获取卫星影像：从影像平台筛选并导出 Sentinel-2 数据。",
    )
    use_gee_download = st.session_state.ui_workflow == "GEE 数据下载"

    sbui.section("任务与数据")
    with st.container(border=True):
        root_dir = st.text_input("原始影像目录", key="ui_root_dir")

        task_options = []
        if os.path.exists(root_dir):
            sub_dirs = [d for d in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, d))]
            task_options = sorted(sub_dirs)

        if not task_options:
            sbui.hint("未发现可用任务", "warn")
            selected_task = None
        else:
            _sel = st.session_state.get("ui_selected_task")
            _task_opts = list(task_options)
            if _sel and _sel not in _task_opts:
                _task_opts = [_sel] + _task_opts
            if st.session_state.get("ui_selected_task") not in _task_opts:
                st.session_state.ui_selected_task = task_options[0]
            selected_task = st.selectbox("目标任务", options=_task_opts, key="ui_selected_task")
            if st.session_state.get("_last_selected_task") != selected_task:
                st.session_state._asset_pinned = False
                st.session_state.asset_override = None
                st.session_state._map_view_synced_for = None
            st.session_state._last_selected_task = selected_task
            sbui.hint(f"当前任务 · {selected_task}", "ok")

    _default_aoi = r"E:\Data\CHINA_tf_city\china_costal.shp"

    def _ui_to_date(key: str, fallback: datetime.date) -> datetime.date:
        v = st.session_state.get(key, fallback)
        if isinstance(v, datetime.date):
            return v
        try:
            return datetime.datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return fallback

    st.session_state.ui_m4_start_date = _ui_to_date("ui_m4_start_date", datetime.date(2020, 1, 1))
    st.session_state.ui_m4_end_date = _ui_to_date("ui_m4_end_date", datetime.date(2020, 1, 31))

    m4_roi_path = st.session_state.get("ui_m4_roi_path") or _default_aoi
    m4_roi_name = st.session_state.get("ui_m4_roi_name") or ""
    m4_start_date = st.session_state.ui_m4_start_date
    m4_end_date = st.session_state.ui_m4_end_date
    m4_export_to = st.session_state.get("ui_m4_export_to") or "drive"
    m4_drive_folder = st.session_state.get("ui_m4_drive_folder") or (selected_task or "GEE_Downloads")
    m4_local_dir = st.session_state.get("ui_m4_local_dir") or (
        os.path.join(root_dir, m4_drive_folder) if selected_task else root_dir
    )
    m4_cloud = int(st.session_state.get("ui_m4_cloud_limit") or 60)
    m4_min_land = float(st.session_state.get("ui_m4_min_land") or 5.0)
    m4_max_land = float(st.session_state.get("ui_m4_max_land") or 95.0)
    m4_min_pix = int(st.session_state.get("ui_m4_min_pixel_count") or 1000)
    m4_bands = list(st.session_state.get("ui_m4_bands") or ["B8", "B4", "B3", "B2", "B11"])
    m4_scale = int(st.session_state.get("ui_m4_scale") or 10)
    m4_gee_proxy = st.session_state.get("ui_m4_gee_proxy") or ""
    m4_gee_project = (st.session_state.get("ui_m4_gee_project") or os.environ.get("EE_PROJECT", "")).strip()

    if use_gee_download:
        sbui.section("获取卫星影像")
        with st.expander("影像筛选参数", expanded=True):
            m4_roi_path = st.text_input("研究区域矢量 (.shp)", key="ui_m4_roi_path")
            _roi_names = []
            try:
                import m4_engine as _m4e
                _roi_names = _m4e.list_roi_names(m4_roi_path)
            except Exception:
                pass
            if _roi_names:
                _def_roi = selected_task if selected_task in _roi_names else _roi_names[0]
                m4_roi_name = st.selectbox("研究区域名称 (name 字段)", _roi_names, index=_roi_names.index(_def_roi) if _def_roi in _roi_names else 0)
            else:
                m4_roi_name = st.text_input("研究区域名称 (name 字段)", key="ui_m4_roi_name", placeholder=selected_task or "zhejiang1")
            _c1, _c2 = st.columns(2)
            with _c1:
                m4_start_date = st.date_input("开始日期", key="ui_m4_start_date")
            with _c2:
                m4_end_date = st.date_input("结束日期", key="ui_m4_end_date")
            if m4_end_date < m4_start_date:
                st.error("结束日期不能早于开始日期")
            else:
                _span_days = (m4_end_date - m4_start_date).days + 1
                if _span_days > 31:
                    st.caption(f"已选 {_span_days} 天，将自动按月分批筛选影像，避免单次查询超时。")
            m4_export_to = st.radio(
                "导出方式",
                ["drive", "local"],
                format_func=lambda x: "Google Drive" if x == "drive" else "本机直链",
                horizontal=True,
                key="ui_m4_export_to",
            )
            m4_drive_folder = st.text_input("云端文件夹 / 任务子目录名", key="ui_m4_drive_folder")
            if m4_export_to == "local":
                m4_local_dir = st.text_input("本地下载目录", key="ui_m4_local_dir")
            else:
                if not st.session_state.get("ui_m4_local_dir"):
                    st.session_state.ui_m4_local_dir = os.path.join(root_dir, m4_drive_folder)
                m4_local_dir = st.session_state.ui_m4_local_dir
                st.caption(f"本地提取目录建议：`{os.path.join(root_dir, m4_drive_folder)}`（云端同步后放此处）")
            m4_bands = st.multiselect(
                "导出波段",
                ["B8", "B4", "B3", "B2", "B11", "B8A", "B5", "B6", "B7", "B12"],
                key="ui_m4_bands",
            )
            m4_cloud = st.slider("云量上限 (%)", 0, 100, key="ui_m4_cloud_limit")
            _lc1, _lc2 = st.columns(2)
            with _lc1:
                m4_min_land = st.number_input("最小陆地占比 (%)", 0.0, 100.0, key="ui_m4_min_land", step=0.5)
            with _lc2:
                m4_max_land = st.number_input("最大陆地占比 (%)", 0.0, 100.0, key="ui_m4_max_land", step=0.5)
            m4_min_pix = st.number_input("最小有效像素数", 100, 500000, key="ui_m4_min_pixel_count", step=100)
            _scale_opts = [10, 20, 30]
            if st.session_state.get("ui_m4_scale") not in _scale_opts:
                st.session_state.ui_m4_scale = 10
            m4_scale = st.selectbox("导出分辨率 (m)", _scale_opts, key="ui_m4_scale")
            m4_gee_proxy = st.text_input(
                "影像平台网络代理 (可选)",
                key="ui_m4_gee_proxy",
                placeholder=DEFAULT_CLASH_PROXY,
                help="Clash 混合代理端口（默认 7892），与 Clash 设置保持一致。",
            )
            m4_gee_project = st.text_input(
                "影像平台项目 ID（必填）",
                key="ui_m4_gee_project",
                placeholder="例如 ee-yourname 或 GCP 项目名",
                help="在 https://code.earthengine.google.com 登录后，右上角可见；"
                "或终端执行 earthengine set_project 项目ID 后填同一 ID。",
            )
        _m4_last = st.session_state.get("m4_last_result")
        if _m4_last:
            st.info(
                f"上次：{ _m4_last.get('roi_name')} · {_m4_last.get('image_count')} 景 · "
                f"{ 'Drive/' + str(_m4_last.get('drive_folder')) if _m4_last.get('export_to') == 'drive' else _m4_last.get('local_out_dir') }"
            )

    with st.expander("路径与模型环境", expanded=False):
        mask_root = st.text_input("预测掩膜根目录 (Mask)", key="ui_mask_root")
        final_root = st.text_input("最终合成根目录 (Output)", key="ui_final_root")
        if selected_task:
            task_mask_dir = os.path.join(mask_root, selected_task)
            task_final_dir = os.path.join(final_root, selected_task)
            st.text_input("当前任务 Mask", task_mask_dir, disabled=True)
            st.text_input("当前任务 Final", task_final_dir, disabled=True)
        else:
            task_mask_dir, task_final_dir = "", ""

        model_path = st.text_input("提取模型权重 (.pth)", key="ui_model_path")
        shp_path = st.text_input("岸线约束矢量 (.shp)", key="ui_shp_path")
        points_shp = st.text_input(
            "海洋种子点 (.shp，指数法)",
            key="ui_points_shp",
            help="用于从水体中筛选真实海洋面，需落在海水上的点要素。",
        )
        task_aoi_shp = st.text_input(
            "任务分区研究区域（裁剪参考真值，用于指标）",
            key="ui_task_aoi_shp",
            help="与侧栏「目标任务」同名的要素用于裁剪参考真值，再与预测比交并比/F1；自适应与合成阶段一致。文件不存在则跳过裁剪。",
        )

    if not use_gee_download:
        with st.expander("提取参数", expanded=False):
            _im_opts = ["深度学习", "指数法"]
            if st.session_state.get("ui_inference_mode") not in _im_opts:
                st.session_state.ui_inference_mode = "深度学习"
            inference_mode = st.radio(
                "提取方式",
                _im_opts,
                horizontal=True,
                key="ui_inference_mode",
                help="深度学习：模型逐景掩膜 + 时空合成；指数法：mNDWI 海面 + ACWI 频率 + 空间交集。",
            )
            use_index_mode = st.session_state.ui_inference_mode == "指数法"
            adaptive_mode = st.checkbox(
                "参数自动优化",
                key="ui_adaptive_mode",
                disabled=use_index_mode,
                help="自动搜索最优 (提取概率阈值, 最少有效影像次数)，使合成图与参考真值的交并比 / F1 最优。",
            )
            if use_index_mode:
                adaptive_mode = False
                prob_th, min_cnt = 0.05, 2
                st.caption("指数法输出 `{任务}_Index_Final.tif`；深度学习输出 `{任务}_Final_p*.shp`。")
            elif adaptive_mode:
                prob_th = 0.05
                min_cnt = 2
            else:
                prob_th = st.slider("提取概率阈值", 0.01, 0.50, step=0.01, key="ui_prob_th")
                min_cnt = st.slider("最少有效影像次数", 1, 10, step=1, key="ui_min_cnt")

        with st.expander("成果分析", expanded=False):
            m5_enabled = st.checkbox(
                "潮滩变化分析",
                key="ui_m5_enabled",
                help="合成完成后对比往年同区域潮滩，输出变化告警。",
            )
            m5_baseline_shp = st.text_input(
                "历史对比成果 SHP（可选）",
                key="ui_m5_baseline_shp",
                placeholder="留空自动匹配往年成果",
                disabled=not m5_enabled,
            )
            e1_enabled = st.checkbox(
                "潮滩精度评价",
                key="ui_e1_enabled",
                help="与开源潮滩产品做像元级对比，输出交并比、分歧图与成因分析。",
            )
            e1_data_root = st.text_input(
                "参考数据根目录",
                key="ui_e1_data_root",
                disabled=not e1_enabled,
            )
            _e1_ref_options = ["师姐_2020", "师姐_2022", "师姐_2024", "师姐_2025"]
            if st.session_state.get("ui_e1_reference") not in _e1_ref_options:
                st.session_state.ui_e1_reference = _e1_ref_options[0]
            e1_reference = st.selectbox(
                "参考数据",
                _e1_ref_options,
                key="ui_e1_reference",
                disabled=not e1_enabled,
            )
            _e1_default_compare = [
                "DCTF_2020", "FCS30_2020", "GTF30_2020", "CHN_2024",
                "MTWM_2020", "TFMC_2020", "national_10m_2020",
            ]
            if e1_enabled:
                try:
                    import e1_engine as _e1e

                    _e1_all = _e1e.list_e1_datasets(e1_data_root)
                    _e1_choices = [d for d in _e1_all if d != e1_reference and d not in _e1e._SKIP_COMPARE]
                    e1_compare_sources = st.multiselect(
                        "对比数据",
                        _e1_choices,
                        default=[d for d in _e1_default_compare if d in _e1_choices],
                    )
                except Exception:
                    e1_compare_sources = st.multiselect(
                        "对比数据",
                        _e1_default_compare,
                        default=_e1_default_compare,
                    )
                e1_export_maps = st.checkbox("导出分歧 GeoTIFF", key="ui_e1_export_maps")
                e1_export_heatmap = st.checkbox("导出一致热力图", key="ui_e1_export_heatmap")
            else:
                e1_compare_sources = _e1_default_compare
                e1_export_maps = True
                e1_export_heatmap = True
    else:
        use_index_mode = False
        adaptive_mode = False
        prob_th, min_cnt = 0.05, 2
        m5_enabled = False
        m5_baseline_shp = ""
        e1_enabled = False
        e1_data_root = r"E:\潮滩数据集"
        e1_reference = "师姐_2020"
        e1_compare_sources = []
        e1_export_maps = True
        e1_export_heatmap = True

    cache_hit = None
    force_rerun = bool(st.session_state.get("ui_force_rerun", False))
    tune_btn = False
    run_btn = False
    m4_run_btn = False

    _autotune_ready = False
    _ref_id = None
    _tune_objective = "iou_f1"

    if adaptive_mode and selected_task and not use_gee_download:
        with st.expander("参数自动优化配置", expanded=True):
            try:
                from dataset_assets import list_datasets, get_primary_path as _ds_get_path
                _ref_rows = list_datasets(role="reference_truth")
            except Exception:
                _ref_rows = []
            if not _ref_rows:
                st.warning("参考数据中无真值数据，请先登记真值数据集。")
                adaptive_mode = False
            else:
                _ref_opts = {}
                _default_idx = 0
                _task_year = None
                _ym = re.match(r"(\d{2})", selected_task or "")
                if _ym:
                    _task_year = 2000 + int(_ym.group(1))
                for _i, _d in enumerate(_ref_rows):
                    _label = f"{_d.get('title', _d['id'])} ({_d.get('year', '?')})"
                    _ref_opts[_label] = _d["id"]
                    if _task_year and _d.get("year") == _task_year:
                        _default_idx = _i
                _ref_label = st.selectbox("参考真值数据集", list(_ref_opts.keys()), index=_default_idx)
                _ref_id = _ref_opts[_ref_label]
                _obj_label = st.radio(
                    "优化目标",
                    ["交并比 + F1 (均衡)", "交并比 (优先)", "F1 (精确-召回优先)"],
                    horizontal=True,
                )
                _tune_objective = {"交并比 + F1 (均衡)": "iou_f1", "交并比 (优先)": "iou", "F1 (精确-召回优先)": "f1"}[_obj_label]

                _task_mask_check = os.path.join(mask_root, selected_task) if selected_task else ""
                _mask_count = len(glob.glob(os.path.join(_task_mask_check, "**", "*_mask.tif"), recursive=True)) if os.path.isdir(_task_mask_check) else 0
                if _mask_count == 0:
                    sbui.hint("尚无 Mask，请先运行提取", "warn")
                else:
                    sbui.hint(f"可优化 · {_mask_count} 个 Mask", "ok")
                    _autotune_ready = True

                st.caption("prob ∈ [0.01, 0.50] × cnt ∈ [1, 10] 自动搜索最优组合")

    if selected_task:
        if use_gee_download:
            final_tif_path = ""
        elif use_index_mode:
            final_tif_path = os.path.join(task_final_dir, f"{selected_task}_Index_Final.tif")
        else:
            final_tif_path = os.path.join(task_final_dir, f"{selected_task}_Final_p{prob_th:.2f}_c{min_cnt}.shp")
    else:
        final_tif_path = ""

    # --- 首次启动时扫描已有产出并注册到资产库 ---
    if not st.session_state.assets_scanned:
        scan_and_register_existing(final_root)
        st.session_state.assets_scanned = True

    map_display_path = None
    if not use_gee_download:
        # --- 参数变更时清除手动加载的资产覆盖 ---
        if use_index_mode:
            current_param_key = f"{selected_task}_index"
        else:
            current_param_key = f"{selected_task}_p{prob_th:.2f}_c{min_cnt}"
        if st.session_state._param_key is not None and st.session_state._param_key != current_param_key:
            if not st.session_state.get("_asset_pinned"):
                st.session_state.asset_override = None
        st.session_state._param_key = current_param_key

        # --- 资产缓存检测 ---
        if selected_task:
            cache_hit = find_index_asset(selected_task) if use_index_mode else find_asset(selected_task, prob_th, min_cnt)
        else:
            cache_hit = None

        if st.session_state.asset_override and os.path.exists(st.session_state.asset_override):
            map_display_path = st.session_state.asset_override
        elif cache_hit:
            map_display_path = cache_hit["file_path"]
        elif final_tif_path and os.path.exists(final_tif_path):
            map_display_path = final_tif_path

        sbui.section("成果管理")
        with st.container(border=True):
            if cache_hit:
                sbui.hint(f"缓存命中 · {cache_hit['file_size_mb']} MB · {cache_hit['created_at']}", "ok")
            elif selected_task:
                sbui.hint("暂无缓存，运行提取后生成")

            st.slider(
                "图层透明度",
                min_value=5,
                max_value=100,
                step=5,
                key="result_overlay_opacity_pct",
            )

            if selected_task:
                task_assets = get_task_assets(selected_task)
                if task_assets:
                    with st.expander(f"历史成果 ({len(task_assets)})", expanded=False):
                        for key, asset in task_assets.items():
                            a_cols = st.columns([5, 2])
                            with a_cols[0]:
                                if asset.get("method") == "index":
                                    _lbl = f"指数 · {asset['created_at']} · {asset['file_size_mb']}MB"
                                else:
                                    _lbl = (
                                        f"P={asset['prob_threshold']} C={asset['min_count']} "
                                        f"· {asset['created_at']} · {asset['file_size_mb']}MB"
                                    )
                                st.caption(_lbl)
                            with a_cols[1]:
                                if st.button("加载", key=f"load_{key}", use_container_width=True):
                                    st.session_state.asset_override = asset["file_path"]
                                    st.session_state._asset_pinned = True
                                    st.session_state._map_view_synced_for = None
                                    st.session_state.asset_just_loaded = True
                                    st.session_state._globe_rev = int(st.session_state.get("_globe_rev", 0)) + 1
                                    st.rerun()

            if cache_hit and not adaptive_mode:
                force_rerun = st.checkbox("强制重新生成", key="ui_force_rerun", help="忽略缓存，重新运行提取。")
    elif st.session_state.asset_override and os.path.exists(st.session_state.asset_override):
        map_display_path = st.session_state.asset_override

    # --- 自适应优化历史结果 ---
    _at_res = st.session_state.get("autotune_result")
    if _at_res:
        sbui.section("参数自动优化结果")
        with st.container(border=True):
            st.caption(f"最优概率 P={_at_res['best_prob']:.2f} · 次数 C={_at_res['best_cnt']}")
            _mc1, _mc2 = st.columns(2)
            with _mc1:
                st.metric("交并比 (IoU)", f"{_at_res['best_iou'] * 100:.1f}%")
            with _mc2:
                st.metric("F1 综合评分", f"{_at_res['best_f1'] * 100:.1f}%")
            st.caption(
                f"精确率 {_at_res['best_precision'] * 100:.1f}% · 召回率 {_at_res['best_recall'] * 100:.1f}% · "
                f"{_at_res['total_trials']} 组 · {_at_res['total_time_sec']:.0f}s"
            )
            _at_trials = _at_res.get("trials") or []
            if _at_trials:
                _sorted_t = sorted(_at_trials, key=lambda t: t["score"], reverse=True)
                with st.expander(f"Top-10 ({len(_sorted_t)})", expanded=False):
                    import pandas as pd
                    _top = _sorted_t[:10]
                    st.dataframe(
                        pd.DataFrame({
                            "#": range(1, len(_top) + 1),
                            "概率": [t["prob"] for t in _top],
                            "次数": [t["cnt"] for t in _top],
                            "交并比%": [round(t["iou"] * 100, 2) for t in _top],
                            "F1%": [round(t["f1"] * 100, 2) for t in _top],
                        }),
                        use_container_width=True,
                        hide_index=True,
                    )
            if st.button("清除参数优化结果", key="clear_autotune_result"):
                st.session_state.pop("autotune_result", None)
                st.rerun()

    if selected_task and final_root and not st.session_state.is_running:
        try:
            import m5_engine as _m5e
            _disk_m5 = _m5e.load_m5_report(final_root, selected_task)
            _cur_m5 = st.session_state.get("m5_report")
            if _disk_m5 and (not _cur_m5 or _cur_m5.get("target_roi") != selected_task):
                st.session_state.m5_report = _disk_m5
        except Exception:
            pass

    # 独立 M5 预检入口（不经 LLM，便于验收与无模型时使用）
    if selected_task and final_root and not st.session_state.is_running:
        if st.button("预检并生成变化分析计划", key="propose_m5_manual_btn", use_container_width=True):
            queue_agent_command(
                st.session_state,
                {
                    "sidebar_states": {
                        "m5_enabled": True,
                        "selected_task": selected_task,
                        "final_root": final_root,
                        "root_dir": root_dir,
                    },
                    "pending_action": {"type": "propose_m5", "task": selected_task},
                },
            )
            st.rerun()

    _m5_res = st.session_state.get("m5_report")
    _m5_plan = st.session_state.get("_m5_pending_plan")
    if isinstance(_m5_plan, dict) and not st.session_state.is_running:
        sbui.section("潮滩变化分析计划")
        with st.container(border=True):
            if _m5_plan.get("ready"):
                st.success("条件已满足，确认后将运行变化分析")
            else:
                st.warning("条件未满足，暂不可执行")
                for _b in _m5_plan.get("blockers") or []:
                    st.caption(f"· {_b}")
            st.caption(
                f"当前 `{_m5_plan.get('current_task') or '—'}` · "
                f"历史对比成果 `{_m5_plan.get('baseline_task') or '—'}` · "
                f"可用时期 {len(_m5_plan.get('available_periods') or [])}"
            )
            _pc1, _pc2 = st.columns(2)
            with _pc1:
                if st.button(
                    "确认执行变化分析",
                    key="confirm_m5_plan_btn",
                    type="primary",
                    use_container_width=True,
                    disabled=not bool(_m5_plan.get("ready")),
                ):
                    queue_agent_command(
                        st.session_state,
                        {"pending_action": {"type": "run_m5", "confirmed": True}},
                    )
                    st.rerun()
            with _pc2:
                if st.button("取消计划", key="cancel_m5_plan_btn", use_container_width=True):
                    st.session_state.pop("_m5_pending_plan", None)
                    st.session_state.pop("_m5_plan_confirmed", None)
                    st.session_state.pop("_m5_plan_notice", None)
                    st.rerun()

    _e1_plan = st.session_state.get("_e1_pending_plan")
    if isinstance(_e1_plan, dict) and not st.session_state.is_running:
        sbui.section("潮滩精度评价计划")
        with st.container(border=True):
            if _e1_plan.get("ready"):
                st.success("条件已满足，确认后将运行精度评价")
            else:
                st.warning("条件未满足，暂不可执行")
                for _b in _e1_plan.get("blockers") or []:
                    st.caption(f"· {_b}")
            st.caption(
                f"当前 `{_e1_plan.get('current_task') or '—'}` · "
                f"参考数据 `{_e1_plan.get('reference') or '—'}`"
            )
            _ec1, _ec2 = st.columns(2)
            with _ec1:
                if st.button(
                    "确认执行精度评价",
                    key="confirm_e1_plan_btn",
                    type="primary",
                    use_container_width=True,
                    disabled=not bool(_e1_plan.get("ready")),
                ):
                    queue_agent_command(
                        st.session_state,
                        {"pending_action": {"type": "run_e1", "confirmed": True}},
                    )
                    st.rerun()
            with _ec2:
                if st.button("取消精度评价计划", key="cancel_e1_plan_btn", use_container_width=True):
                    st.session_state.pop("_e1_pending_plan", None)
                    st.session_state.pop("_e1_plan_confirmed", None)
                    st.session_state.pop("_e1_plan_notice", None)
                    st.rerun()

    # 潮滩智能提取执行计划（可信执行闭环：先计划后确认）
    _inf_plan = st.session_state.get("_inference_pending_plan")
    if isinstance(_inf_plan, dict) and not st.session_state.is_running:
        sbui.section("潮滩智能提取计划")
        with st.container(border=True):
            if _inf_plan.get("ready"):
                st.success("条件已满足，确认后将真实调用提取/成果生成代码")
            else:
                st.warning("条件未满足，暂不可执行")
                for _b in _inf_plan.get("blockers") or []:
                    st.caption(f"· {_b}")
            st.caption(
                f"任务 `{_inf_plan.get('task_id') or '—'}` · "
                f"概率 P={_inf_plan.get('prob_threshold')} 次数 C={_inf_plan.get('count_threshold')} · "
                f"设备策略 `{_inf_plan.get('device_policy') or 'auto'}`"
                + (f"（实际 `{_inf_plan.get('device')}`）" if _inf_plan.get("device") else "")
            )
            _infc1, _infc2 = st.columns(2)
            with _infc1:
                if st.button(
                    "确认执行提取",
                    key="confirm_inference_plan_btn",
                    type="primary",
                    use_container_width=True,
                    disabled=not bool(_inf_plan.get("ready")),
                ):
                    from agent_command_bridge import confirm_inference_plan as _bridge_confirm_inf

                    _pid = _inf_plan.get("plan_id")
                    _ok, _cerr = _bridge_confirm_inf(st.session_state, str(_pid))
                    if not _ok:
                        st.warning(_cerr or "确认失败，请重新生成计划。")
                        st.rerun()
                    queue_agent_command(
                        st.session_state,
                        {"pending_action": {"type": "run_inference", "confirmed": True,
                                            "task": _inf_plan.get("task_id"), "plan_id": _pid}},
                    )
                    st.rerun()
            with _infc2:
                if st.button("取消计划", key="cancel_inference_plan_btn", use_container_width=True):
                    st.session_state.pop("_inference_pending_plan", None)
                    st.session_state.pop("_inference_plan_confirmed", None)
                    st.session_state.pop("_inference_plan_notice", None)
                    st.rerun()

    # 获取卫星影像执行计划（可信执行闭环：先计划后确认）
    _gee_plan = st.session_state.get("_gee_pending_plan")
    if isinstance(_gee_plan, dict) and not st.session_state.is_running:
        sbui.section("获取卫星影像计划")
        with st.container(border=True):
            if _gee_plan.get("ready"):
                st.success("条件已满足，确认后将真实下载卫星影像（不自动启动提取）")
            else:
                st.warning("条件未满足，暂不可执行")
                for _b in _gee_plan.get("blockers") or []:
                    st.caption(f"· {_b}")
            st.caption(
                f"任务 `{_gee_plan.get('task_id') or '—'}` · "
                f"{_gee_plan.get('start_date') or '—'} → {_gee_plan.get('end_date') or '—'} · "
                f"波段 {','.join((_gee_plan.get('bands') or ['B4','B3','B2']))} · "
                f"导出 `{_gee_plan.get('export_to')}`"
            )
            _gc1, _gc2 = st.columns(2)
            with _gc1:
                if st.button(
                    "确认下载影像",
                    key="confirm_gee_plan_btn",
                    type="primary",
                    use_container_width=True,
                    disabled=not bool(_gee_plan.get("ready")),
                ):
                    from agent_command_bridge import confirm_gee_plan as _bridge_confirm_gee

                    _gpid = _gee_plan.get("plan_id")
                    _gok, _gcerr = _bridge_confirm_gee(st.session_state, str(_gpid))
                    if not _gok:
                        st.warning(_gcerr or "确认失败，请重新生成计划。")
                        st.rerun()
                    queue_agent_command(
                        st.session_state,
                        {"pending_action": {"type": "run_gee_download", "confirmed": True,
                                            "task": _gee_plan.get("task_id"), "plan_id": _gpid}},
                    )
                    st.rerun()
            with _gc2:
                if st.button("取消计划", key="cancel_gee_plan_btn", use_container_width=True):
                    st.session_state.pop("_gee_pending_plan", None)
                    st.session_state.pop("_gee_plan_confirmed", None)
                    st.session_state.pop("_gee_plan_notice", None)
                    st.rerun()

    # 端到端一键潮滩分析：先计划后确认（父级确认门闩）
    _wf_plan = st.session_state.get("_workflow_pending_plan")
    if isinstance(_wf_plan, dict) and not st.session_state.is_running:
        sbui.section("一键潮滩分析")
        with st.container(border=True):
            import workflow_orchestrator as _wo

            _wf_id = str(_wf_plan.get("workflow_id") or "")
            _wf_confirmed = _wo.is_workflow_confirmed(st.session_state, _wf_id)
            _wf_status = str(_wf_plan.get("status") or "PENDING")
            if _wf_status == "PAUSED":
                st.warning("参数已变化，需重新确认后执行")
            elif _wf_confirmed:
                st.success(f"已确认 · `{_wf_id[:12]}…`")
            else:
                st.info(f"待确认 · `{_wf_id[:12]}…`")
            _wf_blockers = _wf_plan.get("blockers") or []
            if _wf_blockers:
                st.warning("全局校验未通过，暂不可执行")
                for _b in _wf_blockers:
                    st.caption(f"· {_b}")
            st.caption(
                f"任务 `{_wf_plan.get('task_id') or '—'}` · "
                f"{(_wf_plan.get('context') or {}).get('target_year')} 年潮滩"
                + (f" · 历史对比 {(_wf_plan.get('context') or {}).get('baseline_year')}" if (_wf_plan.get('context') or {}).get('baseline_year') else "")
            )
            _wf_steps = _wf_plan.get("steps") or []
            st.markdown(
                "\n".join(
                    f"- {'必' if s.get('required') else '选'} · "
                    f"{uil.get_tool_label(s.get('tool'))}"
                    f"（{uil.get_status_label(s.get('status') or 'PENDING')}）"
                    for s in _wf_steps
                )
            )
            _wfc1, _wfc2 = st.columns(2)
            with _wfc1:
                if st.button(
                    "确认执行一键分析",
                    key="confirm_workflow_btn",
                    type="primary",
                    use_container_width=True,
                    disabled=bool(_wf_blockers) or _wf_confirmed,
                ):
                    _ok, _cerr = _wo.confirm_workflow(st.session_state, _wf_id)
                    if not _ok:
                        st.warning(_cerr or "一键分析确认失败。")
                        st.rerun()
                    queue_agent_command(
                        st.session_state,
                        {"pending_action": {"type": "run_workflow", "confirmed": True,
                                            "workflow_id": _wf_id,
                                            "task": _wf_plan.get("task_id")}},
                    )
                    st.rerun()
            with _wfc2:
                if st.button("取消一键分析", key="cancel_workflow_btn", use_container_width=True):
                    st.session_state.pop("_workflow_pending_plan", None)
                    st.session_state.pop("_workflow_plan_confirmed", None)
                    st.session_state.pop("_workflow_notice", None)
                    st.rerun()

    # 重型工具确认门闩：Agent 请求 run_pipeline/run_m4/run_gee_download/run_autotune 未确认时在此待命
    _pending_heavy = st.session_state.get("_pending_heavy_confirm")
    if isinstance(_pending_heavy, dict) and not st.session_state.is_running:
        sbui.section("待确认操作")
        with st.container(border=True):
            _h_label = _pending_heavy.get("label") or _pending_heavy.get("action_type") or "潮滩智能提取"
            _h_task = _pending_heavy.get("task") or "—"
            st.warning(f"Agent 请求执行 **{_h_label}**（任务 `{_h_task}`），需要你确认后才会启动。")
            _hc1, _hc2 = st.columns(2)
            with _hc1:
                if st.button(
                    "确认执行",
                    key="confirm_heavy_btn",
                    type="primary",
                    use_container_width=True,
                ):
                    _orig = dict(_pending_heavy.get("action") or {})
                    _orig["confirmed"] = True
                    queue_agent_command(st.session_state, {"pending_action": _orig})
                    st.session_state.pop("_pending_heavy_confirm", None)
                    st.rerun()
            with _hc2:
                if st.button("取消", key="cancel_heavy_btn", use_container_width=True):
                    st.session_state.pop("_pending_heavy_confirm", None)
                    st.rerun()

    if _m5_res and (not selected_task or _m5_res.get("target_roi") == selected_task):
        sbui.section("潮滩变化分析结果")
        with st.container(border=True):
            _lvl = _m5_res.get("alert_level", "GREEN")
            _msg = _m5_res.get("diagnostic_message", "")
            if _lvl == "RED":
                st.error(_msg)
            elif _lvl == "YELLOW":
                st.warning(_msg)
            else:
                st.success(_msg)
            _qm = _m5_res.get("quantitative_metrics") or {}
            _ae = _qm.get("area_evolution") or {}
            _ct = _qm.get("centroid_trajectory") or {}
            st.caption(
                f"历史对比成果 {_m5_res.get('baseline_task') or '—'} · "
                f"面积 {_ae.get('baseline_area_km2', '?')}→{_ae.get('current_area_km2', '?')} km² "
                f"({_ae.get('change_rate_percentage', '?')}%) · "
                f"漂移 {_ct.get('drift_distance_meters', '?')} m"
            )
            with st.expander("详细指标", expanded=False):
                st.json(_m5_res)
            _mc1, _mc2, _mc3 = st.columns(3)
            _spatial = _m5_res.get("spatial_outputs") or {}
            _loss_p = _spatial.get("loss_shapefile_path")
            _silt_p = _spatial.get("siltation_shapefile_path")
            with _mc1:
                if (
                    _loss_p
                    and str(_loss_p) != "None"
                    and os.path.isfile(str(_loss_p))
                    and st.button("加载变化区域（萎缩）", key="load_m5_loss", use_container_width=True)
                ):
                    st.session_state.asset_override = _loss_p
                    st.session_state._asset_pinned = True
                    st.session_state._map_view_synced_for = None
                    st.session_state._map_prefer_center = False
                    st.session_state.asset_just_loaded = True
                    st.session_state._globe_rev = int(st.session_state.get("_globe_rev", 0)) + 1
                    st.rerun()
            with _mc2:
                if (
                    _silt_p
                    and str(_silt_p) != "None"
                    and os.path.isfile(str(_silt_p))
                    and st.button("加载变化区域（淤积）", key="load_m5_silt", use_container_width=True)
                ):
                    st.session_state.asset_override = _silt_p
                    st.session_state._asset_pinned = True
                    st.session_state._map_view_synced_for = None
                    st.session_state._map_prefer_center = False
                    st.session_state.asset_just_loaded = True
                    st.session_state._globe_rev = int(st.session_state.get("_globe_rev", 0)) + 1
                    st.rerun()
            with _mc3:
                if st.button("清除变化分析结果", key="clear_m5_report", use_container_width=True):
                    st.session_state.pop("m5_report", None)
                    st.rerun()

    if selected_task and final_root and not st.session_state.is_running:
        try:
            import e1_engine as _e1e
            _e1_ws = _e1e.workspace_for_task(final_root, selected_task)
            _disk_e1 = _e1e.load_e1_report(_e1_ws, selected_task)
            _cur_e1 = st.session_state.get("e1_report")
            if _disk_e1 and (not _cur_e1 or _cur_e1.get("roi_name") != selected_task):
                st.session_state.e1_report = _disk_e1
        except Exception:
            pass

    _e1_res = st.session_state.get("e1_report")
    if _e1_res and (not selected_task or _e1_res.get("roi_name") == selected_task):
        sbui.section("潮滩精度评价结果")
        with st.container(border=True):
            _comps = _e1_res.get("comparisons") or {}
            _rows = []
            _heat_path = None
            for _pair, _m in _comps.items():
                if "error" in _m:
                    _rows.append({"对比数据": _pair, "交并比 (IoU)": "ERR", "交集 km²": "-"})
                    continue
                _rows.append({
                    "对比数据": _pair,
                    "交并比 (IoU)": _m.get("jaccard_iou", "-"),
                    "交集 km²": _m.get("intersection_km2", "-"),
                })
                _maps = (_m.get("causal_analysis") or {}).get("disagreement_maps") or {}
                if not _heat_path and _maps.get("heatmap") and os.path.isfile(_maps["heatmap"]):
                    _heat_path = _maps["heatmap"]
            if _rows:
                st.dataframe(_rows, use_container_width=True, hide_index=True)
            _mp = _e1_res.get("multi_product_heatmap") or {}
            if _mp.get("disagreement_pixel_ratio") is not None:
                st.caption(f"分歧像元 {_mp.get('disagreement_pixel_ratio', 0):.2%}")
            _e1c1, _e1c2 = st.columns(2)
            with _e1c1:
                if _heat_path and st.button("加载热力图", key="load_e1_heatmap", use_container_width=True):
                    st.session_state.asset_override = _heat_path
                    st.session_state._asset_pinned = True
                    st.session_state._map_view_synced_for = None
                    st.session_state.asset_just_loaded = True
                    st.session_state._globe_rev = int(st.session_state.get("_globe_rev", 0)) + 1
                    st.rerun()
            with _e1c2:
                if st.button("清除精度评价结果", key="clear_e1_report", use_container_width=True):
                    st.session_state.pop("e1_report", None)
                    st.rerun()
            st.checkbox("球面叠加精度评价图层", key="globe_show_e1")
            with st.expander("详细报告", expanded=False):
                _md_path = _e1_res.get("report_md_path") or ""
                if _md_path and os.path.isfile(_md_path):
                    try:
                        with open(_md_path, "r", encoding="utf-8") as _mf:
                            st.markdown(_mf.read())
                    except Exception:
                        st.json(_e1_res)
                else:
                    st.json(_e1_res)

    st.markdown("---")
    if st.session_state.is_running:
        sbui.hint("任务运行中…", "run")

    if use_gee_download:
        m4_run_btn = st.button(
            "开始获取影像",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.is_running,
        )
    elif adaptive_mode:
        tune_btn = st.button(
            "开始参数优化",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.is_running or not _autotune_ready,
        )
    elif cache_hit and not force_rerun:
        run_btn = st.button(
            "加载已有成果",
            type="primary",
            use_container_width=True,
        )
    else:
        _run_label = "开始指数法提取" if use_index_mode else "开始模型提取"
        run_btn = st.button(
            _run_label,
            type="primary",
            use_container_width=True,
            disabled=st.session_state.is_running,
        )

    stop_btn = st.button(
        "中断任务",
        type="secondary",
        use_container_width=True,
        disabled=not st.session_state.is_running,
    )

    if stop_btn:
        st.session_state.stop_requested = True
        st.session_state.pending_task = None
        st.session_state.pop("pending_autotune", None)
        if st.session_state.get("pipeline_stop_event") is not None:
            st.session_state.pipeline_stop_event.set()
        _tl_add(selected_task or st.session_state.get("_tl_current_task") or "system",
                "EXECUTE", "任务已被用户中断", status="CANCELLED", tool="stop_button")
        st.toast("正在请求安全终止…", icon="🛑")
        st.rerun()

    if tune_btn and _autotune_ready and _ref_id:
        _aoi_path = (task_aoi_shp or "").strip()
        _aoi_use = _aoi_path if _aoi_path and os.path.isfile(_aoi_path) else None
        _tl_add(selected_task or "unknown", "QUEUED", "参数优化任务已入队",
                status="QUEUED", tool="run_autotune")
        st.session_state.pending_autotune = {
            "task": selected_task,
            "reference_id": _ref_id,
            "objective": _tune_objective,
            "task_aoi_shp": _aoi_use,
        }
        st.session_state.is_running = True
        st.session_state.stop_requested = False
        st.rerun()

    if m4_run_btn:
        if m4_end_date < m4_start_date:
            st.error("结束日期不能早于开始日期。")
        elif not m4_bands:
            st.error("请至少选择一个导出波段。")
        elif not os.path.isfile(m4_roi_path):
            st.error(f"研究区域矢量不存在: {m4_roi_path}")
        else:
            _tl_add(selected_task or "unknown", "QUEUED", "影像获取任务已入队",
                    status="QUEUED", tool="run_m4")
            st.session_state.pending_task = {
                "task": selected_task,
                "mode": "m4",
                "m4": {
                    "roi_path": m4_roi_path.strip(),
                    "roi_name": str(m4_roi_name).strip(),
                    "start_date": m4_start_date.isoformat(),
                    "end_date": m4_end_date.isoformat(),
                    "export_to": m4_export_to,
                    "local_out_dir": os.path.normpath(m4_local_dir.strip()),
                    "drive_folder": m4_drive_folder.strip(),
                    "bands": list(m4_bands),
                    "cloud_limit": int(m4_cloud),
                    "min_land_pct": float(m4_min_land),
                    "max_land_pct": float(m4_max_land),
                    "min_pixel_count": int(m4_min_pix),
                    "scale": int(m4_scale),
                    "gee_proxy_url": (m4_gee_proxy or "").strip(),
                    "gee_project_id": (m4_gee_project or "").strip(),
                },
            }
            st.session_state.is_running = True
            st.session_state.stop_requested = False
            st.rerun()

    if run_btn:
        if cache_hit and not force_rerun:
            st.session_state.asset_override = cache_hit["file_path"]
            st.session_state._asset_pinned = True
            st.session_state.asset_just_loaded = True
            st.session_state._map_view_synced_for = None
            st.session_state._globe_rev = int(st.session_state.get("_globe_rev", 0)) + 1
            _tl_add(selected_task or "unknown", "REGISTER", "加载缓存成果",
                    status="SUCCEEDED", tool="cache_load",
                    artifacts=[os.path.basename(str(cache_hit["file_path"]))])
            st.rerun()
        else:
            _tl_add(selected_task or "unknown", "QUEUED", "提取任务已入队",
                    status="QUEUED", tool="run_pipeline")
            st.session_state.pending_task = {
                "task": selected_task,
                "prob": prob_th,
                "cnt": min_cnt,
                "mode": "index" if use_index_mode else "dl",
                "points_shp": (points_shp or "").strip() if use_index_mode else None,
                "force_rerun": bool(force_rerun),
            }
            st.session_state.is_running = True
            st.session_state.stop_requested = False
            st.rerun()

    # ---- 功能状态面板（B 阶段）：折叠、可刷新、不含敏感路径 ----
    with st.expander("功能状态", expanded=False):
        try:
            import capability_registry as _cap
        except Exception:
            _cap = None
        if _cap is not None:
            _app_dir = os.path.dirname(os.path.abspath(__file__))
            _cap_ctx = {
                "model_path": st.session_state.get("ui_model_path") or "",
                "autotune_script": os.path.join(_app_dir, "auto_tune.py"),
                "knowledge_db_dir": os.path.normpath(
                    os.path.join(_app_dir, "..", "rs_knowledge_db")
                ),
                "task": selected_task or "",
            }
            _cap_sig = hashlib.md5(
                (f"{_cap_ctx['model_path']}|{_cap_ctx['task']}").encode("utf-8", errors="replace")
            ).hexdigest()[:12]
            _cap_reg = st.session_state.get("_capability_reg")
            if _cap_reg is None or st.session_state.get("_capability_ctx_sig") != _cap_sig:
                _cap_reg = _cap.CapabilityRegistry(context=_cap_ctx)
                st.session_state._capability_reg = _cap_reg
                st.session_state._capability_ctx_sig = _cap_sig
            _cap_c1, _cap_c2 = st.columns([3, 1])
            with _cap_c1:
                st.caption("动态功能状态（不含敏感路径）")
            with _cap_c2:
                if st.button("刷新", key="cap_refresh_btn", use_container_width=True):
                    _cap_reg.bump()
                    st.rerun()
            _status_labels = {
                "AVAILABLE": "🟢 可用",
                "CONDITIONAL": "🟡 条件可用",
                "BLOCKED": "🔴 暂不可用",
                "UNAVAILABLE": "⚪ 不可用",
                "UNKNOWN": "❔ 状态未知",
            }
            for _cid in _cap_reg.ids():
                _cst = _cap_reg.check(_cid)
                st.markdown(
                    f"**{uil.get_capability_label(_cid)}** · {_status_labels.get(_cst.status, _cst.status)}"
                )
                st.caption(_cst.summary)

    st.session_state["map_display_path"] = map_display_path

# ---- 主舱布局：中央地球 + 右侧指挥台（上：状态/日志，下：Copilot）----
# Cesium 内部画布高度；可视 iframe 高度由 CSS --workbench-h 控制
GLOBE_HEIGHT = 1000
LOG_PANEL_HEIGHT = 88

col_map, col_side = st.columns([70, 30], gap="small")

_log_panel_slot = None
uploaded_img = None
prompt = ""
send_btn = False
raster_load_error = None
map_state = None

with col_map:
    st.markdown('<div class="cockpit-map-col"></div>', unsafe_allow_html=True)

    map_display_path = st.session_state.get("map_display_path")

    try:
        _mc = st.session_state.get("map_center") or [35.0, 105.0]
        st.session_state.map_center = [float(_mc[0]), float(_mc[1])]
    except (TypeError, IndexError, ValueError):
        st.session_state.map_center = [35.0, 105.0]
    try:
        st.session_state.map_zoom = int(st.session_state.get("map_zoom", 3))
    except (TypeError, ValueError):
        st.session_state.map_zoom = 3

    # st_folium 会用 session 的 center/zoom 覆盖 Folium 内部视角；换缓存/换 TIF 时必须先把视角对齐到数据范围
    if map_display_path and os.path.exists(map_display_path):
        if st.session_state.get("_map_view_synced_for") != map_display_path:
            # Copilot 刚跳转时会把 _map_view_synced_for 置空；此时保留跳转中心，勿拉回成果范围
            if (
                st.session_state.get("_map_prefer_center")
                and st.session_state.get("_map_view_synced_for") is None
            ):
                st.session_state._map_view_synced_for = map_display_path
            else:
                _rv = _cached_view_for_asset_path(st.session_state, map_display_path)
                if _rv is not None:
                    _rla, _rlo, _rzm = _rv
                    st.session_state.map_center = [_rla, _rlo]
                    st.session_state.map_zoom = int(_rzm)
                    st.session_state._map_view_synced_for = map_display_path
                    # 新加载成果时恢复「飞到图层范围」，覆盖上一轮 Copilot 跳转优先标志
                    st.session_state._map_prefer_center = False
    else:
        st.session_state._map_view_synced_for = None

    raster_load_error = None
    _use_2d = bool(st.session_state.get("use_2d_map_fallback", False))
    map_state = None
    _globe_open_url = None
    _payload: dict = {}

    if not _use_2d:
        try:
            import globe_engine as _globe
            import globe_server as _globe_srv

            if "_globe_server_port" not in st.session_state:
                st.session_state._globe_server_port = 8765
            _globe_port = _globe_srv.ensure_running(
                preferred_port=int(st.session_state._globe_server_port)
            )
            st.session_state._globe_server_port = _globe_port

            _grev = int(st.session_state.get("_globe_rev", 0))
            _e1_tag = ""
            _e1r = st.session_state.get("e1_report")
            if isinstance(_e1r, dict):
                _e1_tag = str(_e1r.get("roi_name") or _e1r.get("reference") or "")

            _amt = 0.0
            _ap = ""
            if map_display_path and os.path.exists(map_display_path):
                _ap = os.path.normpath(os.path.abspath(map_display_path))
                try:
                    _amt = os.path.getmtime(_ap)
                except OSError:
                    _amt = 0.0

            _prefer_center = bool(st.session_state.get("_map_prefer_center", False))

            # 本机打开页面时强制本地地球，避免 iframe 走失效/过载的 ngrok（ERR_NGROK_3004）
            _page_host = ""
            try:
                _hdrs = getattr(getattr(st, "context", None), "headers", None) or {}
                _page_host = str(_hdrs.get("Host") or _hdrs.get("host") or "")
            except Exception:
                _page_host = ""
            _force_local_globe = _globe_srv.is_local_page_host(_page_host)

            # 缓存签名不含 map_center/zoom：Agent 纯跳转时复用同一 iframe，避免 Viewer 重建
            # 但包含 force_local，避免本机/远程 URL 混用
            _cache_sig = hashlib.md5(
                (
                    f"{_ap}|{_amt:.4f}|{_grev}|"
                    f"{st.session_state.get('result_overlay_opacity_pct', 50)}|"
                    f"{st.session_state.get('globe_show_e1', True)}|{_e1_tag}|"
                    f"local={int(_force_local_globe)}|cam=v3"
                ).encode("utf-8", errors="replace")
            ).hexdigest()

            _globe_warn = _globe_srv.globe_public_url_warning(_globe_port)
            if _globe_warn and not _force_local_globe:
                _warn_tok = hashlib.md5(_globe_warn.encode("utf-8", errors="replace")).hexdigest()[:12]
                if st.session_state.get("_globe_warn_token") != _warn_tok:
                    st.session_state._globe_warn_token = _warn_tok
                    st.warning(_globe_warn)

            _cached_url = st.session_state.get("_globe_iframe_url")
            _globe_cache_hit = (
                st.session_state.get("_globe_iframe_cache_sig") == _cache_sig
                and bool(_cached_url)
                and _globe_srv.same_globe_origin(
                    _cached_url, _globe_port, force_local=_force_local_globe
                )
            )

            if _globe_cache_hit:
                _globe_open_url = _cached_url
                _serve_ok = bool((st.session_state.get("_last_globe_payload") or {}).get("serve_ok", True))
            else:
                _alive_tiles = {}
                for _tk, _tc in list(st.session_state._globe_tile_clients.items()):
                    if _globe._tile_client_alive(_tc):
                        _alive_tiles[_tk] = _tc
                st.session_state._globe_tile_clients = _alive_tiles

                with _local_tile_no_proxy():
                    _payload = _globe.build_globe_payload(
                        center=tuple(st.session_state.map_center),
                        zoom=int(st.session_state.map_zoom),
                        result_path=map_display_path if map_display_path and os.path.exists(map_display_path) else None,
                        opacity_pct=float(st.session_state.get("result_overlay_opacity_pct", 50)),
                        e1_report=st.session_state.get("e1_report"),
                        show_e1_overlay=bool(st.session_state.get("globe_show_e1", True)),
                        tile_clients=st.session_state._globe_tile_clients,
                        ion_token=os.environ.get("CESIUM_ION_TOKEN"),
                        show_borders=False,
                        globe_port=_globe_port,
                        prefer_center=_prefer_center,
                        force_local=_force_local_globe,
                    )

                if map_display_path and os.path.exists(map_display_path):
                    _has_layer = bool(_payload.get("geojsonLayers") or _payload.get("imageryLayers"))
                    if not _has_layer:
                        raster_load_error = f"无法解析或加载资产: {map_display_path}"

                _html = _globe.build_cesium_html(_payload, height_px=GLOBE_HEIGHT, full_viewport=True)

                _gkey = hashlib.md5(_html.encode("utf-8", errors="replace")).hexdigest()[:10]
                if _ap:
                    _gkey = hashlib.md5(
                        f"{_gkey}|{_ap}|{_amt:.4f}|{_grev}|{st.session_state.get('result_overlay_opacity_pct', 50)}".encode(
                            "utf-8", errors="replace"
                        )
                    ).hexdigest()[:16]
                else:
                    _gkey = f"{_gkey}_{_grev}"

                _globe_srv.publish_html(_html, _gkey)
                _globe_open_url = _globe_srv.globe_url(
                    _globe_port, _gkey, bust=_grev, force_local=_force_local_globe
                )
                _html_disk = _globe_srv.html_dir() / f"{_gkey}.html"

                _serve_ok = False
                _serve_err = ""
                if _html_disk.is_file() and _payload.get("assetName"):
                    try:
                        import urllib.request as _urlreq

                        with _urlreq.urlopen(_globe_open_url, timeout=4) as _resp:
                            _body = _resp.read().decode("utf-8", errors="replace")
                        _serve_ok = _payload["assetName"] in _body
                        if not _serve_ok:
                            _serve_err = "HTTP 服务返回的页面不含资产数据（可能是旧版 globe 进程占用端口）"
                    except Exception as _se:
                        _serve_err = str(_se)
                elif _html_disk.is_file():
                    _serve_ok = True

                st.session_state["_last_globe_payload"] = {
                    "path": map_display_path,
                    "flyRectangle": bool(_payload.get("flyRectangle")),
                    "geojson": len(_payload.get("geojsonLayers") or []),
                    "imagery": len(_payload.get("imageryLayers") or []),
                    "assetName": _payload.get("assetName"),
                    "key": _gkey,
                    "url": _globe_open_url,
                    "port": _globe_port,
                    "serve_ok": _serve_ok,
                }

                if not _serve_ok and _serve_err:
                    raster_load_error = _serve_err or raster_load_error

                st.session_state._globe_iframe_cache_sig = _cache_sig
                st.session_state._globe_iframe_url = _globe_open_url

            if _globe_open_url:
                components.iframe(
                    _globe_open_url,
                    height=GLOBE_HEIGHT,
                    scrolling=False,
                )
                # Phase D: 消费 Cesium AOI 消息（绘制 → 校验 → 回声图层）
                _poll_aoi_messages()
                # Copilot 地图跳转：向已加载的 Cesium iframe 发 CSTF_FLY，避免重建 Viewer
                _fly = st.session_state.pop("_pending_camera_fly", None)
                if isinstance(_fly, dict) and _fly.get("lat") is not None and _fly.get("lon") is not None:
                    try:
                        _fly_lat = float(_fly["lat"])
                        _fly_lon = float(_fly["lon"])
                        _fly_zoom = int(_fly.get("zoom", 9))
                        _fly_height = _fly.get("height")
                        if _fly_height is None:
                            _fly_height = float(_globe.zoom_to_height_m(_fly_zoom, _fly_lat))
                        _fly_label = _fly.get("label") or f"({_fly_lat:.2f}°N, {_fly_lon:.2f}°E)"
                        _fly_payload, _fly_errs = _map_proto.make_fly_message(
                            _fly_lon,
                            _fly_lat,
                            zoom=_fly_zoom,
                            height=_fly_height,
                            pitch=float(_globe.DEFAULT_CAMERA["pitch_deg"]),
                            heading=float(_globe.DEFAULT_CAMERA["heading_deg"]),
                            duration=float(_fly.get("duration", 1.0)),
                            preset=_fly.get("preset"),
                            label=_fly_label,
                            source=str(_fly.get("source") or "agent"),
                        )
                        if _fly_payload is None:
                            st.warning("地图跳转参数无效：" + "; ".join(_fly_errs or []))
                        else:
                            # READY 握手：等 Cesium 就绪后发；等待窗口超 3s 仍未就绪则带警告发送
                            _mp_state = _globe_srv.map_protocol_state()
                            _ready_ok = bool(_mp_state.get("ready_ts"))
                            if not _ready_ok:
                                _wait_started = st.session_state.get("_map_ready_wait_started")
                                if _wait_started is None:
                                    st.session_state._map_ready_wait_started = time.time()
                                elif (time.time() - float(_wait_started)) > 3.0:
                                    st.caption("⚠️ 地图尚未确认就绪（可能仍在加载），已尝试跳转。")
                            import json as _json

                            _fly_js = _json.dumps(_fly_payload, ensure_ascii=False)
                            components.html(
                                f"""
<script>
(() => {{
  const win = window.parent || window;
  const doc = win.document;
  const msg = {_fly_js};
  // targetOrigin 收紧：从 iframe src 提取精确 origin；取不到时回退当前页面 origin
  let origin = "*";
  try {{
    const iframes = doc.querySelectorAll("iframe");
    iframes.forEach((ifr) => {{
      const src = ifr.getAttribute("src") || "";
      if (src.indexOf("/globe") >= 0 || src.indexOf(":8765") >= 0) {{
        try {{ origin = new URL(src, win.location.href).origin; }} catch (e) {{}}
      }}
    }});
  }} catch (e) {{}}
  const send = () => {{
    const iframes = doc.querySelectorAll("iframe");
    let sent = false;
    iframes.forEach((ifr) => {{
      const src = ifr.getAttribute("src") || "";
      if (!src) return;
      if (src.indexOf("/globe") >= 0 || src.indexOf(":8765") >= 0) {{
        try {{
          ifr.contentWindow.postMessage(msg, origin);
          sent = true;
        }} catch (e) {{}}
      }}
    }});
    return sent;
  }};
  if (!send()) {{
    let n = 0;
    const t = setInterval(() => {{
      if (send() || ++n > 40) clearInterval(t);
    }}, 120);
  }}
}})();
</script>
                            """,
                                height=0,
                            )
                            # 短等待 FLY_ACK（最多 ~1.2s），成功则 toast；未确认不阻塞
                            _ack = _globe_srv.wait_map_ack(_fly_payload.get("command_id", ""), timeout=1.2)
                            if _ack:
                                st.session_state["_map_ready_wait_started"] = None
                                if _ack.get("ok"):
                                    st.toast(f"地图已定位：{_fly_label}", icon="🗺️")
                                else:
                                    st.warning("地图跳转未完成，请检查地球页面状态。")
                    except (TypeError, ValueError):
                        pass
            else:
                raster_load_error = raster_load_error or "未能生成三维地球 URL"
        except Exception as _globe_err:
            raster_load_error = str(_globe_err)
            st.error(f"三维地球加载失败：{_globe_err}")
            st.caption(
                "本机请用 http://localhost:8501 打开；远程演示需同时启动 ngrok 并设置 CSTF_GLOBE_PUBLIC_URL。"
            )
            st.toast(f"三维地球加载失败，已切换 2D 地图: {_globe_err}", icon="⚠️")
            _use_2d = True

    if _use_2d:
        m = leafmap.Map(
            center=st.session_state.map_center,
            zoom=st.session_state.map_zoom,
            draw_control=True,
            measure_control=True,
        )
        try:
            m.add_basemap("OpenStreetMap")
        except Exception:
            pass

        if map_display_path and os.path.exists(map_display_path):
            layer_label = os.path.splitext(os.path.basename(map_display_path))[0]
            _rop = st.session_state.get("result_overlay_opacity_pct", 50) / 100.0
            _ok, _rerr = _add_result_to_map(
                m, map_display_path, f"成果: {layer_label}", opacity=_rop
            )
            if not _ok:
                raster_load_error = _rerr

        m.add_layer_control()
        _lat, _lon = float(st.session_state.map_center[0]), float(st.session_state.map_center[1])
        _folium_key = "cstf_main_map"
        if map_display_path and os.path.exists(map_display_path):
            _ap = os.path.normpath(os.path.abspath(map_display_path))
            try:
                _sig = hashlib.md5(
                    f"{_ap}\0{os.path.getmtime(_ap):.6f}".encode("utf-8", errors="replace")
                ).hexdigest()[:12]
                _folium_key = f"cstf_{_sig}"
            except OSError:
                pass
        map_state = st_folium(
            m,
            height=GLOBE_HEIGHT,
            width=None,
            use_container_width=True,
            center=(_lat, _lon),
            zoom=int(st.session_state.map_zoom),
            key=_folium_key,
        )

    if st.session_state.asset_just_loaded:
        st.session_state.asset_just_loaded = False
        _lp = st.session_state.get("_last_globe_payload") or {}
        if raster_load_error:
            st.toast(f"成果图层加载失败: {raster_load_error}", icon="⚠️")
        elif _lp.get("flyRectangle") and _lp.get("assetName"):
            st.toast(
                f"✅ 已加载 {_lp.get('assetName')} · 矢量:{_lp.get('geojson', 0)} 栅格:{_lp.get('imagery', 0)}",
                icon="✅",
            )
        elif map_display_path:
            st.toast("⚡ 成果路径已更新，但未能解析图层范围", icon="⚠️")
        else:
            st.toast("⚡ 已有成果已加载到地图", icon="✅")

    if raster_load_error and not st.session_state.asset_just_loaded:
        st.toast(f"成果图层加载异常: {raster_load_error}", icon="⚠️")
        with st.expander("🛰️ 地图加载诊断", expanded=False):
            _lp = st.session_state.get("_last_globe_payload") or {}
            st.write(f"**错误**：{raster_load_error}")
            if _lp.get("url"):
                st.write(f"**地球 URL**：{_lp.get('url')}")
            st.write(f"**本机地球端口**：{st.session_state.get('_globe_server_port', '—')}")
            st.write(
                "**建议**：① 用 http://localhost:8501 打开（不要用局域网 IP，除非已配 ngrok）；"
                "② 重启 Streamlit；③ 远程演示见 REMOTE_DEMO.md"
            )
            if st.button("切换为 2D 地图并重试", key="btn_force_2d_map"):
                st.session_state.use_2d_map_fallback = True
                st.rerun()

with col_side:
    st.markdown('<div class="command-deck-side">', unsafe_allow_html=True)
    _log_panel_slot = st.container()
    st.markdown('<div class="cstf-log-panel-host-marker"></div>', unsafe_allow_html=True)

    st.markdown('<div class="cstf-copilot-dock">', unsafe_allow_html=True)
    st.markdown('<div class="cockpit-copilot-zone-start"></div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="deck-section-title">🤖 智能分析助手</div>',
        unsafe_allow_html=True,
    )

    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": "您好！我是智能分析助手。请告诉我您想分析的区域和年份，或上传截图让我识别。"}
        ]
    st.markdown('<div class="cockpit-chat-anchor"></div>', unsafe_allow_html=True)
    chat_box = st.container(border=True)

    with chat_box:
        for msg in st.session_state.messages:
            avatar = "🧑‍💻" if msg["role"] == "user" else "🤖"
            with st.chat_message(msg["role"], avatar=avatar):
                if msg["role"] == "user":
                    st.markdown('<div class="msg-role msg-role-user">用户</div>', unsafe_allow_html=True)
                else:
                    st.markdown('<div class="msg-role msg-role-assistant">智能体</div>', unsafe_allow_html=True)
                st.markdown(msg["content"])
                _preview_path = msg.get("image_preview_path")
                if _preview_path and os.path.exists(_preview_path):
                    st.image(
                        _preview_path,
                        caption=msg.get("image_name") or "uploaded image",
                        use_container_width=True,
                    )

    st.markdown('<div class="cstf-chat-compose-host">', unsafe_allow_html=True)
    with st.form(key="chat_form", clear_on_submit=True):
        _input_cols = st.columns([10, 1])
        with _input_cols[0]:
            prompt = st.text_input(
                "chat_input",
                placeholder="问问智能分析助手…",
                label_visibility="collapsed",
            )
        with _input_cols[1]:
            send_btn = st.form_submit_button("➤", use_container_width=True)

        uploaded_img = st.file_uploader(
            "chat_attach",
            type=["png", "jpg", "jpeg", "webp", "tif", "tiff"],
            label_visibility="collapsed",
            key="chat_attach_uploader",
        )

    components.html(
        """
        <script>
        (() => {
          const win = window.parent || window;
          const doc = win.document;
          if (!doc || doc.body?.dataset?.cstfChatComposeBound === "1") {
            /* allow re-bind on streamlit rerun via observer */
          }

          const bindChatCompose = () => {
            const forms = doc.querySelectorAll('[data-testid="stForm"]');
            let chatForm = null;
            forms.forEach((f) => {
              if (f.querySelector('input[aria-label="chat_input"]')) chatForm = f;
            });
            if (!chatForm) return false;
            const fileWrap = chatForm.querySelector('[data-testid="stFileUploader"]');
            const fileInput = chatForm.querySelector('input[type="file"]');
            if (!fileWrap || !fileInput) return false;

            let bar = chatForm.querySelector('.cstf-attach-bar');
            if (!bar) {
              bar = doc.createElement('div');
              bar.className = 'cstf-attach-bar';
              bar.innerHTML =
                '<button type="button" class="cstf-plus-btn" title="上传图片或影像">+</button>' +
                '<span class="cstf-attach-name"></span>' +
                '<span class="cstf-attach-hint">PNG / JPG / WebP / TIFF</span>';
              fileWrap.appendChild(bar);
            }

            const plusBtn = bar.querySelector('.cstf-plus-btn');
            const nameEl = bar.querySelector('.cstf-attach-name');
            const hintEl = bar.querySelector('.cstf-attach-hint');

            if (plusBtn && plusBtn.dataset.bound !== '1') {
              plusBtn.dataset.bound = '1';
              plusBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                fileInput.click();
              });
            }

            const syncAttach = () => {
              const f = fileInput.files && fileInput.files[0];
              if (f) {
                nameEl.textContent = f.name;
                if (hintEl) hintEl.style.display = 'none';
              } else {
                nameEl.textContent = '';
                if (hintEl) hintEl.style.display = '';
              }
            };

            if (fileInput.dataset.cstfBound !== '1') {
              fileInput.dataset.cstfBound = '1';
              fileInput.addEventListener('change', () => {
                syncAttach();
                setTimeout(() => {
                  const chatInput =
                    doc.querySelector('input[aria-label="chat_input"]') ||
                    doc.querySelector('input[placeholder*="智能分析助手"]');
                  if (chatInput) chatInput.focus();
                }, 60);
              });
            }
            syncAttach();
            return true;
          };

          let tries = 0;
          const tick = () => {
            if (bindChatCompose() || tries++ > 40) return;
            win.setTimeout(tick, 120);
          };
          tick();

          if (!doc.body.dataset.cstfChatComposeObs) {
            doc.body.dataset.cstfChatComposeObs = '1';
            const obs = new MutationObserver(() => {
              win.setTimeout(bindChatCompose, 80);
            });
            obs.observe(doc.body, { childList: true, subtree: true });
          }
        })();
        </script>
        """,
        height=0,
    )
    st.markdown('</div></div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

_has_text = bool(prompt and prompt.strip())
_has_image = uploaded_img is not None
_user_submitted = send_btn and (_has_text or _has_image)

if _user_submitted:
    used_default_prompt = False
    if _has_text:
        prompt = prompt.strip()
    else:
        prompt = "请结合上传的遥感/地图影像进行专业解译，说明可能的地物、波段组合或异常现象。"
        used_default_prompt = True
        st.toast("未输入文本，已自动使用默认解译指令发送。", icon="ℹ️")

    drawn_context = ""
    if isinstance(map_state, dict) and map_state.get("last_active_drawing"):
        geo_info = map_state["last_active_drawing"]["geometry"]
        drawn_context = f"\n\n[系统隐秘信息：用户当前在地图上框选的区域几何信息为 {geo_info}]"

    full_prompt_for_agent = prompt + drawn_context
    user_preview_path = None
    user_image_name = None
    if uploaded_img is not None:
        user_preview_path, user_image_name = _save_chat_image_preview(uploaded_img)

    display_prompt = prompt
    if used_default_prompt:
        display_prompt = prompt + "\n\n`（未输入文本，系统已自动填充默认解译指令）`"

    with chat_box:
        with st.chat_message("user", avatar="🧑‍💻"):
            st.markdown('<div class="msg-role msg-role-user">用户</div>', unsafe_allow_html=True)
            st.markdown(display_prompt)
            if user_preview_path and os.path.exists(user_preview_path):
                st.image(user_preview_path, caption=user_image_name or "uploaded image", use_container_width=True)
    user_msg = {"role": "user", "content": display_prompt}
    if user_preview_path:
        user_msg["image_preview_path"] = user_preview_path
        user_msg["image_name"] = user_image_name
    st.session_state.messages.append(user_msg)

    with chat_box:
        with st.chat_message("assistant", avatar="🤖"):
            st.markdown('<div class="msg-role msg-role-assistant">智能体</div>', unsafe_allow_html=True)
            with st.spinner("🧠 智能体思考中..."):
                try:
                    import agent
                    import m5_agent_loop
                    import e1_agent_loop

                    # 待确认 M5 计划时：用户短句确认 → 直接执行，不绕开条件检查
                    _pending_m5 = st.session_state.get("_m5_pending_plan")
                    if (
                        isinstance(_pending_m5, dict)
                        and m5_agent_loop.is_m5_confirm_utterance(prompt)
                        and not st.session_state.is_running
                    ):
                        if not _pending_m5.get("ready"):
                            _block = "；".join(_pending_m5.get("blockers") or ["条件未满足"])
                            _msg = f"当前潮滩变化分析计划尚不可执行：{_block}"
                            st.warning(_msg)
                            st.session_state.messages.append(
                                {"role": "assistant", "content": _msg}
                            )
                        else:
                            queue_agent_command(
                                st.session_state,
                                {
                                    "pending_action": {
                                        "type": "run_m5",
                                        "confirmed": True,
                                        "task": _pending_m5.get("current_task"),
                                    }
                                },
                            )
                            _msg = (
                                "已确认潮滩变化分析计划，正在调用现有分析引擎。"
                                "完成后将根据真实报告与差异面回复。"
                            )
                            st.success(_msg)
                            st.session_state.messages.append(
                                {"role": "assistant", "content": _msg}
                            )
                        st.rerun()

                    # 待确认 E1 计划时：短句确认 → 直接执行
                    _pending_e1 = st.session_state.get("_e1_pending_plan")
                    if (
                        isinstance(_pending_e1, dict)
                        and e1_agent_loop.is_e1_confirm_utterance(prompt)
                        and not st.session_state.is_running
                    ):
                        # 若同时有 M5 待确认，优先已处理的 M5；此处仅当无 M5 待确认或用户明确提 E1
                        _pending_m5_chk = st.session_state.get("_m5_pending_plan")
                        if not isinstance(_pending_m5_chk, dict) or "e1" in (prompt or "").lower() or "一致" in (prompt or ""):
                            if not _pending_e1.get("ready"):
                                _block = "；".join(_pending_e1.get("blockers") or ["条件未满足"])
                                _msg = f"当前潮滩精度评价计划尚不可执行：{_block}"
                                st.warning(_msg)
                                st.session_state.messages.append(
                                    {"role": "assistant", "content": _msg}
                                )
                            else:
                                queue_agent_command(
                                    st.session_state,
                                    {
                                        "pending_action": {
                                            "type": "run_e1",
                                            "confirmed": True,
                                            "task": _pending_e1.get("current_task"),
                                        }
                                    },
                                )
                                _msg = (
                                    "已确认潮滩精度评价计划，正在调用现有评价引擎。"
                                    "完成后将根据真实报告回复交并比等指标。"
                                )
                                st.success(_msg)
                                st.session_state.messages.append(
                                    {"role": "assistant", "content": _msg}
                                )
                            st.rerun()

                    # 验收/高级：用户消息中直接粘贴 SYSTEM_COMMAND_JSON 时入队（不经 LLM）
                    if "[SYSTEM_COMMAND_JSON]" in (prompt or ""):
                        cmd_result, clean_reply = process_agent_reply(st.session_state, prompt)
                        for _ce in cmd_result.errors:
                            st.warning(_ce)
                        _msg = clean_reply or (
                            f"已接收系统指令：{cmd_result.action_type or 'sidebar/map'}"
                        )
                        st.markdown(_msg)
                        st.session_state.messages.append(
                            {"role": "assistant", "content": _msg}
                        )
                        st.rerun()

                    temp_img_path = None
                    if uploaded_img is not None:
                        _yy_dir = os.path.dirname(os.path.abspath(__file__))
                        _up_dir = os.path.join(_yy_dir, "_chat_upload_tmp")
                        os.makedirs(_up_dir, exist_ok=True)
                        _base = os.path.basename(uploaded_img.name) or "image"
                        _safe = "".join(c for c in _base if c.isalnum() or c in "._-")
                        if not _safe:
                            _safe = "upload.png"
                        temp_img_path = os.path.join(_up_dir, f"_{os.getpid()}_{_safe}")
                        with open(temp_img_path, "wb") as f:
                            f.write(uploaded_img.getbuffer())

                    try:
                        from dataset_assets import build_dataset_catalog_for_agent
                        _ds_cat = build_dataset_catalog_for_agent()
                    except Exception:
                        _ds_cat = ""

                    _sidebar_ctx = build_agent_sidebar_context(st.session_state)
                    # Phase D: AOI 空间上下文（紧凑摘要 + 能力推荐；AOI 选定 ≠ 确认）
                    _aoi_ctx_text = _aoi_sidebar_context()
                    if _aoi_ctx_text:
                        _sidebar_ctx = (_sidebar_ctx + "\n\n" + _aoi_ctx_text) if _sidebar_ctx else _aoi_ctx_text
                    # 能力状态快照（白名单，无路径/密钥）：仅首条消息或刷新后注入一次
                    _cap_snap_text = None
                    try:
                        import capability_registry as _cap
                        _cap_reg = st.session_state.get("_capability_reg")
                        if _cap_reg is None:
                            _cap_reg = _cap.CapabilityRegistry(context={})
                            st.session_state._capability_reg = _cap_reg
                        if not st.session_state.get("_cap_snapshot_injected"):
                            _snap = _cap_reg.snapshot_for_agent()
                            _groups = _cap_reg.grouped_summary()
                            _lines = [
                                "可用: " + ",".join(_groups.get("AVAILABLE", [])),
                                "受限: " + ",".join(_groups.get("CONDITIONAL", [])),
                                "阻断: " + ",".join(_groups.get("BLOCKED", [])),
                                "未启用: " + ",".join(_groups.get("UNAVAILABLE", [])),
                                "未知: " + ",".join(_groups.get("UNKNOWN", [])),
                            ]
                            _reasons = {cid: e["summary"] for cid, e in _snap.items()}
                            _cap_snap_text = "\n".join(_lines) + "\n原因: " + str(_reasons)
                            st.session_state._cap_snapshot_injected = True
                    except Exception:
                        _cap_snap_text = None
                    reply = agent.chat_with_vlm(
                        full_prompt_for_agent,
                        st.session_state.messages,
                        temp_img_path,
                        available_tasks=task_options,
                        dataset_catalog_text=_ds_cat or None,
                        sidebar_context=_sidebar_ctx,
                        capability_summary=_cap_snap_text,
                    )

                    if temp_img_path and os.path.exists(temp_img_path):
                        os.remove(temp_img_path)

                    cmd_result, clean_reply = process_agent_reply(st.session_state, reply)
                    if cmd_result.applied:
                        for _ce in cmd_result.errors:
                            st.warning(_ce)
                        if cmd_result.action_type:
                            st.success(f"⚙️ 已接收指令：{cmd_result.action_type}")
                        display = clean_reply or "已更新系统设置。"
                        st.markdown(display)
                        st.session_state.messages.append({"role": "assistant", "content": display})
                        st.rerun()

                    parsed_map = _parse_agent_map_command(reply)
                    if parsed_map is not None:
                        target_lat, target_lon, target_zoom, _cmd_span = parsed_map
                        queue_agent_command(
                            st.session_state,
                            {"map": {"lat": target_lat, "lon": target_lon, "zoom": target_zoom}},
                        )
                        clean_reply = _strip_map_command_from_reply(reply)
                        if not clean_reply:
                            clean_reply = "已为您将地图视角定位至目标区域 🛰️。"
                        st.markdown(clean_reply)
                        st.session_state.messages.append({"role": "assistant", "content": clean_reply})
                        st.rerun()

                    parsed_pipe = _parse_agent_pipeline_command(reply)
                    if parsed_pipe is not None:
                        agent_task, agent_prob, agent_cnt, cmd_span = parsed_pipe
                        st.success(f"⚙️ 准备执行任务: {agent_task}")
                        clean_reply = reply.replace(cmd_span, "").replace("**", "").strip()
                        if not clean_reply:
                            clean_reply = "好的，已收到您的指令，正在后台为您执行调度任务..."
                        st.markdown(clean_reply)
                        st.session_state.messages.append({"role": "assistant", "content": clean_reply})
                        queue_agent_command(
                            st.session_state,
                            {
                                "sidebar_states": {
                                    "selected_task": agent_task,
                                    "prob_th": agent_prob,
                                    "min_cnt": agent_cnt,
                                },
                                "pending_action": {
                                    "type": "run_pipeline",
                                    "task": agent_task,
                                },
                            },
                        )
                        st.rerun()

                    _has_map_kw = re.search(r"COMMAND_UPDATE_MAP", reply, re.I) is not None
                    _has_pipe_kw = re.search(r"COMMAND_RUN_PIPELINE", reply, re.I) is not None
                    if (_has_map_kw or _has_pipe_kw) and parsed_map is None and parsed_pipe is None:
                        st.warning(
                            "模型提到了地图/跑图暗号但无法解析。推荐：让模型调用工具 `change_map_view`；"
                            "或正文含 `COMMAND_UPDATE_MAP|纬度|经度|缩放`（竖线）。"
                            "括号格式 `(lat,lon)` 已做兼容，若仍失败请重试。"
                        )
                        st.markdown(reply)
                        st.session_state.messages.append({"role": "assistant", "content": reply})
                    elif not _has_map_kw and not _has_pipe_kw:
                        st.markdown(reply)
                        st.session_state.messages.append({"role": "assistant", "content": reply})

                except Exception as e:
                    _append_debug_log(f"agent_chat_failed: {_format_agent_exception(e)}")
                    st.error(f"连接智能体出错: {_format_agent_exception(e)}")

# =======================================================
#  4. 后台流水线：启动 + 收尾 + 监控区（须放在 root_dir 等侧栏变量之后）
# =======================================================
def finalize_background_pipeline():
    shared = st.session_state.get("pipeline_shared")
    if not shared or not shared.get("done") or not st.session_state.is_running:
        return False
    with shared["lock"]:
        success = bool(shared.get("success", False))
        asset_path = shared.get("asset_path")
        lines = list(shared.get("log_lines") or [])
        prog = int(shared.get("progress", 0))
        at_result = shared.get("autotune_result")
        m5_report = shared.get("m5_report")
        m5_verification = shared.get("m5_verification")
        e1_report = shared.get("e1_report")
        e1_verification = shared.get("e1_verification")
        job_kind = shared.get("job_kind")
        inference_result = shared.get("inference_result")
        inference_verification = shared.get("inference_verification")
        inference_asset_id = shared.get("asset_id")
    _tl_task = st.session_state.get("_tl_current_task") or "unknown"
    st.session_state.pipeline_log_snapshot = lines
    st.session_state.pipeline_progress_value = prog
    st.session_state.is_running = False
    st.session_state.pipeline_thread_started = False
    st.session_state.pipeline_shared = None
    st.session_state.pipeline_stop_event = None
    st.session_state.stop_requested = False
    st.session_state.executing_pipeline = False
    if at_result:
        st.session_state.autotune_result = at_result
    # ---- 本地潮滩推理可信执行闭环收尾 ----
    if inference_result is not None or inference_asset_id:
        _iv_ok = bool(inference_verification and inference_verification.get("ok") is True)
        if success and _iv_ok:
            # 真实结果写回 Copilot（只展示真实数据）
            try:
                import inference_agent_loop as _ial

                summary = _ial.summarize_inference_result_for_chat(
                    inference_result, inference_verification
                )
                st.session_state.messages = list(st.session_state.get("messages") or [])
                st.session_state.messages.append(
                    {"role": "assistant", "content": summary}
                )
                st.session_state._inference_last_summary = summary
            except Exception:
                pass
            _tl_add(_tl_task, "INFERENCE", "智能提取完成",
                    status="SUCCEEDED", tool="run_inference")
            _tl_add(_tl_task, "POST_PROCESS", "成果生成完成（潮滩栅格/矢量成果）",
                    status="SUCCEEDED", tool="post_engine")
            _tl_add(_tl_task, "VERIFY", "成果校验通过（潮滩栅格/矢量成果）",
                    status="SUCCEEDED", tool="verify_inference")
            if inference_asset_id:
                _tl_add(_tl_task, "REGISTER", "提取成果已登记",
                        status="SUCCEEDED", tool="register_inference",
                        artifacts=[str(inference_asset_id)])
            # 地图加载（不重建 iframe / 不重置相机）：成果路径已由校验确认
            _map_path = (inference_verification or {}).get("final_tif") or \
                        (inference_verification or {}).get("final_shp") or \
                        (asset_path or "")
            if _map_path and os.path.isfile(str(_map_path)):
                st.session_state.asset_override = os.path.abspath(str(_map_path))
                st.session_state._asset_pinned = True
                st.session_state.asset_just_loaded = True
                st.session_state._map_view_synced_for = None
                st.session_state._map_prefer_center = False
                st.session_state._globe_rev = int(st.session_state.get("_globe_rev", 0)) + 1
                _tl_add(_tl_task, "MAP", "成果已加载到地图",
                        status="SUCCEEDED", tool="map_load",
                        artifacts=[os.path.basename(str(_map_path))])
            _tl_add(_tl_task, "REPORT", "结果已回复智能助手",
                    status="SUCCEEDED", tool="report")
            # 动态能力状态刷新（含深度学习推理能力）
            try:
                import capability_registry as _cap
                _cap_reg = st.session_state.get("_capability_reg")
                if _cap_reg is not None:
                    _cap_reg.invalidate()
                st.session_state._cap_snapshot_injected = False
            except Exception:
                pass
        else:
            _err = (inference_result or {}).get("error") or "提取失败（详见终端日志）"
            _tl_add(_tl_task, "INFERENCE", f"提取未完成：{_err[:60]}",
                    status="FAILED", error=_err, tool="run_inference")
    # ---- GEE 影像下载可信执行闭环收尾 ----
    gee_result = shared.get("gee_result") if shared else None
    gee_verification = shared.get("gee_verification") if shared else None
    gee_dataset_id = shared.get("dataset_id") if shared else None
    if gee_result is not None or gee_dataset_id:
        _gv_ok = bool(gee_verification and gee_verification.get("ok") is True)
        if success and _gv_ok:
            try:
                import gee_agent_loop as _gal

                summary = _gal.summarize_gee_result_for_chat(gee_result, gee_verification)
                st.session_state.messages = list(st.session_state.get("messages") or [])
                st.session_state.messages.append(
                    {"role": "assistant", "content": summary}
                )
                st.session_state._gee_last_summary = summary
            except Exception:
                pass
            _tl_add(_tl_task, "GEE_EXPORT", "影像获取完成",
                    status="SUCCEEDED", tool="run_gee_download")
            _tl_add(_tl_task, "VERIFY", "影像校验通过",
                    status="SUCCEEDED", tool="verify_gee")
            if gee_dataset_id:
                _tl_add(_tl_task, "REGISTER", "影像数据已登记",
                        status="SUCCEEDED", tool="register_gee",
                        artifacts=[str(gee_dataset_id)])
            _tl_add(_tl_task, "REPORT", "结果已回复智能助手",
                    status="SUCCEEDED", tool="report")
            # 动态能力状态刷新（GEE 能力 / 推理能力 scene_count 感知）
            try:
                import capability_registry as _cap
                _cap_reg = st.session_state.get("_capability_reg")
                if _cap_reg is not None:
                    _cap_reg.invalidate()
                st.session_state._cap_snapshot_injected = False
            except Exception:
                pass
        else:
            _err = (gee_result or {}).get("error") or "影像获取失败（详见终端日志）"
            _tl_add(_tl_task, "GEE_EXPORT", f"获取未完成：{_err[:60]}",
                    status="FAILED", error=_err, tool="run_gee_download")
    if m5_report:
        st.session_state.m5_report = m5_report
        _lvl = m5_report.get("alert_level", "GREEN")
        if _lvl in ("RED", "YELLOW"):
            try:
                st.toast(
                    f"变化分析告警 [{_lvl}]: {m5_report.get('diagnostic_message', '')[:80]}",
                    icon="🚨" if _lvl == "RED" else "⚠️",
                )
            except Exception:
                pass
        # 独立 M5 闭环：把真实结果写回 Copilot，并加载地图
        if job_kind == "m5" or (success and m5_verification is not None):
            try:
                import m5_agent_loop

                summary = m5_agent_loop.summarize_m5_report_for_chat(
                    m5_report, m5_verification
                )
                st.session_state.messages = list(st.session_state.get("messages") or [])
                st.session_state.messages.append(
                    {"role": "assistant", "content": summary}
                )
                st.session_state._m5_last_summary = summary
            except Exception:
                pass
            map_path = asset_path
            if not map_path:
                try:
                    import m5_agent_loop

                    map_path = m5_agent_loop.pick_m5_map_path(m5_report)
                except Exception:
                    map_path = None
            if map_path and os.path.isfile(str(map_path)):
                st.session_state.asset_override = map_path
                st.session_state._asset_pinned = True
                st.session_state.asset_just_loaded = True
                st.session_state._map_view_synced_for = None
                st.session_state._map_prefer_center = False
                st.session_state._globe_rev = int(st.session_state.get("_globe_rev", 0)) + 1
    if e1_report:
        st.session_state.e1_report = e1_report
        try:
            st.toast("精度评价已完成", icon="📊")
        except Exception:
            pass
        # 独立 E1 闭环：真实指标写回 Copilot，并优先加载分歧热力图
        if job_kind == "e1" or (success and e1_verification is not None):
            try:
                import e1_agent_loop

                summary = e1_agent_loop.summarize_e1_report_for_chat(
                    e1_report, e1_verification
                )
                st.session_state.messages = list(st.session_state.get("messages") or [])
                st.session_state.messages.append(
                    {"role": "assistant", "content": summary}
                )
                st.session_state._e1_last_summary = summary
            except Exception:
                pass
            map_path = asset_path
            if not map_path:
                try:
                    import e1_agent_loop

                    map_path = e1_agent_loop.pick_e1_map_path(e1_report)
                except Exception:
                    map_path = None
            if map_path and os.path.isfile(str(map_path)):
                st.session_state.asset_override = map_path
                st.session_state._asset_pinned = True
                st.session_state.asset_just_loaded = True
                st.session_state._map_view_synced_for = None
                st.session_state._map_prefer_center = False
                st.session_state._globe_rev = int(st.session_state.get("_globe_rev", 0)) + 1
    m4_result = shared.get("m4_result") if shared else None
    if m4_result:
        st.session_state.m4_last_result = m4_result
    # ---- 端到端潮滩分析 Workflow 收尾 ----
    workflow_result = shared.get("workflow_result") if shared else None
    if workflow_result:
        st.session_state.workflow_last_result = workflow_result
        wf_status = workflow_result.get("status")
        try:
            summary = workflow_result.get("summary") or ""
            st.session_state.messages = list(st.session_state.get("messages") or [])
            st.session_state.messages.append(
                {"role": "assistant", "content": summary}
            )
            st.session_state._workflow_last_summary = summary
        except Exception:
            pass
        step_line = " | ".join(
            f"{sid}:{s.get('status')}"
            for sid, s in (workflow_result.get("steps") or {}).items()
        )
        if success:
            _tl_add(_tl_task, "WORKFLOW",
                    f"一键潮滩分析完成（{uil.get_status_label(wf_status)}）",
                    status="SUCCEEDED", progress=100,
                    tool="run_workflow",
                    artifacts=[str(workflow_result.get("workflow_id") or "")])
            _tl_add(_tl_task, "WORKFLOW", f"步骤: {step_line}",
                    status="SUCCEEDED", tool="run_workflow")
            try:
                st.balloons()
            except Exception:
                pass
        else:
            _tl_add(_tl_task, "WORKFLOW", f"一键潮滩分析未完成（{uil.get_status_label(wf_status)}）",
                    status="FAILED", error=step_line, tool="run_workflow")
    # 推理闭环已在上面自行登记 EXECUTE/REGISTER/VERIFY/MAP/REPORT，这里避免重复
    _inference_handled = inference_result is not None or inference_asset_id is not None
    _gee_handled = gee_result is not None or gee_dataset_id is not None
    _workflow_handled = workflow_result is not None
    if success and asset_path and job_kind not in ("m5", "e1") and not _inference_handled and not _gee_handled and not _workflow_handled:
        st.session_state.asset_override = asset_path
    if success and not _inference_handled and not _gee_handled and not _workflow_handled:
        _tl_add(_tl_task, "EXECUTE", f"任务执行完成（{job_kind or 'pipeline'}）",
                status="SUCCEEDED", progress=100, tool=job_kind or "run_pipeline")
        if asset_path:
            _tl_add(_tl_task, "REGISTER", "成果已登记",
                    status="SUCCEEDED", tool="register_asset",
                    artifacts=[os.path.basename(str(asset_path))])
        if m5_report:
            _tl_add(_tl_task, "VERIFY", "变化分析校验通过",
                    status="SUCCEEDED", tool="verify_m5")
        if e1_report:
            _tl_add(_tl_task, "VERIFY", "精度评价校验通过",
                    status="SUCCEEDED", tool="verify_e1")
        try:
            st.balloons()
        except Exception:
            pass
    else:
        if not _inference_handled and not _gee_handled:
            _tl_add(_tl_task, "EXECUTE", "任务执行失败",
                    status="FAILED", error="任务执行失败，详见终端日志",
                    tool=job_kind or "run_pipeline")
        time.sleep(2)
    return True


# ---- 自适应调参后台线程 ----

def run_autotune_sync(ctx, shared, stop_event):
    """后台线程：自适应参数搜索（假设 Mask 已存在）。"""
    logs_local = []

    def check_stop():
        return stop_event.is_set()

    def push_log(msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] root@autotune: {msg}"
        logs_local.append(line)
        with shared["lock"]:
            shared["log_lines"] = logs_local[-40:]
        print(msg)

    def push_progress(pct):
        with shared["lock"]:
            shared["progress"] = int(min(100, max(0, pct)))

    def push_status(kind, text):
        with shared["lock"]:
            shared["status"] = (kind, text)

    push_progress(0)
    push_status("info", "🔬 参数自动优化启动…")

    task = ctx["task"]
    task_options_local = ctx["task_options"]
    actual_task = task
    for opt in task_options_local:
        if task in opt:
            actual_task = opt
            break

    input_dir = os.path.join(ctx["root_dir"], actual_task)
    mask_out_dir = os.path.join(ctx["mask_root"], actual_task)
    final_out_dir = os.path.join(ctx["final_root"], actual_task)
    os.makedirs(final_out_dir, exist_ok=True)

    push_log(f"TASK: {actual_task} | REF: {ctx['reference_id']} | OBJ: {ctx['objective']}")
    push_progress(80)
    push_status("info", "🔬 正在搜索最优参数…")

    try:
        import auto_tune
        from dataset_assets import get_primary_path as ds_get_path

        ref_shp = ds_get_path(ctx["reference_id"])
        if not ref_shp:
            push_status("error", f"❌ 参考真值 {ctx['reference_id']} 文件不存在")
            return False

        result = auto_tune.run_adaptive_tuning(
            source_folder=input_dir,
            mask_folder=mask_out_dir,
            final_out_dir=final_out_dir,
            task_name=actual_task,
            reference_shp_path=ref_shp,
            shp_clip_path=ctx.get("shp_path"),
            task_aoi_shp_path=ctx.get("task_aoi_shp"),
            objective=ctx["objective"],
            logger=push_log,
            progress_callback=push_progress,
            stop_callback=check_stop,
        )

        if result and result.get("best_shp_path"):
            register_asset(actual_task, result["best_prob"], result["best_cnt"], result["best_shp_path"])
            _run_m5_phase(
                ctx, shared, result["best_shp_path"], actual_task,
                result["best_prob"], result["best_cnt"], push_log, check_stop,
            )
            _run_e1_phase(ctx, shared, result["best_shp_path"], actual_task, push_log, check_stop)
            push_status(
                "success",
                f"🏆 最优参数: P={result['best_prob']:.2f} C={result['best_cnt']} | "
                f"交并比={result['best_iou'] * 100:.1f}% F1={result['best_f1'] * 100:.1f}%",
            )
            with shared["lock"]:
                shared["asset_path"] = result["best_shp_path"]
                shared["autotune_result"] = result
            return True
        push_status("warning", "参数优化未能得出结果（可能被中断或无有效真值像元）。")
        return False
    except Exception as e:
        push_log(f"[ERROR] {e}")
        push_status("error", f"参数优化异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def _autotune_worker_entry(ctx, shared, stop_event):
    ok = False
    try:
        ok = run_autotune_sync(ctx, shared, stop_event)
    except Exception as e:
        tb_lines = traceback.format_exc().split("\n")[:25]
        with shared["lock"]:
            lines = list(shared.get("log_lines") or [])
            lines.append(f"[CRASH] {e}")
            lines.extend(tb_lines)
            shared["log_lines"] = lines[-40:]
            shared["status"] = ("error", str(e))
    finally:
        with shared["lock"]:
            shared["success"] = ok
            shared["done"] = True


def maybe_start_pipeline_thread():
    # ---- 自适应优化分支 ----
    at_info = st.session_state.get("pending_autotune")
    if at_info and st.session_state.is_running and not st.session_state.pipeline_thread_started:
        st.session_state.pop("pending_autotune", None)
        st.session_state.pipeline_thread_started = True
        st.session_state.pipeline_log_snapshot = []
        st.session_state.pipeline_progress_value = 0
        st.session_state.executing_pipeline = True
        st.session_state._tl_current_task = at_info["task"]

        stop_ev = threading.Event()
        st.session_state.pipeline_stop_event = stop_ev
        _tl_add(at_info["task"], "EXECUTE", "参数自动优化已启动",
                status="RUNNING", tool="run_autotune", progress=0)
        shared = {
            "lock": threading.Lock(),
            "log_lines": [],
            "progress": 0,
            "status": ("info", "🔬 正在启动参数优化线程…"),
            "done": False,
            "success": False,
            "asset_path": None,
            "autotune_result": None,
            "m5_report": None,
            "e1_report": None,
        }
        st.session_state.pipeline_shared = shared
        ctx = {
            "root_dir": root_dir,
            "mask_root": mask_root,
            "final_root": final_root,
            "shp_path": shp_path,
            "task_options": list(task_options),
            "task": at_info["task"],
            "reference_id": at_info["reference_id"],
            "objective": at_info["objective"],
            "task_aoi_shp": at_info.get("task_aoi_shp"),
            "m5_enabled": m5_enabled,
            "m5_baseline_shp": m5_baseline_shp,
            "e1_enabled": e1_enabled,
            "e1_data_root": e1_data_root,
            "e1_reference": e1_reference,
            "e1_compare_sources": list(e1_compare_sources),
            "e1_export_maps": e1_export_maps,
            "e1_export_heatmap": e1_export_heatmap,
        }
        threading.Thread(target=_autotune_worker_entry, args=(ctx, shared, stop_ev), daemon=True).start()
        return

    # ---- 常规推理 / M4 / 独立 M5 分支 ----
    if not (st.session_state.pending_task and st.session_state.is_running):
        return
    if st.session_state.pipeline_thread_started:
        return
    task_info = st.session_state.pending_task
    st.session_state.pending_task = None
    st.session_state.pipeline_thread_started = True
    st.session_state.pipeline_log_snapshot = []
    st.session_state.pipeline_progress_value = 0
    st.session_state.executing_pipeline = True
    st.session_state._tl_current_task = task_info.get("task") or "unknown"

    stop_ev = threading.Event()
    st.session_state.pipeline_stop_event = stop_ev
    _mode_txt = {"m4": "获取卫星影像", "index": "指数法提取", "dl": "深度学习提取"}.get(
        task_info.get("mode"), task_info.get("mode") or "提取"
    )
    _tl_add(task_info.get("task") or "unknown", "EXECUTE",
            f"任务已启动（{_mode_txt}）",
            status="RUNNING", tool="run_pipeline", progress=0)
    shared = {
        "lock": threading.Lock(),
        "log_lines": [],
        "progress": 0,
        "status": ("info", "正在启动后台线程…"),
        "done": False,
        "success": False,
        "asset_path": None,
        "m5_report": None,
        "m5_verification": None,
        "e1_report": None,
        "job_kind": task_info.get("mode"),
    }
    st.session_state.pipeline_shared = shared

    # 本地潮滩推理可信执行闭环（不进入 run_pipeline_sync 旧路径）
    if task_info.get("inference_plan") or task_info.get("mode") == "dl_inference":
        shared["status"] = ("info", "正在启动潮滩智能提取…")
        _tl_add(task_info.get("task") or "unknown", "INFERENCE",
                "潮滩智能提取已启动", status="RUNNING", tool="run_inference", progress=0)
        ctx = {
            "root_dir": root_dir,
            "final_root": final_root,
            "mask_root": mask_root,
            "model_path": model_path,
            "shp_path": shp_path,
            "task_options": list(task_options),
            "task": task_info.get("task"),
            "inference_plan": task_info.get("inference_plan"),
        }
        threading.Thread(
            target=_inference_worker_entry,
            args=(ctx, shared, stop_ev),
            daemon=True,
        ).start()
        return

    # GEE 影像下载可信执行闭环（不跑推理）
    if task_info.get("mode") == "gee":
        shared["status"] = ("info", "正在启动影像获取…")
        _tl_add(task_info.get("task") or "unknown", "GEE_EXPORT",
                "影像获取已启动", status="RUNNING", tool="run_gee_download", progress=0)
        ctx = {
            "root_dir": root_dir,
            "task": task_info.get("task"),
            "gee_plan": task_info.get("gee_plan"),
        }
        threading.Thread(
            target=_gee_worker_entry,
            args=(ctx, shared, stop_ev),
            daemon=True,
        ).start()
        return

    # 独立 M5 闭环（不跑推理）
    if task_info.get("mode") == "m5":
        shared["status"] = ("info", "正在启动潮滩变化分析…")
        ctx = {
            "root_dir": root_dir,
            "final_root": final_root,
            "task_options": list(task_options),
            "task": task_info.get("task"),
            "prob": task_info.get("prob"),
            "cnt": task_info.get("cnt"),
            "m5": task_info.get("m5") or {},
            "m5_baseline_shp": m5_baseline_shp,
        }
        threading.Thread(
            target=_m5_worker_entry,
            args=(ctx, shared, stop_ev),
            daemon=True,
        ).start()
        return

    # 独立 E1 闭环（不跑推理）
    if task_info.get("mode") == "e1":
        shared["status"] = ("info", "正在启动潮滩精度评价…")
        shared["e1_verification"] = None
        ctx = {
            "root_dir": root_dir,
            "final_root": final_root,
            "task_options": list(task_options),
            "task": task_info.get("task"),
            "prob": task_info.get("prob"),
            "cnt": task_info.get("cnt"),
            "task_aoi_shp": task_aoi_shp,
            "e1": task_info.get("e1") or {},
            "e1_data_root": e1_data_root,
            "e1_reference": e1_reference,
            "e1_compare_sources": list(e1_compare_sources),
            "e1_export_maps": e1_export_maps,
            "e1_export_heatmap": e1_export_heatmap,
        }
        threading.Thread(
            target=_e1_worker_entry,
            args=(ctx, shared, stop_ev),
            daemon=True,
        ).start()
        return

    # 端到端潮滩分析 Workflow（复用子闭环编排）
    if task_info.get("mode") == "workflow":
        shared["status"] = ("info", "正在启动一键潮滩分析（获取影像→提取→评价/变化→报告）…")
        ctx = {
            "root_dir": root_dir,
            "final_root": final_root,
            "mask_root": mask_root,
            "model_path": model_path,
            "shp_path": shp_path,
            "e1_data_root": e1_data_root,
            "e1_reference": e1_reference,
            "task": task_info.get("task"),
            "workflow_plan": task_info.get("workflow_plan"),
            # _active_aoi 可能是 AOIContext 对象，先序列化为 dict 再交给编排器
            "aoi": _aoi_state_to_dict(st.session_state),
            "registry": None,
            "registry_path": os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "assets_registry.json"),
            "report_output_dir": None,
            "baseline_task": None,
        }
        threading.Thread(
            target=_workflow_worker_entry,
            args=(ctx, shared, stop_ev),
            daemon=True,
        ).start()
        return

    ctx = {
        "root_dir": root_dir,
        "mask_root": mask_root,
        "final_root": final_root,
        "model_path": model_path,
        "shp_path": shp_path,
        "task_options": list(task_options),
        "task": task_info.get("task"),
        "prob": task_info.get("prob", 0.05),
        "cnt": task_info.get("cnt", 2),
        "mode": task_info.get("mode", "dl"),
        "points_shp": task_info.get("points_shp"),
        "force_rerun": task_info.get("force_rerun", False),
        "m4": task_info.get("m4"),
        "task_aoi_shp": task_aoi_shp,
        "m5_enabled": m5_enabled,
        "m5_baseline_shp": m5_baseline_shp,
        "e1_enabled": e1_enabled,
        "e1_data_root": e1_data_root,
        "e1_reference": e1_reference,
        "e1_compare_sources": list(e1_compare_sources),
        "e1_export_maps": e1_export_maps,
        "e1_export_heatmap": e1_export_heatmap,
    }
    threading.Thread(
        target=_pipeline_worker_entry,
        args=(ctx, shared, stop_ev),
        daemon=True,
    ).start()


def _pipeline_monitor_inner():
    shared = st.session_state.get("pipeline_shared")
    if shared and shared.get("done") and st.session_state.is_running:
        if finalize_background_pipeline():
            st.rerun()
        return

    if shared:
        with shared["lock"]:
            lines = list(shared.get("log_lines") or [])
            prog = int(shared.get("progress", 0))
            status = shared.get("status", ("info", ""))
    else:
        lines = list(st.session_state.get("pipeline_log_snapshot") or [])
        prog = int(st.session_state.get("pipeline_progress_value", 0))
        status = ("info", "")

    st.markdown('<div class="deck-section-title">⏳ 任务执行状态</div>', unsafe_allow_html=True)
    st.progress(min(100, max(0, prog)))
    if isinstance(status, (list, tuple)) and len(status) >= 2:
        kind, text = status[0], status[1]
    else:
        kind, text = "info", ""
    if text:
        if kind == "error":
            st.error(text)
        elif kind == "success":
            st.success(text)
        elif kind == "warning":
            st.warning(text)
        else:
            st.info(text)
    elif st.session_state.is_running:
        st.caption("后台任务运行中，日志与进度将自动刷新…")

    st.markdown('<div class="deck-section-title">🖥️ 系统终端日志</div>', unsafe_allow_html=True)
    with st.container(height=LOG_PANEL_HEIGHT, border=False):
        if lines:
            st.code("\n".join(lines), language="bash")
        elif st.session_state.is_running:
            st.caption("任务启动中…")
        else:
            st.caption("暂无日志。运行提取或影像获取后，终端输出将显示在此处。")

    # ---- Phase C: 任务时间线（倒序事件 + 阶段/状态徽章）----
    try:
        _tl = _get_task_timeline()
        _tl_events = _tl.events(limit=12)
        if _tl_events:
            with st.expander(f"📋 任务进度（{len(_tl_events)}）", expanded=False):
                if _tl.restored_from == "disk":
                    st.caption("历史记录（进程重启后恢复），非实时状态")
                _status_icons = {
                    "PENDING": "⏳", "WAITING_CONFIRMATION": "❓",
                    "QUEUED": "🕓", "RUNNING": "🔵", "SUCCEEDED": "✅",
                    "FAILED": "❌", "BLOCKED": "⛔", "CANCELLED": "🚫",
                    "WARNING": "⚠️",
                }
                for _ev in reversed(_tl_events):
                    _icon = _status_icons.get(_ev.status, "•")
                    _pct = f" {_ev.progress}%" if _ev.progress is not None else ""
                    st.markdown(
                        f"`{_ev.updated_at[11:19]}` {_icon} **{uil.get_phase_label(_ev.phase)}**/"
                        f"{uil.get_status_label(_ev.status)} {_ev.message}{_pct}"
                    )
                # ---- Phase E: PDF 报告入口（任务完成后生成）----
                _tl_col1, _tl_col2 = st.columns(2)
                with _tl_col1:
                    if st.button("📄 生成成果报告", key="_btn_gen_pdf_report"):
                        _build_pdf_report()
                with _tl_col2:
                    if st.button("🗺️ 生成监测报告", key="_btn_gen_asset_report"):
                        _build_asset_report()
                    _amsg = st.session_state.get("_asset_report_msg")
                    if _amsg:
                        if _amsg.get("level") == "success":
                            st.markdown("✅ **成果报告已生成**")
                            st.code(_amsg.get("path", ""))
                            try:
                                with open(_amsg["path"], "rb") as _pf:
                                    st.download_button(
                                        "⬇️ 下载成果报告",
                                        _pf.read(),
                                        file_name=os.path.basename(_amsg["path"]),
                                        mime="application/pdf",
                                        key="_btn_dl_asset_report",
                                    )
                            except Exception:
                                pass
                        else:
                            st.markdown(f"⚠️ **{_amsg.get('text', '未知错误')}**")
                        for _w in (_amsg.get("warnings") or []):
                            st.caption(_w)
    except Exception:
        pass


# ---- Phase E+: 成果报告生成（集成自 E:\\Code\\pdf report_engine.py，栅格统计 + 参考真值对比）----
def _build_asset_report():
    try:
        import asset_report_engine as _are

        _tl = _get_task_timeline()
        _events = _tl.events(limit=50)
        _task = ""
        if _events:
            for _e in reversed(_events):
                # 顶层 task_id 为权威字段；details.task 为兼容回退
                if getattr(_e, "task_id", None):
                    _task = str(_e.task_id)
                    break
                if isinstance(_e.details, dict) and _e.details.get("task"):
                    _task = str(_e.details["task"])
                    break
        if not _task:
            _task = str(st.session_state.get("selected_task") or "") or ""
        if not _task:
            st.session_state["_asset_report_msg"] = {
                "level": "warning",
                "text": "未识别到目标任务，请在左侧选择目标任务",
            }
            return
        _res = _are.generate_asset_report(
            _task, progress_callback=lambda p, m: None,
        )
        if _res.success and _res.report_path:
            st.session_state["_asset_report_msg"] = {
                "level": "success",
                "text": "✅ 成果报告已生成",
                "path": _res.report_path,
            }
        else:
            _msg = {
                "level": "warning",
                "text": f"成果报告生成失败：{_res.error or '未知错误'}",
            }
            _warns = [("· " + w) for w in (_res.warnings or [])]
            if _warns:
                _msg["warnings"] = _warns
            st.session_state["_asset_report_msg"] = _msg
    except Exception as _re:
        st.session_state["_asset_report_msg"] = {
            "level": "warning",
            "text": f"成果报告生成异常：{_re}",
        }


# ---- Phase E: PDF 报告生成（真实数据：时间线 + 能力 + 资产）----
def _build_pdf_report():
    try:
        import report_generator as _rg

        _tl = _get_task_timeline()
        _events = _tl.events(limit=50)
        if not _events:
            st.warning("无任务进度记录，无法生成报告")
            return
        _last = _events[-1]
        _task_id = _last.task_id or "task_unknown"
        _task_ctx = {"task_id": _task_id}
        _det = {}
        for _e in reversed(_events):
            if isinstance(_e.details, dict):
                _det.update(_e.details)
        _task_ctx.update(
            {
                "task": _det.get("task") or _task_id,
                "mode": _det.get("mode") or "",
                "prob": _det.get("prob"),
                "cnt": _det.get("cnt"),
                "plan_id": _det.get("plan_id"),
            }
        )
        _caps = {}
        try:
            _creg = st.session_state.get("_capability_reg")
            if _creg is not None:
                _caps = _creg.snapshot_for_agent()
        except Exception:
            pass
        _assets = []
        for _e in _events:
            for _a in (_e.artifacts or []):
                _assets.append({"path": str(_a), "kind": "artifact"})
        _res = _rg.generate_task_report(
            _task_ctx, capabilities=_caps, timeline=_events, assets=_assets,
        )
        if _res.success and _res.report_path:
            st.success("✅ 成果报告已生成")
            st.markdown(f"`{_res.report_path}`")
            try:
                with open(_res.report_path, "rb") as _pf:
                    st.download_button(
                        "⬇️ 下载成果报告",
                        _pf.read(),
                        file_name=os.path.basename(_res.report_path),
                        mime="application/pdf",
                        key="_btn_dl_pdf_report",
                    )
            except Exception:
                pass
        else:
            st.warning(f"成果报告生成失败：{_res.error or '未知错误'}")
        for _w in (_res.warnings or []):
            st.caption("· " + _w)
    except Exception as _re:
        st.warning(f"成果报告生成异常：{_re}")


_PIPELINE_USE_FRAGMENT = False
try:
    _pipeline_monitor = st.fragment(run_every=2.5)(_pipeline_monitor_inner)
    _PIPELINE_USE_FRAGMENT = True
except (TypeError, AttributeError):
    _pipeline_monitor = _pipeline_monitor_inner


# ---- 右侧指挥台：填充状态 / 日志面板 ----
maybe_start_pipeline_thread()
if _log_panel_slot is not None:
    with _log_panel_slot:
        _pipeline_monitor()

components.html(
    """
    <script>
    (() => {
      const win = window.parent || window;
      const doc = win.document;
      const setImp = (el, prop, val) => el?.style?.setProperty(prop, val, "important");

      const syncWorkbenchHeight = () => {
        const header = doc.querySelector('[data-testid="stHeader"]');
        const headerH = header ? header.offsetHeight : 56;
        const h = Math.max(480, win.innerHeight - headerH - 6);
        const px = h + "px";
        doc.documentElement.style.setProperty("--workbench-h", px);
        doc.querySelectorAll(
          'div[data-testid="stColumn"]:has(.cockpit-map-col) [data-testid="stIFrame"], ' +
          'div[data-testid="stColumn"]:has(.cockpit-map-col) [data-testid="stIFrame"] iframe'
        ).forEach((el) => {
          if (el.offsetHeight <= 4) return;
          setImp(el, "height", px);
          setImp(el, "max-height", px);
        });
      };

      const lockPageWheel = () => {
        if (win.__cstfWheelLocked) return;
        win.__cstfWheelLocked = true;
        const canScroll = (el) => {
          if (!el || el === doc.documentElement) return false;
          const oy = win.getComputedStyle(el).overflowY;
          if (!["auto", "scroll", "overlay"].includes(oy)) return false;
          return el.scrollHeight > el.clientHeight + 2;
        };
        win.addEventListener(
          "wheel",
          (e) => {
            let el = e.target;
            while (el && el !== doc.body) {
              if (el.dataset?.testid === "stSidebar") return;
              if (canScroll(el)) {
                const top = el.scrollTop <= 0;
                const bottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 1;
                if ((e.deltaY < 0 && top) || (e.deltaY > 0 && bottom)) e.preventDefault();
                return;
              }
              el = el.parentElement;
            }
            e.preventDefault();
          },
          { passive: false, capture: true }
        );
      };

      syncWorkbenchHeight();
      lockPageWheel();
      win.addEventListener("resize", syncWorkbenchHeight);
      [100, 400, 900].forEach((ms) => win.setTimeout(syncWorkbenchHeight, ms));
    })();
    </script>
    """,
    height=0,
)

# 无 st.fragment 时，只能靠整页定时刷新看到后台日志（会略卡顿）
if (
    not _PIPELINE_USE_FRAGMENT
    and st.session_state.is_running
    and st.session_state.get("pipeline_shared")
):
    _ps = st.session_state.pipeline_shared
    if not _ps.get("done"):
        time.sleep(3.0)
        st.rerun()