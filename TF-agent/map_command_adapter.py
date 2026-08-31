# -*- coding: utf-8 -*-
"""Normalize map commands from current and legacy clients.

This module intentionally has no Streamlit dependency.  It is the single
boundary for converting loose model/client output into a strict map command.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
import re
from typing import Any, Mapping

from agent_command_schema import MapBounds


_KNOWN_KEYS = {"lat", "lon", "zoom", "bounds", "preset", "label", "center"}
_PIPE_COMMAND = re.compile(
    r"COMMAND_UPDATE_MAP\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|\s]+)",
    re.IGNORECASE,
)


@dataclass
class NormalizedMapCommand:
    lat: float
    lon: float
    zoom: int = 8
    bounds: dict[str, float] | None = None
    preset: str | None = None
    label: str | None = None
    source: str = "payload"
    warnings: list[str] = field(default_factory=list)

    def to_command_dict(self) -> dict[str, Any]:
        """Return only keys accepted by the strict ``MapCommand`` schema."""
        result: dict[str, Any] = {"lat": self.lat, "lon": self.lon, "zoom": self.zoom}
        if self.bounds is not None:
            result["bounds"] = dict(self.bounds)
        if self.preset is not None:
            result["preset"] = self.preset
        if self.label is not None:
            result["label"] = self.label
        return result


def _number(value: Any, name: str, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be numeric") from None
    if not isfinite(number) or not low <= number <= high:
        raise ValueError(f"{name} is out of range")
    return number


def _center(payload: Mapping[str, Any]) -> tuple[float, float]:
    if "lat" in payload and "lon" in payload:
        return _number(payload["lat"], "lat", -90, 90), _number(payload["lon"], "lon", -180, 180)
    center = payload.get("center")
    if isinstance(center, (list, tuple)) and len(center) >= 2:
        return _number(center[0], "lat", -90, 90), _number(center[1], "lon", -180, 180)
    if isinstance(center, Mapping):
        lat = center.get("lat", center.get("latitude"))
        lon = center.get("lon", center.get("longitude"))
        return _number(lat, "lat", -90, 90), _number(lon, "lon", -180, 180)
    raise ValueError("map command requires lat/lon or center")


def _bounds(value: Any) -> dict[str, float]:
    if isinstance(value, Mapping):
        try:
            values = {key: _number(value[key], key, -180 if key in {"west", "east"} else -90, 180 if key in {"west", "east"} else 90) for key in ("west", "south", "east", "north")}
        except KeyError as exc:
            raise ValueError(f"bounds is missing {exc.args[0]}") from None
    elif isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(item, (list, tuple)) and len(item) >= 2 for item in value):
        values = {"west": _number(value[0][1], "west", -180, 180), "south": _number(value[0][0], "south", -90, 90), "east": _number(value[1][1], "east", -180, 180), "north": _number(value[1][0], "north", -90, 90)}
    else:
        raise ValueError("bounds must be [[south, west], [north, east]] or an object")
    if values["west"] > values["east"] or values["south"] > values["north"]:
        raise ValueError("bounds must not be inverted")
    return values


def normalize_map_payload(payload: dict[str, Any]) -> NormalizedMapCommand:
    if not isinstance(payload, dict):
        raise ValueError("map payload must be an object")
    lat, lon = _center(payload)
    warnings = [f"unknown map field: {key}" for key in payload if key not in _KNOWN_KEYS]
    zoom_value = payload.get("zoom", 8)
    try:
        zoom = int(zoom_value)
    except (TypeError, ValueError):
        raise ValueError("zoom must be an integer") from None
    if not 1 <= zoom <= 18:
        raise ValueError("zoom is out of range")
    parsed_bounds = None
    if "bounds" in payload:
        try:
            parsed_bounds = _bounds(payload["bounds"])
        except ValueError as exc:
            warnings.append(f"invalid bounds: {exc}")
    return NormalizedMapCommand(lat, lon, zoom, parsed_bounds, payload.get("preset"), payload.get("label"), "payload", warnings)


def parse_legacy_map_text(text: str) -> NormalizedMapCommand:
    match = _PIPE_COMMAND.search(str(text))
    if not match:
        raise ValueError("unsupported legacy map command")
    result = normalize_map_payload({"lat": match.group(1), "lon": match.group(2), "zoom": match.group(3)})
    result.source = "legacy_text"
    return result


__all__ = ["NormalizedMapCommand", "normalize_map_payload", "parse_legacy_map_text"]
