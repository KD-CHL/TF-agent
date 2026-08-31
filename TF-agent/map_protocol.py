# -*- coding: utf-8 -*-
"""地图命令协议（CSTF_MAP_V1）——纯 Python 可测部分。

职责（A/D 阶段）：
- 消息信封构造/解析：CSTF_MAP_READY / CSTF_FLY / CSTF_FLY_ACK / CSTF_LAYER_ADD /
  CSTF_LAYER_REMOVE / CSTF_LAYER_ACK / CSTF_AOI_SELECTED / CSTF_AOI_CLEARED / CSTF_MAP_ERROR
- command_id 幂等窗口（有界去重）
- 相机预设：地名与坐标分离；overview/region/point 高度
- targetOrigin 收紧：本机默认精确源 127.0.0.1，远程演示可放宽
- READY 握手等待策略
- iframe 缓存签名（不含相机字段）与重建决策
"""
from __future__ import annotations

import hashlib
import math
import uuid
from typing import Any, Dict, List, Optional, Tuple

MAP_PROTOCOL_VERSION = 1

# ---- 消息类型 ----
MSG_READY = "CSTF_MAP_READY"
MSG_FLY = "CSTF_FLY"
MSG_FLY_ACK = "CSTF_FLY_ACK"
MSG_LAYER_ADD = "CSTF_LAYER_ADD"
MSG_LAYER_REMOVE = "CSTF_LAYER_REMOVE"
MSG_LAYER_ACK = "CSTF_LAYER_ACK"
MSG_AOI_SELECTED = "CSTF_AOI_SELECTED"
MSG_AOI_CLEARED = "CSTF_AOI_CLEARED"
MSG_MAP_ERROR = "CSTF_MAP_ERROR"

KNOWN_MSG_TYPES = frozenset(
    {
        MSG_READY,
        MSG_FLY,
        MSG_FLY_ACK,
        MSG_LAYER_ADD,
        MSG_LAYER_REMOVE,
        MSG_LAYER_ACK,
        MSG_AOI_SELECTED,
        MSG_AOI_CLEARED,
        MSG_MAP_ERROR,
    }
)

# ---- 相机预设（地名与坐标分离存储，勿把地名硬编码进坐标解析） ----
CAMERA_PRESETS: Dict[str, Dict[str, Any]] = {
    "杭州湾": {"lat": 30.5, "lon": 120.8, "preset": "region", "label": "杭州湾"},
    "乐清湾": {"lat": 28.0, "lon": 121.2, "preset": "region", "label": "乐清湾"},
    "中国": {"lat": 36.0, "lon": 104.0, "preset": "overview", "label": "中国"},
}

# preset → lookAt 距离（米），与 globe_engine.DEFAULT_CAMERA 一致
PRESET_RANGES: Dict[str, float] = {
    "overview": 4_800_000.0,
    "region": 280_000.0,
    "point": 90_000.0,
}

_LAT_RANGE = (-90.0, 90.0)
_LON_RANGE = (-180.0, 180.0)


def new_command_id() -> str:
    return uuid.uuid4().hex


def zoom_to_height_m(zoom: int, lat: float = 30.0) -> float:
    """Web 缩放级别 → Cesium lookAt 距离（米），与 globe_engine.zoom_to_height_m 一致。"""
    z = max(1, min(18, int(zoom)))
    if z <= 4:
        return PRESET_RANGES["overview"]
    if z <= 7:
        return 1_000_000.0
    if z <= 10:
        return PRESET_RANGES["region"]
    if z <= 12:
        return PRESET_RANGES["point"]
    return 35_000.0


def resolve_preset(name: Optional[str]) -> Optional[Dict[str, Any]]:
    """地名 → 预设（坐标 + preset + label）；未知返回 None。"""
    if not name:
        return None
    key = str(name).strip()
    return CAMERA_PRESETS.get(key)


def validate_coords(lat: Any, lon: Any) -> Tuple[bool, List[str]]:
    """坐标校验：None / 非数值 / NaN / Inf / 越界 → 阻断。"""
    errors: List[str] = []
    for label, val, (lo, hi) in (("lat", lat, _LAT_RANGE), ("lon", lon, _LON_RANGE)):
        if val is None:
            errors.append(f"{label} 缺失")
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            errors.append(f"{label} 非数值: {val!r}")
            continue
        if not math.isfinite(f):
            errors.append(f"{label} 非有限数值（收到 {val!r}）")
            continue
        if f < lo or f > hi:
            errors.append(f"{label} 越界: {f}（允许 {lo}~{hi}）")
    return (not errors), errors


def bounds_to_center(bounds: Any) -> Tuple[Tuple[float, float], float]:
    """规范化经纬度矩形，并返回其 (lat, lon) 中心和合适的相机高度。

    矩形必须是未跨越反子午线的 ``west < east`` / ``south < north`` 盒子；
    无效矩形由调用方降级为其已有的点位相机。
    """
    if not isinstance(bounds, dict):
        raise ValueError("bounds 必须为对象")

    try:
        west = float(bounds["west"])
        south = float(bounds["south"])
        east = float(bounds["east"])
        north = float(bounds["north"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("bounds 必须包含数值 west/south/east/north") from exc

    valid_sw, sw_errors = validate_coords(south, west)
    valid_ne, ne_errors = validate_coords(north, east)
    if not valid_sw or not valid_ne:
        raise ValueError("; ".join(sw_errors + ne_errors))
    if west >= east or south >= north:
        raise ValueError("bounds 必须满足 west < east 且 south < north")

    center = ((south + north) / 2.0, (west + east) / 2.0)
    span = max(east - west, north - south, 0.02)
    height = max(60_000.0, min(span * 111_000.0 * 2.8, 6_000_000.0))
    return center, height


def make_message(
    msg_type: str,
    command_id: Optional[str] = None,
    **payload: Any,
) -> Dict[str, Any]:
    """构造 CSTF_MAP_V1 信封：{type, version, command_id, ts, ...payload}。"""
    msg: Dict[str, Any] = {
        "type": msg_type,
        "version": MAP_PROTOCOL_VERSION,
        "command_id": command_id or new_command_id(),
        "ts": _now_epoch(),
    }
    msg.update(payload)
    return msg


def _now_epoch() -> float:
    import time

    return time.time()


def make_fly_message(
    lon: Any,
    lat: Any,
    *,
    zoom: Optional[int] = None,
    height: Optional[float] = None,
    bounds: Optional[Dict[str, Any]] = None,
    pitch: Optional[float] = None,
    heading: Optional[float] = None,
    duration: Optional[float] = None,
    preset: Optional[str] = None,
    label: Optional[str] = None,
    source: Optional[str] = None,
    command_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """构造 CSTF_FLY 消息；非法坐标 → (None, errors)，不产生消息。"""
    ok, errors = validate_coords(lat, lon)
    if not ok:
        return None, errors

    f_lat, f_lon = float(lat), float(lon)

    normalized_bounds: Optional[Dict[str, float]] = None
    bounds_height: Optional[float] = None
    if bounds is not None:
        try:
            _, bounds_height = bounds_to_center(bounds)
            normalized_bounds = {
                key: float(bounds[key])
                for key in ("west", "south", "east", "north")
            }
        except (KeyError, TypeError, ValueError):
            # 边界是可选增强；无效时继续使用已校验的点位相机。
            pass

    if height is None:
        if normalized_bounds is not None:
            height = bounds_height
        elif zoom is not None:
            height = zoom_to_height_m(int(zoom), f_lat)
        elif preset in PRESET_RANGES:
            height = PRESET_RANGES[preset]
        else:
            height = PRESET_RANGES["point"]

    if label is None:
        label = f"({f_lat:.2f}°N, {f_lon:.2f}°E)"

    msg = make_message(
        MSG_FLY,
        command_id=command_id,
        lon=f_lon,
        lat=f_lat,
        height=float(height),
    )
    if normalized_bounds is not None:
        msg["bounds"] = normalized_bounds
    if pitch is not None:
        msg["pitch"] = float(pitch)
    if heading is not None:
        msg["heading"] = float(heading)
    if duration is not None:
        msg["duration"] = float(duration)
    if preset:
        msg["preset"] = preset
    if label:
        msg["label"] = label
    if source:
        msg["source"] = source
    return msg, []


def parse_map_message(data: Any) -> Tuple[bool, List[str]]:
    """校验信封：type 已知、version 兼容（缺省视为 1）；command_id 缺失自动补。"""
    errors: List[str] = []
    if not isinstance(data, dict):
        return False, ["消息必须为 JSON 对象"]
    mtype = data.get("type")
    if mtype not in KNOWN_MSG_TYPES:
        errors.append(f"未知消息 type: {mtype!r}")
    ver = data.get("version")
    if ver is not None and int(ver) != MAP_PROTOCOL_VERSION:
        errors.append(f"协议版本不兼容: {ver} != {MAP_PROTOCOL_VERSION}")
    if not data.get("command_id"):
        data["command_id"] = new_command_id()
    return (not errors), errors


class CommandIdWindow:
    """command_id 幂等窗口：重复命令判定 + 容量淘汰（有界去重）。

    语义：
    - 窗口内命中 → True（不修改窗口）。
    - 全新 id → 写入窗口（满则淘汰最旧）并返回 False。
    - 已淘汰的旧 id → 返回 False 且**不重新写入**（避免污染窗口挤掉其他项）。
    """

    def __init__(self, capacity: int = 200):
        self.capacity = max(1, int(capacity))
        self._seen: List[str] = []
        self._set = set()
        self._ever = set()

    def is_duplicate(self, command_id: str) -> bool:
        cid = str(command_id)
        if cid in self._set:
            return True
        if cid in self._ever:
            # 已淘汰的旧 id：不重复、不重新写入
            return False
        self._ever.add(cid)
        if len(self._seen) >= self.capacity:
            evicted = self._seen.pop(0)
            self._set.discard(evicted)
        self._set.add(cid)
        self._seen.append(cid)
        return False


def target_origin(
    port: int,
    *,
    force_local: bool = True,
    public_base: Optional[str] = None,
    allow_wildcard: bool = False,
) -> str:
    """postMessage targetOrigin：默认精确源 http://127.0.0.1:{port}；远程演示可放宽。"""
    if force_local:
        return f"http://127.0.0.1:{int(port)}"
    if allow_wildcard:
        return "*"
    if public_base:
        return str(public_base).rstrip("/")
    return f"http://127.0.0.1:{int(port)}"


def ready_window_expired(ready_ts: Optional[float], now: float, timeout_s: float) -> bool:
    """READY 握手窗口：未收到 READY（None）视为仍在等待；超过 timeout 视为过期。"""
    if ready_ts is None:
        return False
    return (float(now) - float(ready_ts)) > float(timeout_s)


def ready_policy(
    ready_ts: Optional[float],
    now: float,
    timeout_s: float,
    wait_started_at: Optional[float] = None,
) -> str:
    """READY 等待策略：'send' 直接发 / 'wait' 继续等 / 'send_warn' 超时仍发（带警告）。"""
    if ready_ts is not None:
        return "send"
    start = wait_started_at if wait_started_at is not None else now
    if (float(now) - float(start)) > float(timeout_s):
        return "send_warn"
    return "wait"


# ---- iframe 缓存签名与重建决策（与 app.py 现有 _cache_sig 同构） ----

_CAMERA_SIG_TAG = "v3"


def globe_cache_signature(
    asset_path: str,
    mtime: float,
    rev: int,
    opacity_pct: float,
    show_e1: bool,
    e1_tag: str,
    force_local: bool,
    camera: str = _CAMERA_SIG_TAG,
) -> str:
    """缓存签名：**不含相机字段**（map_center/map_zoom 不入签名 → 纯跳转复用 iframe）。"""
    raw = (
        f"{asset_path}|{mtime:.4f}|{int(rev)}|{float(opacity_pct)}|"
        f"{int(bool(show_e1))}|{e1_tag}|local={int(bool(force_local))}|cam={camera}"
    )
    return hashlib.md5(raw.encode("utf-8", errors="replace")).hexdigest()


def globe_cache_hit(cached_sig: str, current_sig: str) -> bool:
    return bool(cached_sig) and cached_sig == current_sig


def should_rebuild_iframe(
    has_active_iframe: bool,
    layer_protocol_ok: bool,
    signature_changed: bool,
) -> bool:
    """重建决策：无活跃 iframe 或图层协议不可用 → 重建；否则走 CSTF_LAYER_* 协议。"""
    if not has_active_iframe:
        return True
    if not signature_changed:
        return False
    if not layer_protocol_ok:
        return True
    return False


def same_globe_origin(url: str, base: str) -> bool:
    """缓存 iframe URL 是否仍指向当前地球服务根。"""
    if not url or not base:
        return False
    b = str(base).rstrip("/")
    u = str(url)
    return u.startswith(b + "/") or u.rstrip("/") == b


def globe_service_base_for_test(port: int, *, force_local: bool = True) -> str:
    """测试辅助：本机 globe 服务根（生产路径由 globe_server.globe_service_base 决定）。"""
    return target_origin(port, force_local=force_local)
