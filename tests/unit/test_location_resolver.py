# -*- coding: utf-8 -*-
"""Deterministic, local-first place-name resolution tests."""
from __future__ import annotations

import os
import sys

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)


def test_local_preset_is_deterministic():
    from location_resolver import resolve_location

    result = resolve_location("杭州湾")
    assert result.ok is True
    assert (result.lat, result.lon) == (30.5, 120.8)
    assert result.label == "杭州湾"
    assert result.source == "local_preset"
    assert result.resolver_source == "local_preset"


def test_unknown_name_requires_explicit_coordinates_or_provider():
    from location_resolver import resolve_location

    result = resolve_location("不存在的区域")
    assert result.ok is False
    assert result.reason == "unresolved"
    assert result.lat is None
    assert result.lon is None


def test_ambiguous_local_match_is_explicit_and_has_candidates():
    from location_resolver import resolve_location

    result = resolve_location(
        "湾",
        presets={
            "杭州湾": {"lat": 30.5, "lon": 120.8, "label": "杭州湾"},
            "乐清湾": {"lat": 28.0, "lon": 121.2, "label": "乐清湾"},
        },
    )
    assert result.ok is False
    assert result.reason == "ambiguous"
    assert result.candidates == ("杭州湾", "乐清湾")


def test_explicit_coordinates_can_be_used_without_name_resolution():
    from location_resolver import resolve_location

    result = resolve_location("", lat=31.2, lon=121.5)
    assert result.ok is True
    assert (result.lat, result.lon) == (31.2, 121.5)
    assert result.source == "provided_coordinates"


def test_unknown_name_with_coordinates_stays_unresolved():
    from location_resolver import resolve_location

    result = resolve_location("不存在的区域", lat=31.2, lon=121.5)
    assert result.ok is False
    assert result.reason == "unresolved"
    assert result.lat is None
    assert result.lon is None


def test_invalid_coordinates_are_an_explicit_error():
    from location_resolver import resolve_location

    result = resolve_location("", lat=91, lon=121.5)
    assert result.ok is False
    assert result.reason == "invalid_coordinates"


def test_command_bridge_resolves_local_name_without_external_geocoding():
    from agent_command_bridge import parse_system_command

    raw = (
        '[SYSTEM_COMMAND_JSON]{"map":{"location_name":"杭州湾","zoom":9}}'
        "[/SYSTEM_COMMAND_JSON]"
    )
    command = parse_system_command(raw)
    assert command["map"] == {
        "lat": 30.5,
        "lon": 120.8,
        "zoom": 9,
        "label": "杭州湾",
    }


def test_command_bridge_keeps_explicit_coordinates_for_unknown_name():
    from agent_command_bridge import parse_system_command

    raw = (
        '[SYSTEM_COMMAND_JSON]{"map":{"location_name":"未知地点",'
        '"lat":31.2,"lon":121.5,"zoom":10}}'
        "[/SYSTEM_COMMAND_JSON]"
    )
    command = parse_system_command(raw)
    assert command["map"]["lat"] == 31.2
    assert command["map"]["lon"] == 121.5
    assert "location_name" not in command["map"]


def test_unknown_name_without_coordinates_is_rejected_explicitly():
    from agent_command_bridge import apply_system_command

    state = {}
    result = apply_system_command(state, {"map": {"location_name": "未知地点", "zoom": 9}})
    assert result.applied is False
    assert any("unresolved" in error for error in result.errors)
    assert "map_center" not in state
