# -*- coding: utf-8 -*-
"""通用地名解析：纯定位语句提取 + 可替换的 Nominatim 地理编码。"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple


_DIRECT_MAP_REQUEST_RE = re.compile(
    r"^\s*(?:(?:请|麻烦|劳驾)\s*)?(?:(?:帮我|给我)\s*)?(?:把|将)?\s*"
    r"(?:地图(?:视角)?|视角|镜头|画面)?\s*"
    r"(?:聚焦|定位|跳转|飞到|移动|挪到|移到|切换到|看看|查看)\s*"
    r"(?:到|至|在)?\s*(?P<place>.+?)\s*$",
    re.IGNORECASE,
)
_NON_LOCATION_INTENT_RE = re.compile(
    r"(?:并且?|然后|同时|以及|运行|执行|下载|分析|搜索|查询|检索|介绍|"
    r"告诉我|天气|政策|报告|影像|提取|推理|变化检测|为什么|怎么|哪里|"
    r"什么|如何|是否|能否|可以吗)"
)
_TRAILING_TONE_RE = re.compile(r"(?:一下子|一下|吧|呀|啊|哈|呢)+$")
_TRAILING_MAP_NOUN_RE = re.compile(r"(?:的)?(?:区域|位置|附近|一带|地图)$")
_TRIM_CHARS = " \t\r\n,，。.!！?？:：;；、（）()[]{}'\""

_DEFAULT_GEOCODER_URL = "https://nominatim.openstreetmap.org/search"
_DEFAULT_USER_AGENT = "TF-agent/1.0 (+https://github.com/gwxislander/TF-agent)"
_ATTRIBUTION = "© OpenStreetMap contributors / Nominatim"
_MIN_REQUEST_INTERVAL_S = 1.05

_CACHE: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()
_LAST_REQUEST_MONOTONIC = 0.0


def extract_direct_map_place(text: Any) -> Optional[str]:
    """从纯地图操作中提取完整地名；复合任务或知识问答返回 ``None``。"""
    value = str(text or "").strip()
    if not value:
        return None
    match = _DIRECT_MAP_REQUEST_RE.fullmatch(value)
    if not match:
        return None
    place = match.group("place").strip(_TRIM_CHARS)
    place = _TRAILING_TONE_RE.sub("", place).strip(_TRIM_CHARS)
    place = _TRAILING_MAP_NOUN_RE.sub("", place).strip(_TRIM_CHARS)
    if not place or len(place) > 120 or _NON_LOCATION_INTENT_RE.search(place):
        return None
    return place


def _zoom_for_result(item: Dict[str, Any]) -> int:
    kind = str(item.get("addresstype") or item.get("type") or "").lower()
    if kind in {"country", "continent"}:
        return 4
    if kind in {"state", "province", "region"}:
        return 7
    if kind in {"city", "municipality", "county"}:
        return 10
    if kind in {"town", "borough", "district", "suburb"}:
        return 12
    if kind in {"village", "neighbourhood", "quarter", "hamlet"}:
        return 13
    return 15


def _parse_result(place: str, payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None
    item = payload[0]
    try:
        lat = float(item.get("lat"))
        lon = float(item.get("lon"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return {
        "lat": lat,
        "lon": lon,
        "zoom": _zoom_for_result(item),
        "label": place,
        "display_name": str(item.get("display_name") or place),
        "attribution": _ATTRIBUTION,
    }


def _fetch_nominatim(place: str, timeout_s: float) -> Any:
    base_url = os.environ.get("CSTF_GEOCODER_URL", _DEFAULT_GEOCODER_URL).strip()
    params = urllib.parse.urlencode(
        {
            "q": place,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
            "accept-language": "zh-CN,zh,en",
        }
    )
    separator = "&" if "?" in base_url else "?"
    request = urllib.request.Request(
        base_url + separator + params,
        headers={
            "User-Agent": os.environ.get(
                "CSTF_GEOCODER_USER_AGENT", _DEFAULT_USER_AGENT
            ).strip()
            or _DEFAULT_USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        raw = response.read(256_000)
    return json.loads(raw.decode("utf-8"))


def geocode_place(place: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """把任意地名解析为 WGS84 坐标，并对重复查询做进程内缓存。"""
    global _LAST_REQUEST_MONOTONIC

    query = re.sub(r"\s+", " ", str(place or "")).strip()
    if not query:
        return None, "没有可解析的地名。"
    if str(os.environ.get("CSTF_GEOCODER_ENABLED", "1")).strip().lower() in {
        "0",
        "false",
        "off",
        "no",
    }:
        return None, "通用地名解析已关闭（CSTF_GEOCODER_ENABLED=0）。"

    cache_key = query.casefold()
    with _LOCK:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return dict(cached), None

        elapsed = time.monotonic() - _LAST_REQUEST_MONOTONIC
        if elapsed < _MIN_REQUEST_INTERVAL_S:
            time.sleep(_MIN_REQUEST_INTERVAL_S - elapsed)
        try:
            timeout_s = max(
                1.0,
                min(15.0, float(os.environ.get("CSTF_GEOCODER_TIMEOUT_SEC", "6"))),
            )
        except (TypeError, ValueError):
            timeout_s = 6.0
        try:
            payload = _fetch_nominatim(query, timeout_s)
        except Exception:
            _LAST_REQUEST_MONOTONIC = time.monotonic()
            return None, f"暂时无法联网解析地名“{query}”，请检查网络后重试。"
        _LAST_REQUEST_MONOTONIC = time.monotonic()

        result = _parse_result(query, payload)
        if result is None:
            return None, f"未找到地名“{query}”，请尝试更完整的名称。"
        _CACHE[cache_key] = dict(result)
        return dict(result), None


def _reset_cache_for_tests() -> None:
    global _LAST_REQUEST_MONOTONIC
    with _LOCK:
        _CACHE.clear()
        _LAST_REQUEST_MONOTONIC = 0.0


__all__ = ["extract_direct_map_place", "geocode_place"]
