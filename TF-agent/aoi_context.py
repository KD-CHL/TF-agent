# -*- coding: utf-8 -*-
"""
地图 AOI 双向交互（Phase D）。

- AOIContext：来源分类（map_click/map_rectangle/map_polygon/current_view/
  asset_geometry/named_location）、GeoJSON Polygon 几何、geodesic 面积、校验。
- validate_aoi：拒绝 NaN/Inf、顶点 <3、bbox 越界、面积 ≤0；自相交 make_valid
  修复；跨 180° 经线 warning。
- 摘要注入：紧凑文本（无完整 GeoJSON、无敏感路径）。
"""
from __future__ import annotations

import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from pyproj import Geod

    _GEOD = Geod(ellps="WGS84")
    _HAS_PYPROJ = True
except Exception:  # pragma: no cover - 环境缺失时降级平面面积
    _GEOD = None
    _HAS_PYPROJ = False

_LAT_RANGE = (-90.0, 90.0)
_LON_RANGE = (-180.0, 180.0)
_MAX_VERTICES_DEFAULT = 2000

VALID_SOURCES = (
    "map_click",
    "map_rectangle",
    "map_polygon",
    "current_view",
    "asset_geometry",
    "named_location",
)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _is_finite(v: Any) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def geodesic_area_km2(coords) -> float:
    """WGS84 大地测量面积（km²）。不支持时降级平面近似。"""
    if _HAS_PYPROJ and _GEOD is not None and len(coords) >= 3:
        try:
            lons = [float(c[0]) for c in coords]
            lats = [float(c[1]) for c in coords]
            area_m2, _perim = _GEOD.polygon_area_perimeter(lons, lats)
            return abs(area_m2) / 1e6
        except Exception:
            pass
    # 平面近似（不等积投影仅作兜底，设计文档要求 geodesic 优先）
    n = len(coords) - 1
    area = 0.0
    for i in range(n):
        x1, y1 = coords[i][0], coords[i][1]
        x2, y2 = coords[i + 1][0], coords[i + 1][1]
        area += (x2 - x1) * (y2 + y1)
    return abs(area) / 2.0 * (111.32 ** 2)


def _repair_self_intersection(geometry: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """尝试 shapely make_valid 修复自相交；失败保留原几何 + warning。"""
    warnings: List[str] = []
    try:
        from shapely.geometry import shape
        from shapely.validation import make_valid

        geom = shape(geometry)
        if geom.is_valid:
            return geometry, warnings
        fixed = make_valid(geom)
        if fixed.is_empty or fixed.area <= 0:
            warnings.append("自相交修复失败，保留原几何（标记 invalid）")
            return geometry, warnings
        import json

        fixed_geojson = json.loads(
            json.dumps(fixed.__geo_interface__) if hasattr(fixed, "__geo_interface__") else "{}"
        )
        # 修复为 MultiPolygon 时归一为最大子面 Polygon
        if fixed_geojson.get("type") == "MultiPolygon":
            polys = fixed_geojson.get("coordinates", [])
            if polys:
                largest = max(polys, key=lambda p: abs(geodesic_area_km2(p[0])))
                fixed_geojson = {"type": "Polygon", "coordinates": largest}
        warnings.append("检测到自相交，已尝试修复")
        return fixed_geojson, warnings
    except Exception:
        warnings.append("自相交检测不可用，保留原几何")
        return geometry, warnings


def _validate_bbox(
    geometry: Dict[str, Any], cross_antimeridian_hint: bool = False
) -> Tuple[bool, List[str], Tuple[float, float, float, float]]:
    ring = geometry.get("coordinates", [[]])[0] if geometry.get("coordinates") else []
    warnings: List[str] = []
    lons = [float(p[0]) for p in ring if _is_finite(p[0])]
    lats = [float(p[1]) for p in ring if _is_finite(p[1])]
    if not lons or not lats:
        return False, ["坐标缺失或不可用"], (0.0, 0.0, 0.0, 0.0)
    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)
    ok = True
    if west < _LON_RANGE[0] or east > _LON_RANGE[1]:
        ok = False
        warnings.append(f"经度越界: {west}..{east}")
    if south < _LAT_RANGE[0] or north > _LAT_RANGE[1]:
        ok = False
        warnings.append(f"纬度越界: {south}..{north}")
    if cross_antimeridian_hint or (west > east and (west - east) > 180):
        warnings.append("跨 180° 经线（未切片，仅提示）")
    return ok, warnings, (west, south, east, north)


def validate_aoi(
    geometry: Dict[str, Any],
    *,
    source: str,
    aoi_id: Optional[str] = None,
    label: Optional[str] = None,
    max_vertices: int = _MAX_VERTICES_DEFAULT,
    cross_antimeridian_hint: bool = False,
) -> AOIContext:
    """校验并构造 AOIContext。返回的 valid=False 表示不可用（不回声、不注入推荐）。"""
    warnings: List[str] = []
    if source not in VALID_SOURCES:
        warnings.append(f"未知来源: {source}")
        source = "map_polygon"

    if not geometry or geometry.get("type") != "Polygon":
        return AOIContext(
            aoi_id=aoi_id or uuid.uuid4().hex, source=source, geometry=geometry or {},
            bbox=(0.0, 0.0, 0.0, 0.0), centroid=(0.0, 0.0), area_km2=0.0,
            valid=False, warnings=warnings + ["几何必须为 GeoJSON Polygon"],
            created_at=_now_str(), label=label,
        )

    ring = geometry.get("coordinates", [[]])[0] if geometry.get("coordinates") else []
    if not ring or len(ring) < 4:
        warnings.append("顶点数 < 3（需 ≥3 个不同点并闭合）")
    # 有限性检查
    non_finite = [p for p in ring if not (_is_finite(p[0]) and _is_finite(p[1]))]
    if non_finite:
        warnings.append(f"坐标含 NaN/Inf（{len(non_finite)} 个顶点），已拒绝")
    ring_finite = [p for p in ring if _is_finite(p[0]) and _is_finite(p[1])]
    if len(ring_finite) < 4:
        return AOIContext(
            aoi_id=aoi_id or uuid.uuid4().hex, source=source, geometry=geometry,
            bbox=(0.0, 0.0, 0.0, 0.0), centroid=(0.0, 0.0), area_km2=0.0,
            valid=False, warnings=warnings, created_at=_now_str(), label=label,
        )
    # 顶点数上限 → 降采样
    if len(ring_finite) > max_vertices:
        step = math.ceil(len(ring_finite) / max_vertices)
        ring_finite = ring_finite[::step]
        if ring_finite[0] != ring_finite[-1]:
            ring_finite = ring_finite + [ring_finite[0]]
        warnings.append(f"顶点数超上限，已降采样至 {len(ring_finite)}")
    # 去重首尾闭合点后统计唯一顶点
    unique_pts = ring_finite[:-1] if ring_finite and ring_finite[0] == ring_finite[-1] else ring_finite
    if len(unique_pts) < 3:
        warnings.append("有效顶点数 < 3")

    geometry = {"type": "Polygon", "coordinates": [ring_finite]}
    geometry, repair_warnings = _repair_self_intersection(geometry)
    warnings.extend(repair_warnings)

    bbox_ok, bbox_warnings, bbox = _validate_bbox(geometry, cross_antimeridian_hint)
    warnings.extend(bbox_warnings)

    area_km2 = geodesic_area_km2(geometry["coordinates"][0])
    if area_km2 <= 0:
        warnings.append("面积 ≤ 0，不可用")
    if area_km2 > 500000:  # 超大区域（约 > 50 万 km²）提示
        warnings.append("区域过大（>50 万 km²），建议缩小范围")

    # 质心：去重闭合重复点后再求均值
    ring_pts = geometry["coordinates"][0]
    if ring_pts and ring_pts[0] == ring_pts[-1]:
        ring_pts = ring_pts[:-1]
    lons = [float(p[0]) for p in ring_pts]
    lats = [float(p[1]) for p in ring_pts]
    centroid = (sum(lons) / len(lons), sum(lats) / len(lats))

    valid = bbox_ok and area_km2 > 0 and len(unique_pts) >= 3 and not non_finite
    return AOIContext(
        aoi_id=aoi_id or uuid.uuid4().hex, source=source, geometry=geometry,
        bbox=bbox, centroid=centroid, area_km2=area_km2,
        valid=valid, warnings=warnings, created_at=_now_str(), label=label,
    )


def aoi_from_bbox(west: float, south: float, east: float, north: float, *,
                  source: str = "map_rectangle", label: Optional[str] = None) -> AOIContext:
    ring = [
        [west, south], [east, south], [east, north], [west, north], [west, south],
    ]
    return validate_aoi(
        {"type": "Polygon", "coordinates": [ring]},
        source=source, label=label,
        cross_antimeridian_hint=float(west) > float(east),
    )


def aoi_from_click(lon: float, lat: float, *, delta: float = 0.002,
                   label: Optional[str] = None) -> AOIContext:
    return aoi_from_bbox(lon - delta, lat - delta, lon + delta, lat + delta,
                         source="map_click", label=label)


def aoi_from_current_view(west: float, south: float, east: float, north: float, *,
                          label: Optional[str] = None) -> AOIContext:
    return aoi_from_bbox(west, south, east, north, source="current_view", label=label)


@dataclass
class AOIContext:
    """AOI 空间上下文（设计文档 §6.1）。"""

    aoi_id: str
    source: str
    geometry: Dict[str, Any]
    bbox: Tuple[float, float, float, float]
    centroid: Tuple[float, float]
    area_km2: float
    crs: str = "EPSG:4326"
    valid: bool = True
    warnings: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_str)
    label: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AOIContext":
        data = dict(data)
        data["bbox"] = tuple(data.get("bbox", (0.0, 0.0, 0.0, 0.0)))
        data["centroid"] = tuple(data.get("centroid", (0.0, 0.0)))
        return cls(**data)


def compact_summary(aoi: AOIContext) -> str:
    """紧凑注入文本（无完整 GeoJSON、无敏感值）。"""
    parts = [
        f"id={aoi.aoi_id}",
        f"source={aoi.source}",
        f"bbox=({aoi.bbox[0]},{aoi.bbox[1]},{aoi.bbox[2]},{aoi.bbox[3]})",
        f"centroid=({aoi.centroid[0]:.4f},{aoi.centroid[1]:.4f})",
        f"area_km2={aoi.area_km2:.1f}",
    ]
    if aoi.label:
        parts.append(f"label={aoi.label}")
    if not aoi.valid:
        parts.append("invalid")
        if aoi.warnings:
            parts.append("reasons=" + ";".join(aoi.warnings[:3]))
    return "[当前AOI] " + " ".join(parts)
