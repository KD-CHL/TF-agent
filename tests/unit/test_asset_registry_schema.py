from __future__ import annotations

import os
import sys

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

from asset_registry_schema import (  # noqa: E402
    ensure_valid_registry,
    valid_entries,
    validate_registry,
)


def test_historical_windows_asset_entry_is_accepted():
    registry = {
        "20zhejiang1_p0.05_c3": {
            "task": "20zhejiang1",
            "file_path": r"E:\Data\843output\20zhejiang1\result.tif",
            "prob_threshold": 0.05,
            "min_count": 3,
            "file_size_mb": 2.1,
        }
    }
    assert validate_registry(registry) == []
    valid, errors = valid_entries(registry)
    assert errors == []
    assert valid == registry


def test_invalid_rows_are_filtered_without_rewriting_input():
    registry = {
        "valid": {"task": "t", "file_path": "result.tif"},
        "bad": {"task": "t", "file_path": ["not", "a", "path"]},
        "scalar": "not an asset",
    }
    valid, errors = valid_entries(registry)
    assert set(valid) == {"valid"}
    assert len(errors) == 2
    assert isinstance(registry["bad"]["file_path"], list)


def test_write_boundary_rejects_non_finite_numeric_values():
    registry = {"bad": {"task": "t", "file_size_mb": float("nan")}}
    assert validate_registry(registry)
    try:
        ensure_valid_registry(registry)
    except ValueError as exc:
        assert "资产注册表结构无效" in str(exc)
    else:
        raise AssertionError("invalid registry unexpectedly accepted")
