# -*- coding: utf-8 -*-
"""Rectangle camera protocol regression tests."""
from __future__ import annotations

import os
import sys

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)


def test_bounds_to_camera_height_is_finite():
    from map_protocol import bounds_to_center

    center, height = bounds_to_center({
        "west": 120.5, "south": 38.0,
        "east": 122.7, "north": 39.8,
    })
    assert center == (38.9, 121.6)
    assert 60_000 <= height <= 6_000_000


def test_fly_message_serializes_valid_bounds_and_uses_rectangle_height():
    from map_protocol import make_fly_message

    message, errors = make_fly_message(
        121.6,
        38.9,
        bounds={"west": "120.5", "south": "38.0", "east": "122.7", "north": "39.8"},
    )

    assert errors == []
    assert message["bounds"] == {"west": 120.5, "south": 38.0, "east": 122.7, "north": 39.8}
    assert message["height"] > 280_000


def test_fly_message_falls_back_to_point_when_bounds_are_invalid():
    from map_protocol import make_fly_message

    message, errors = make_fly_message(
        121.6,
        38.9,
        bounds={"west": 122.7, "south": 38.0, "east": 120.5, "north": 39.8},
    )

    assert errors == []
    assert "bounds" not in message
    assert message["lat"] == 38.9
    assert message["lon"] == 121.6
