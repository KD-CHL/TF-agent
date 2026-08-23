"""AGENT-007 index execution adapter contract tests."""
from __future__ import annotations

import os
import sys

_TF_AGENT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent"))
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

import index_agent_loop  # noqa: E402


def test_index_plan_validation_blocks_missing_inputs(tmp_path):
    plan = index_agent_loop.build_index_plan(
        task="T1", input_dir=str(tmp_path / "missing"),
        output_dir=str(tmp_path / "out"), points_shp=str(tmp_path / "points.shp"),
    )
    ok, blockers = index_agent_loop.validate_index_plan(plan)
    assert not ok
    assert any("影像目录" in item for item in blockers)
    result = index_agent_loop.execute_index_plan(
        plan, push_log=lambda _: None, push_progress=lambda _: None,
        stop_callback=lambda: False,
    )
    assert result["status"] == "BLOCKED"
    assert result["success"] is False


def test_verify_index_result_requires_non_empty_file(tmp_path):
    empty = tmp_path / "empty.tif"
    empty.touch()
    assert index_agent_loop.verify_index_result(str(empty))["ok"] is False
    valid = tmp_path / "result.tif"
    valid.write_bytes(b"fixture")
    checked = index_agent_loop.verify_index_result(str(valid))
    assert checked["ok"] is True
    assert checked["result_path"] == str(valid)
