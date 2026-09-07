# -*- coding: utf-8 -*-
"""Deterministic, local-first resolution of user-facing map place names.

This module deliberately has no network or geocoding dependency.  A place
name is resolved only against the local camera preset table; callers that
already have explicit coordinates can use those coordinates without asking
this resolver to infer a place.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from map_protocol import CAMERA_PRESETS, validate_coords


@dataclass(frozen=True)
class LocationResolution:
    """Safe result of a local place-name lookup.

    Coordinates are populated only for a successful resolution.  This keeps
    unresolved and ambiguous names explicit and prevents callers from
    accidentally treating a model guess as a validated location.
    """

    ok: bool
    lat: Optional[float] = None
    lon: Optional[float] = None
    label: Optional[str] = None
    resolver_source: Optional[str] = None
    reason: Optional[str] = None
    candidates: tuple[str, ...] = ()

    @property
    def source(self) -> Optional[str]:
        """Backward-compatible short spelling used by the resolver API."""

        return self.resolver_source

    def as_dict(self) -> dict[str, Any]:
        """Return only the fields that are meaningful for this result."""

        result: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            result.update(
                {
                    "lat": self.lat,
                    "lon": self.lon,
                    "label": self.label,
                    "resolver_source": self.resolver_source,
                }
            )
        else:
            result["reason"] = self.reason or "error"
            if self.candidates:
                result["candidates"] = list(self.candidates)
        return result


def _clean_name(value: Any) -> str:
    return str(value or "").strip()


def _preset_coordinates(name: str, preset: Mapping[str, Any]) -> LocationResolution:
    lat = preset.get("lat")
    lon = preset.get("lon")
    valid, _ = validate_coords(lat, lon)
    if not valid:
        return LocationResolution(ok=False, reason="invalid_preset")
    return LocationResolution(
        ok=True,
        lat=float(lat),
        lon=float(lon),
        label=str(preset.get("label") or name),
        resolver_source="local_preset",
    )


def _coordinate_resolution(lat: Any, lon: Any) -> LocationResolution:
    valid, _ = validate_coords(lat, lon)
    if not valid:
        return LocationResolution(ok=False, reason="invalid_coordinates")
    return LocationResolution(
        ok=True,
        lat=float(lat),
        lon=float(lon),
        label=f"({float(lat):.4f}°, {float(lon):.4f}°)",
        resolver_source="provided_coordinates",
    )


def resolve_location(
    location_name: Any,
    lat: Any = None,
    lon: Any = None,
    *,
    presets: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> LocationResolution:
    """Resolve a location without network access.

    Exact local preset names take precedence.  If no exact name exists, a
    unique containment match is accepted; multiple matches return an explicit
    ``ambiguous`` result with candidates.  Unknown non-empty names remain
    ``unresolved`` even when model coordinates are present: callers may still
    use those coordinates through the canonical direct-coordinate path, but
    they are not silently attributed to the unknown place name.

    An empty name with valid coordinates is treated as an explicit coordinate
    command, not as a place-name lookup.  No provider is contacted.
    """

    name = _clean_name(location_name)
    table = presets if presets is not None else CAMERA_PRESETS
    if not isinstance(table, Mapping):
        return LocationResolution(ok=False, reason="invalid_preset_table")

    if name in table:
        preset = table[name]
        if isinstance(preset, Mapping):
            return _preset_coordinates(name, preset)
        return LocationResolution(ok=False, reason="invalid_preset")

    if not name:
        if lat is None and lon is None:
            return LocationResolution(ok=False, reason="missing_location")
        if lat is None or lon is None:
            return LocationResolution(ok=False, reason="incomplete_coordinates")
        return _coordinate_resolution(lat, lon)

    # Containment is intentionally narrow and deterministic: no fuzzy scoring,
    # transliteration, or external geocoding is involved.
    candidates = tuple(key for key in table if name in str(key))
    if len(candidates) > 1:
        return LocationResolution(ok=False, reason="ambiguous", candidates=candidates)
    if len(candidates) == 1:
        preset = table[candidates[0]]
        if isinstance(preset, Mapping):
            return _preset_coordinates(candidates[0], preset)
        return LocationResolution(ok=False, reason="invalid_preset")
    return LocationResolution(ok=False, reason="unresolved")


__all__ = ["LocationResolution", "resolve_location"]
