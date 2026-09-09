# -*- coding: utf-8 -*-
"""地图命令适配器的严格合同测试。"""
from __future__ import annotations

import os
import sys

_TF_AGENT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent"))
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from agent_command_schema import MapCommand  # noqa: E402
from map_command_adapter import normalize_map_payload, parse_legacy_map_text  # noqa: E402


def test_center_and_bounds_are_normalized():
    result = normalize_map_payload({"center": [38.9126, 121.6174], "zoom": 8, "bounds": [[38.0, 120.5], [39.8, 122.7]]})
    assert result.lat == 38.9126
    assert result.lon == 121.6174
    assert result.bounds == {"west": 120.5, "south": 38.0, "east": 122.7, "north": 39.8}


def test_canonical_payload_is_unchanged():
    result = normalize_map_payload({"lat": 30.5, "lon": 120.8, "zoom": 9})
    assert (result.lat, result.lon, result.zoom) == (30.5, 120.8, 9)


def test_camera_options_are_canonical_map_fields():
    result = normalize_map_payload(
        {
            "lat": 30.5,
            "lon": 120.8,
            "height": 1000,
            "duration": 2.5,
            "pitch": -45,
            "heading": 90,
        }
    )
    assert result.to_command_dict() == {
        "lat": 30.5,
        "lon": 120.8,
        "zoom": 8,
        "height": 1000.0,
        "duration": 2.5,
        "pitch": -45.0,
        "heading": 90.0,
    }


def test_invalid_bounds_does_not_discard_valid_center():
    result = normalize_map_payload({"lat": 30.5, "lon": 120.8, "zoom": 9, "bounds": [[95, 120], [96, 121]]})
    assert (result.lat, result.lon) == (30.5, 120.8)
    assert result.bounds is None
    assert result.warnings


@pytest.mark.parametrize(
    "bounds",
    [
        [[30.0, 120.0], [30.0, 121.0]],
        [[30.0, 120.0], [31.0, 120.0]],
    ],
)
def test_equal_bounds_are_invalid_without_discarding_center(bounds):
    result = normalize_map_payload(
        {"lat": 30.5, "lon": 120.8, "zoom": 9, "bounds": bounds}
    )
    assert (result.lat, result.lon) == (30.5, 120.8)
    assert result.bounds is None
    assert any("invalid bounds" in warning for warning in result.warnings)


def test_pipe_text_uses_lat_lon_zoom_order():
    result = parse_legacy_map_text("COMMAND_UPDATE_MAP|30.5|120.8|9")
    assert (result.lat, result.lon, result.zoom) == (30.5, 120.8, 9)
    assert result.source == "legacy_text"


def test_unknown_fields_are_warned_and_never_emitted():
    result = normalize_map_payload({"lat": 30.5, "lon": 120.8, "zoom": 9, "secret": "x"})
    assert "secret" not in result.to_command_dict()
    assert any("secret" in warning for warning in result.warnings)


def test_map_schema_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        MapCommand.model_validate({"lat": 30.5, "lon": 120.8, "secret": "x"})


def test_map_schema_rejects_degenerate_bounds():
    with pytest.raises(ValidationError):
        MapCommand.model_validate(
            {
                "lat": 30.5,
                "lon": 120.8,
                "bounds": {"west": 120.0, "south": 30.0, "east": 120.0, "north": 31.0},
            }
        )


def test_bounds_dict_and_center_object_aliases_are_supported():
    result = normalize_map_payload({"center": {"latitude": "30.5", "longitude": "120.8"}, "bounds": {"west": 120, "south": 30, "east": 121, "north": 31}})
    assert (result.lat, result.lon) == (30.5, 120.8)
    assert result.bounds == {"west": 120.0, "south": 30.0, "east": 121.0, "north": 31.0}


def test_invalid_center_is_rejected():
    with pytest.raises(ValueError):
        normalize_map_payload({"lat": 91, "lon": 120})


def test_incomplete_bounds_become_warning_without_losing_center():
    result = normalize_map_payload({"lat": 30.5, "lon": 120.8, "bounds": {"west": 120}})
    assert result.bounds is None
    assert result.warnings


def test_fractional_zoom_is_rejected_instead_of_truncated():
    with pytest.raises(ValueError, match="integer"):
        normalize_map_payload({"lat": 30.5, "lon": 120.8, "zoom": 8.5})


def test_nested_unknown_fields_are_warned_and_never_emitted():
    result = normalize_map_payload({
        "center": {"latitude": 30.5, "longitude": 120.8, "extra_center": True},
        "bounds": {"west": 120, "south": 30, "east": 121, "north": 31, "extra_bounds": True},
    })
    assert any("center.extra_center" in warning for warning in result.warnings)
    assert any("bounds.extra_bounds" in warning for warning in result.warnings)
    assert "extra_center" not in result.to_command_dict()
    assert "extra_bounds" not in result.to_command_dict().get("bounds", {})
