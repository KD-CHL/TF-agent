"""
Cesium 3D 地球引擎 — 用于 YYnet 中央地理显示区。

- Cesium Ion 高清卫星底图（.env 中 CESIUM_ION_TOKEN）
- 潮滩 SHP → 球面 GeoJSON 叠加
- 潮滩 TIF → localtileserver 瓦片贴图到球面
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

_CESIUM_VER = "1.128"
_CESIUM_JS = f"https://cesium.com/downloads/cesiumjs/releases/{_CESIUM_VER}/Build/Cesium/Cesium.js"
_CESIUM_CSS = f"https://cesium.com/downloads/cesiumjs/releases/{_CESIUM_VER}/Build/Cesium/Widgets/widgets.css"
_CESIUM_NEII_URL = (
    f"https://cesium.com/downloads/cesiumjs/releases/{_CESIUM_VER}/Build/Cesium/Assets/Textures/NaturalEarthII"
)
_BORDERS_GEOJSON_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson"
)

_BASE_IMAGERY_CANDIDATES = [
    {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "maxLevel": 19,
        "credit": "Esri World Imagery",
    },
    {
        "url": "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "maxLevel": 19,
        "credit": "Esri",
    },
    {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "maxLevel": 19,
        "credit": "OpenStreetMap",
    },
    {
        "url": "https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
        "maxLevel": 19,
        "credit": "CARTO",
    },
]

# 默认框选中国大陆（略收紧边界，使视口尽可能铺满中国）
_CHINA_VIEW_RECT = {"west": 78.0, "south": 21.0, "east": 128.0, "north": 50.0}


def zoom_to_height_m(zoom: int, lat: float = 30.0) -> float:
    z = max(1, min(18, int(zoom)))
    lat_r = math.radians(lat)
    h = 40_075_016.686 * math.cos(lat_r) / (256 * (2**z)) * 2.5
    return float(max(800_000, min(h, 12_000_000)))


def view_from_vector_path(path: str) -> Optional[Tuple[float, float, int]]:
    try:
        import geopandas as gpd

        gdf = gpd.read_file(path)
        if gdf.empty:
            return None
        if gdf.crs is not None:
            gdf = gdf.to_crs(4326)
        minx, miny, maxx, maxy = gdf.total_bounds
        return _view_from_bounds(minx, miny, maxx, maxy)
    except Exception:
        return None


def view_from_raster_path(path: str) -> Optional[Tuple[float, float, int]]:
    try:
        import rasterio
        from rasterio.warp import transform_bounds

        with rasterio.open(path) as ds:
            b = ds.bounds
            crs = ds.crs
        if crs is not None:
            west, south, east, north = transform_bounds(crs, "EPSG:4326", b.left, b.bottom, b.right, b.top)
        else:
            west, south, east, north = b.left, b.bottom, b.right, b.top
        return _view_from_bounds(west, south, east, north)
    except Exception:
        return None


def view_from_asset_path(path: str) -> Optional[Tuple[float, float, int]]:
    if not path or not os.path.isfile(path):
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext == ".shp":
        return view_from_vector_path(path)
    if ext in {".tif", ".tiff"}:
        return view_from_raster_path(path)
    return None


def _view_from_bounds(west: float, south: float, east: float, north: float) -> Tuple[float, float, int]:
    lat = (south + north) / 2.0
    lon = (west + east) / 2.0
    span = max(east - west, north - south)
    if span > 8:
        zoom = 5
    elif span > 3:
        zoom = 7
    elif span > 1:
        zoom = 9
    elif span > 0.3:
        zoom = 11
    elif span > 0.08:
        zoom = 13
    else:
        zoom = 15
    return lat, lon, zoom


def bounds_from_asset_path(path: str) -> Optional[Tuple[float, float, float, float]]:
    try:
        import geopandas as gpd
        import rasterio
        from rasterio.warp import transform_bounds
    except ImportError:
        return None
    if not path or not os.path.isfile(path):
        return None
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".shp":
            gdf = gpd.read_file(path)
            if gdf.empty:
                return None
            gdf = gdf.to_crs(4326)
            minx, miny, maxx, maxy = gdf.total_bounds
            return float(minx), float(miny), float(maxx), float(maxy)
        if ext in {".tif", ".tiff"}:
            with rasterio.open(path) as ds:
                b = ds.bounds
                crs = ds.crs
            if crs is not None:
                w, s, e, n = transform_bounds(crs, "EPSG:4326", b.left, b.bottom, b.right, b.top)
            else:
                w, s, e, n = b.left, b.bottom, b.right, b.top
            return float(w), float(s), float(e), float(n)
    except Exception:
        return None
    return None


def _simplify_geojson(geojson: dict, max_features: int = 8000) -> dict:
    feats = geojson.get("features") or []
    if len(feats) <= max_features:
        return geojson
    step = max(1, len(feats) // max_features)
    geojson = dict(geojson)
    geojson["features"] = feats[::step][:max_features]
    return geojson


def load_shp_geojson(path: str, simplify_tolerance: Optional[float] = None) -> Optional[dict]:
    try:
        import geopandas as gpd

        gdf = gpd.read_file(path)
        if gdf.empty:
            return None
        gdf = gdf.to_crs(4326)
        minx, miny, maxx, maxy = gdf.total_bounds
        span = max(maxx - minx, maxy - miny)
        tol = simplify_tolerance
        if tol is None:
            if span > 2:
                tol = 0.01
            elif span > 0.5:
                tol = 0.002
            elif span > 0.1:
                tol = 0.0005
            else:
                tol = 0.0001
        if tol and tol > 0:
            gdf = gdf.copy()
            gdf["geometry"] = gdf.geometry.simplify(tol, preserve_topology=True)
        geojson = json.loads(gdf.to_json())
        return _simplify_geojson(geojson)
    except Exception:
        return None


def _nodata_safe(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if isinstance(value, (float, np.floating)) and (np.isnan(value) or np.isinf(value)):
            return None
    except (TypeError, ValueError):
        return None
    return float(value)


def infer_raster_tile_params(path: str) -> dict:
    """与 2D 地图一致：单波段成果用 Reds 色图，nodata=0 透明背景。"""
    out: dict[str, Any] = {"indexes": None, "colormap": None, "nodata": None}
    try:
        import rasterio

        with rasterio.open(path) as ds:
            nb = int(ds.count)
            nd = _nodata_safe(ds.nodata)
            if nb == 1:
                out["indexes"] = 1
                out["colormap"] = "reds"
                if nd is None:
                    dt = str(ds.dtypes[0])
                    if dt.startswith(("uint", "int")) or "float" in dt:
                        nd = 0.0
                out["nodata"] = nd
    except Exception:
        pass
    return out


def _tile_client_alive(client: Any) -> bool:
    try:
        import urllib.request

        raw = client.get_tile_url().replace("localhost", "127.0.0.1")
        test = raw.replace("{z}", "0").replace("{x}", "0").replace("{y}", "0")
        with urllib.request.urlopen(test, timeout=4) as resp:
            return int(resp.status) == 200
    except Exception:
        return False


def get_raster_tile_overlay(
    path: str,
    tile_clients: Dict[str, Any],
    globe_port: Optional[int] = None,
) -> Optional[dict]:
    try:
        from localtileserver import TileClient
    except ImportError:
        return None
    if not path or not os.path.isfile(path):
        return None
    try:
        mt = os.path.getmtime(path)
    except OSError:
        mt = 0
    key = f"{os.path.normpath(os.path.abspath(path))}|{mt:.4f}"
    client = tile_clients.get(key)
    if client is None:
        try:
            client = TileClient(path, host="127.0.0.1")
            tile_clients[key] = client
        except Exception:
            return None

    params = infer_raster_tile_params(path)
    url_kwargs: dict[str, Any] = {}
    if params.get("indexes") is not None:
        url_kwargs["indexes"] = params["indexes"]
    if params.get("colormap"):
        url_kwargs["colormap"] = params["colormap"]
    if params.get("nodata") is not None:
        url_kwargs["nodata"] = params["nodata"]

    bounds = client.bounds()
    south, north, west, east = bounds
    raw_url = client.get_tile_url(**url_kwargs).replace("localhost", "127.0.0.1")
    if globe_port:
        import globe_server as gs

        token = gs.register_tile_template(key, raw_url)
        url = gs.overlay_tile_url(globe_port, token)
    else:
        url = raw_url
    return {
        "url": url,
        "west": float(west),
        "south": float(south),
        "east": float(east),
        "north": float(north),
        "min_zoom": int(getattr(client, "min_zoom", 0) or 0),
        "max_zoom": int(getattr(client, "max_zoom", 18) or 18),
    }


def find_e1_overlay_path(e1_report: Optional[dict], prefer: str = "heatmap") -> Optional[str]:
    if not e1_report:
        return None
    for _pair, metrics in (e1_report.get("comparisons") or {}).items():
        causal = metrics.get("causal_analysis") or {}
        maps = causal.get("disagreement_maps") or {}
        for key in (prefer, "heatmap", "consensus", "class"):
            p = maps.get(key)
            if p and os.path.isfile(p):
                return p
    mp = e1_report.get("multi_product_heatmap") or {}
    for key in ("any_disagreement_tif", "agreement_count_tif"):
        p = mp.get(key)
        if p and os.path.isfile(p):
            return p
    return None


def build_globe_payload(
    center: Tuple[float, float],
    zoom: int,
    result_path: Optional[str] = None,
    opacity_pct: float = 50.0,
    pitch_deg: float = -35.0,
    e1_report: Optional[dict] = None,
    show_e1_overlay: bool = False,
    tile_clients: Optional[Dict[str, Any]] = None,
    ion_token: Optional[str] = None,
    show_borders: bool = True,
    globe_port: Optional[int] = None,
) -> dict:
    lat, lon = float(center[0]), float(center[1])
    tile_clients = tile_clients if tile_clients is not None else {}

    payload: Dict[str, Any] = {
        "center": {"lat": lat, "lon": lon},
        "height": zoom_to_height_m(zoom, lat),
        "pitch": float(pitch_deg),
        "heading": 0.0,
        "flyRectangle": None,
        "geojsonLayers": [],
        "imageryLayers": [],
        "opacity": max(0.05, min(1.0, opacity_pct / 100.0)),
        "ionToken": (ion_token or "").strip() or None,
        "showBorders": bool(show_borders),
        "bordersUrl": _BORDERS_GEOJSON_URL,
        "naturalEarthUrl": _CESIUM_NEII_URL,
        "assetName": None,
        "chinaView": dict(_CHINA_VIEW_RECT),
    }

    rects: List[Tuple[float, float, float, float]] = []

    if result_path and os.path.isfile(result_path):
        ext = os.path.splitext(result_path)[1].lower()
        name = os.path.splitext(os.path.basename(result_path))[0]
        payload["assetName"] = name
        if ext == ".shp":
            gj = load_shp_geojson(result_path)
            if gj:
                payload["geojsonLayers"].append(
                    {"name": name, "data": gj, "color": "#e41a1c", "alpha": payload["opacity"]}
                )
            b = bounds_from_asset_path(result_path)
            if b:
                rects.append(b)
        elif ext in {".tif", ".tiff"}:
            tile = get_raster_tile_overlay(result_path, tile_clients, globe_port=globe_port)
            if tile:
                payload["imageryLayers"].append(
                    {
                        "name": name,
                        "url": tile["url"],
                        "west": tile["west"],
                        "south": tile["south"],
                        "east": tile["east"],
                        "north": tile["north"],
                        "minZoom": tile["min_zoom"],
                        "maxZoom": tile["max_zoom"],
                        "alpha": payload["opacity"],
                    }
                )
                rects.append((tile["west"], tile["south"], tile["east"], tile["north"]))

    if show_e1_overlay and e1_report:
        e1_path = find_e1_overlay_path(e1_report)
        if e1_path and os.path.isfile(e1_path):
            ext = os.path.splitext(e1_path)[1].lower()
            if ext == ".shp":
                gj = load_shp_geojson(e1_path)
                if gj:
                    payload["geojsonLayers"].append(
                        {"name": "E1", "data": gj, "color": "#ff6b35", "alpha": 0.65}
                    )
            elif ext in {".tif", ".tiff"}:
                tile = get_raster_tile_overlay(e1_path, tile_clients, globe_port=globe_port)
                if tile:
                    payload["imageryLayers"].append(
                        {
                            "name": "E1",
                            "url": tile["url"],
                            "west": tile["west"],
                            "south": tile["south"],
                            "east": tile["east"],
                            "north": tile["north"],
                            "minZoom": tile["min_zoom"],
                            "maxZoom": tile["max_zoom"],
                            "alpha": 0.7,
                        }
                    )
                    rects.append((tile["west"], tile["south"], tile["east"], tile["north"]))

    if rects:
        west = min(r[0] for r in rects)
        south = min(r[1] for r in rects)
        east = max(r[2] for r in rects)
        north = max(r[3] for r in rects)
        pad_lon = max(0.02, (east - west) * 0.08)
        pad_lat = max(0.02, (north - south) * 0.08)
        payload["flyRectangle"] = {
            "west": west - pad_lon,
            "south": south - pad_lat,
            "east": east + pad_lon,
            "north": north + pad_lat,
        }
    elif result_path:
        auto = view_from_asset_path(result_path)
        if auto:
            payload["center"] = {"lat": auto[0], "lon": auto[1]}
            payload["height"] = zoom_to_height_m(auto[2], auto[0])

    return payload


def _json_for_script(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def build_cesium_html(payload: dict, height_px: int = 700, full_viewport: bool = True) -> str:
    cfg = _json_for_script(payload)
    imagery_candidates = _json_for_script(_BASE_IMAGERY_CANDIDATES)
    if full_viewport:
        size_rule = "width: 100%; height: 100%;"
        container_rule = "width: 100%; height: 100%;"
    else:
        h = int(max(480, height_px))
        size_rule = f"width: 100%; height: {h}px;"
        container_rule = size_rule
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <link rel="stylesheet" href="{_CESIUM_CSS}"/>
  <script src="{_CESIUM_JS}"></script>
  <style>
    html, body {{
      {size_rule} margin: 0; padding: 0; overflow: hidden;
      background: #02040a;
    }}
    #cesiumContainer {{
      {container_rule} margin: 0; padding: 0; overflow: hidden;
    }}
    .cesium-viewer,
    .cesium-viewer-cesiumWidgetContainer,
    .cesium-widget,
    .cesium-widget canvas {{
      width: 100% !important;
      height: 100% !important;
      display: block;
    }}
    #cesiumError {{
      display: none; position: absolute; top: 12px; left: 12px; right: 12px;
      padding: 10px 14px; background: rgba(120, 20, 20, 0.92); color: #fff;
      font: 13px/1.4 sans-serif; border-radius: 4px; z-index: 9999;
    }}
    #cesiumStatus {{
      position: fixed; bottom: 10px; left: 10px; z-index: 99999;
      padding: 5px 10px; background: rgba(8, 14, 28, 0.82); color: #b8c8e8;
      font: 12px/1.35 sans-serif; border-radius: 6px; border: 1px solid #2a3a55;
      pointer-events: none;
    }}
    .cesium-viewer-bottom {{ display: none !important; }}
    .cesium-viewer .cesium-widget-credits {{ font-size: 10px; opacity: 0.55; }}
  </style>
</head>
<body>
<div id="cesiumError"></div>
<div id="cesiumStatus">地球初始化中…</div>
<div id="cesiumContainer"></div>
<script>
(async function() {{
  const CFG = {cfg};
  const IMAGERY_CANDIDATES = {imagery_candidates};

  function setStatus(msg) {{
    const el = document.getElementById("cesiumStatus");
    if (el) el.textContent = msg;
  }}

  function showError(msg) {{
    const el = document.getElementById("cesiumError");
    if (el) {{ el.style.display = "block"; el.textContent = msg; }}
    setStatus("底图异常");
    console.error(msg);
  }}

  function disableDynamicLighting(viewer) {{
    viewer.scene.globe.enableLighting = false;
    viewer.scene.globe.showGroundAtmosphere = false;
    viewer.scene.highDynamicRange = false;
    if (viewer.scene.fog) viewer.scene.fog.enabled = false;
    if (viewer.scene.skyAtmosphere) viewer.scene.skyAtmosphere.show = false;
    if (viewer.scene.sun) viewer.scene.sun.show = false;
    if (viewer.scene.moon) viewer.scene.moon.show = false;
    if (viewer.scene.skyBox) viewer.scene.skyBox.show = false;
    if (viewer.scene.atmosphere && Cesium.DynamicAtmosphereLightingType) {{
      viewer.scene.atmosphere.dynamicLighting = Cesium.DynamicAtmosphereLightingType.NONE;
    }}
    if (viewer.scene.globe.dynamicAtmosphereLighting !== undefined) {{
      viewer.scene.globe.dynamicAtmosphereLighting = false;
    }}
  }}

  async function tryAddImagery(viewer, provider, label) {{
    try {{
      viewer.imageryLayers.addImageryProvider(provider);
      setStatus("底图 · " + label);
      return true;
    }} catch (e) {{
      console.warn("imagery add failed:", label, e);
      return false;
    }}
  }}

  async function setupBaseImagery(viewer) {{
    viewer.imageryLayers.removeAll();
    if (CFG.ionToken) {{
      Cesium.Ion.defaultAccessToken = CFG.ionToken;
      try {{
        const ionProvider = await Cesium.IonImageryProvider.fromAssetId(2);
        if (await tryAddImagery(viewer, ionProvider, "Cesium Ion 卫星")) return;
      }} catch (e) {{ console.warn("Ion asset 2 failed", e); }}
      try {{
        const worldProvider = await Cesium.createWorldImageryAsync({{
          style: Cesium.IonWorldImageryStyle.AERIAL,
        }});
        if (await tryAddImagery(viewer, worldProvider, "Cesium Ion World")) return;
      }} catch (e) {{ console.warn("createWorldImageryAsync failed", e); }}
    }}
    for (let i = 0; i < IMAGERY_CANDIDATES.length; i++) {{
      const item = IMAGERY_CANDIDATES[i];
      try {{
        const p = new Cesium.UrlTemplateImageryProvider({{
          url: item.url,
          maximumLevel: item.maxLevel || 18,
          credit: item.credit || "",
        }});
        if (await tryAddImagery(viewer, p, item.credit || ("fallback-" + i))) return;
      }} catch (e) {{ console.warn("url imagery failed", i, e); }}
    }}
    try {{
      const neii = await Cesium.TileMapServiceImageryProvider.fromUrl(
        Cesium.buildModuleUrl("Assets/Textures/NaturalEarthII")
      );
      if (await tryAddImagery(viewer, neii, "Natural Earth II")) return;
    }} catch (e) {{ console.warn("NEII failed", e); }}
    showError("底图加载失败，请检查 CESIUM_ION_TOKEN 或网络。");
  }}

  let viewer;
  try {{
    viewer = new Cesium.Viewer("cesiumContainer", {{
      animation: false,
      timeline: false,
      geocoder: !!CFG.ionToken,
      homeButton: true,
      sceneModePicker: true,
      baseLayerPicker: false,
      navigationHelpButton: true,
      fullscreenButton: true,
      infoBox: false,
      selectionIndicator: false,
      terrainProvider: new Cesium.EllipsoidTerrainProvider(),
      shouldAnimate: false,
      skyBox: false,
      skyAtmosphere: false,
      requestRenderMode: false,
      baseLayer: false,
    }});
  }} catch (err) {{
    showError("地球初始化失败: " + err);
    return;
  }}

  disableDynamicLighting(viewer);
  viewer.scene.mode = Cesium.SceneMode.SCENE3D;
  viewer.scene.backgroundColor = Cesium.Color.fromBytes(2, 4, 12, 255);
  viewer.scene.globe.show = true;
  viewer.scene.globe.depthTestAgainstTerrain = false;
  if (viewer.cesiumWidget && viewer.cesiumWidget.container) {{
    viewer.cesiumWidget.container.style.width = "100%";
    viewer.cesiumWidget.container.style.height = "100%";
  }}

  await setupBaseImagery(viewer);

  function hexToCesiumColor(hex, alpha) {{
    const h = hex.replace("#", "");
    return Cesium.Color.fromBytes(
      parseInt(h.substring(0, 2), 16),
      parseInt(h.substring(2, 4), 16),
      parseInt(h.substring(4, 6), 16),
      Math.round((alpha || 0.5) * 255)
    );
  }}

  (CFG.geojsonLayers || []).forEach(function(layer) {{
    Cesium.GeoJsonDataSource.load(layer.data, {{
      clampToGround: true,
      stroke: hexToCesiumColor(layer.color || "#e41a1c", 0.95),
      fill: hexToCesiumColor(layer.color || "#e41a1c", layer.alpha || 0.5),
      strokeWidth: 2,
    }}).then(function(ds) {{
      ds.name = layer.name || "layer";
      viewer.dataSources.add(ds);
    }}).catch(function(err) {{ console.warn("GeoJSON load failed", err); }});
  }});

  (CFG.imageryLayers || []).forEach(function(layer) {{
    try {{
      const rect = Cesium.Rectangle.fromDegrees(layer.west, layer.south, layer.east, layer.north);
      const provider = new Cesium.UrlTemplateImageryProvider({{
        url: layer.url,
        rectangle: rect,
        minimumLevel: layer.minZoom || 0,
        maximumLevel: layer.maxZoom || 18,
      }});
      const imgLayer = viewer.imageryLayers.addImageryProvider(provider);
      imgLayer.alpha = layer.alpha != null ? layer.alpha : 0.75;
    }} catch (err) {{ console.warn("overlay imagery failed", err); }}
  }});

  function rectFromCfg(box) {{
    return Cesium.Rectangle.fromDegrees(box.west, box.south, box.east, box.north);
  }}

  function flyToRect(rect, duration) {{
    // 矩形 flyTo 由 Cesium 自动计算距离，使目标区域尽可能铺满视口
    viewer.camera.flyTo({{ destination: rect, duration: duration || 0 }});
  }}

  function applyCameraView() {{
    viewer.resize();
    if (viewer.cesiumWidget && viewer.cesiumWidget.container) {{
      viewer.cesiumWidget.container.style.width = "100%";
      viewer.cesiumWidget.container.style.height = "100%";
    }}
    const cv = CFG.chinaView || {{ west: 78, south: 21, east: 128, north: 50 }};
    const chinaRect = rectFromCfg(cv);
    Cesium.Camera.DEFAULT_VIEW_RECTANGLE = chinaRect;

    if (CFG.flyRectangle) {{
      flyToRect(rectFromCfg(CFG.flyRectangle), 0.8);
      if (CFG.assetName) {{
        const base = document.getElementById("cesiumStatus")?.textContent || "底图就绪";
        setStatus(base + " · 已加载 " + CFG.assetName);
      }}
      return;
    }}

    flyToRect(chinaRect, 0);
    const base = document.getElementById("cesiumStatus")?.textContent || "底图就绪";
    setStatus(base + " · 中国视角");
  }}

  requestAnimationFrame(applyCameraView);
  setTimeout(applyCameraView, 120);
  setTimeout(applyCameraView, 500);
}})();
</script>
</body>
</html>"""
