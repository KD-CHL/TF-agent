# -*- coding: utf-8 -*-
"""跨平台地图定位命令验收矩阵（Task 8）。

The four strings below are the complete matrix. Each input is sent through
the same parser/bridge used by the application and then through the Python
side of the CSTF READY -> FLY -> ACK protocol. The script is deliberately
offline: it does not start Streamlit, launch a browser, or contact a provider.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TF_AGENT = ROOT / "TF-agent"
if str(TF_AGENT) not in sys.path:
    sys.path.insert(0, str(TF_AGENT))

from agent_command_bridge import apply_system_command, parse_system_command  # noqa: E402
from map_protocol import (  # noqa: E402
    MSG_FLY_ACK,
    MSG_READY,
    bounds_to_center,
    make_fly_message,
    make_message,
    parse_map_message,
)

CASES = [
    '{"map":{"lat":30.5,"lon":120.8,"zoom":9}}',
    '{"map":{"center":[38.9126,121.6174],"zoom":8}}',
    '{"map":{"center":[38.9126,121.6174],"zoom":8,"bounds":[[38.0,120.5],[39.8,122.7]]}}',
    'COMMAND_UPDATE_MAP|30.5|120.8|9',
]

CASE_NAMES = (
    "json canonical lat/lon",
    "json legacy center",
    "json center+bounds compatibility",
    "pipe legacy lat/lon/zoom",
)
CANONICAL_MAP_FIELDS = frozenset(
    {
        "lat", "lon", "zoom", "bounds", "preset", "label",
        "height", "duration", "pitch", "heading",
    }
)
EXPECTED_CENTERS = (
    (30.5, 120.8),
    (38.9126, 121.6174),
    (38.9126, 121.6174),
    (30.5, 120.8),
)


def _json_reply(raw: str) -> str:
    """Wrap one JSON case exactly as an Agent response would be wrapped."""

    return f"[SYSTEM_COMMAND_JSON]{raw}[/SYSTEM_COMMAND_JSON]"


def _parse_case(raw: str) -> dict[str, Any]:
    if raw.lstrip().startswith("{"):
        command = parse_system_command(_json_reply(raw))
    else:
        command = parse_system_command(raw)
    assert isinstance(command, dict), "command parser returned no command"
    map_command = command.get("map")
    assert isinstance(map_command, dict), "parsed command has no map payload"
    assert set(map_command).issubset(CANONICAL_MAP_FIELDS), "non-canonical map field leaked"
    return command


def _exercise_browser_protocol(index: int, map_command: dict[str, Any]) -> None:
    """Exercise the browser-facing READY -> FLY -> ACK envelope boundary."""

    ready = make_message(
        MSG_READY,
        command_id=f"acceptance-ready-{index}",
        viewer_ready=True,
        imagery=True,
        camera=True,
    )
    ready_ok, ready_errors = parse_map_message(ready)
    assert ready_ok and not ready_errors, "READY envelope rejected"

    command_id = f"acceptance-fly-{index}"
    fly, fly_errors = make_fly_message(
        map_command["lon"],
        map_command["lat"],
        zoom=map_command.get("zoom"),
        bounds=map_command.get("bounds"),
        command_id=command_id,
        source="acceptance_matrix",
    )
    assert fly is not None and not fly_errors, "FLY envelope could not be built"
    fly_ok, fly_validation_errors = parse_map_message(fly)
    assert fly_ok and not fly_validation_errors, "FLY envelope rejected"
    assert (fly["lat"], fly["lon"]) == (map_command["lat"], map_command["lon"])
    if map_command.get("bounds") is not None:
        assert fly.get("bounds") == map_command["bounds"], "FLY bounds changed after normalization"

    ack = make_message(MSG_FLY_ACK, command_id=command_id, ok=True)
    ack_ok, ack_errors = parse_map_message(ack)
    assert ack_ok and not ack_errors, "ACK envelope rejected"
    assert ack["ok"] is True and ack["command_id"] == command_id


def _exercise_case(index: int, raw: str) -> dict[str, Any]:
    command = _parse_case(raw)
    state: dict[str, Any] = {}
    result = apply_system_command(state, command)
    assert result.applied and result.map_updated, "map command was not applied"
    assert not result.errors, "valid matrix case produced bridge errors"

    map_command = command["map"]
    center = (float(map_command["lat"]), float(map_command["lon"]))
    assert center == EXPECTED_CENTERS[index]
    assert tuple(state["map_center"]) == center
    assert state["map_zoom"] == int(map_command["zoom"])
    pending = state.get("_pending_camera_fly")
    assert isinstance(pending, dict), "bridge did not create pending camera flight"
    assert (pending["lat"], pending["lon"]) == center

    bounds = map_command.get("bounds")
    normalized_bounds: dict[str, float] | None = None
    if index == 2:
        assert isinstance(bounds, dict), "compatibility bounds were not normalized"
        assert set(bounds) == {"west", "south", "east", "north"}
        assert bounds["west"] < bounds["east"]
        assert bounds["south"] < bounds["north"]
        rectangle_center, _ = bounds_to_center(bounds)
        assert rectangle_center == (38.9, 121.6)
        normalized_bounds = dict(bounds)
        assert pending["bounds"] == normalized_bounds
    else:
        assert bounds is None, "unexpected bounds in a matrix case"

    _exercise_browser_protocol(index, map_command)
    return {
        "name": CASE_NAMES[index],
        "index": index + 1,
        "canonical_center": list(center),
        "zoom": int(map_command["zoom"]),
        "bounds": normalized_bounds,
        "protocol": ["CSTF_MAP_READY", "CSTF_FLY", "CSTF_FLY_ACK"],
    }


def run_matrix() -> dict[str, Any]:
    """Run exactly the four cases and return a machine-readable summary."""

    assert len(CASES) == 4, "matrix must contain exactly four cases"
    cases = [_exercise_case(index, raw) for index, raw in enumerate(CASES)]
    centers = [tuple(case["canonical_center"]) for case in cases]
    assert centers[0] == centers[3], "canonical and pipe centers differ"
    assert centers[1] == centers[2], "center and center+bounds centers differ"
    return {
        "status": "PASS",
        "case_count": len(cases),
        "cases": cases,
        "equivalent_center_groups": [[1, 4], [2, 3]],
    }


def main() -> int:
    try:
        print(json.dumps(run_matrix(), ensure_ascii=False, sort_keys=True))
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "FAIL", "error_type": type(exc).__name__, "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
