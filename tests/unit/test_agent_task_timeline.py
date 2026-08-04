# -*- coding: utf-8 -*-
"""Phase C: 统一任务执行时间线 — task_timeline.py 的 TDD 测试。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

from task_timeline import PHASES, STATUSES, TimelineEvent, TimelineStore  # noqa: E402

# ---------------------------------------------------------------------------
# 模型层
# ---------------------------------------------------------------------------


class TestTimelineEventModel:
    def test_event_defaults_unique_id(self):
        e1 = TimelineEvent(task_id="t1", phase="PLAN", message="计划")
        e2 = TimelineEvent(task_id="t1", phase="PLAN", message="计划")
        assert e1.event_id != e2.event_id
        assert e1.status == "PENDING"
        assert e1.progress is None
        assert e1.created_at and e1.updated_at
        assert e1.artifacts == []

    def test_invalid_phase_rejected(self):
        with pytest.raises(ValueError):
            TimelineEvent(task_id="t", phase="NOPE", message="x")

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError):
            TimelineEvent(task_id="t", phase="PLAN", status="NOPE", message="x")

    def test_progress_bounds(self):
        with pytest.raises(ValueError):
            TimelineEvent(task_id="t", phase="EXECUTE", progress=101, message="x")
        with pytest.raises(ValueError):
            TimelineEvent(task_id="t", phase="EXECUTE", progress=-1, message="x")
        ok = TimelineEvent(task_id="t", phase="EXECUTE", progress=50, message="x")
        assert ok.progress == 50


# ---------------------------------------------------------------------------
# 内存存储 + 状态机
# ---------------------------------------------------------------------------


class TestTimelineStoreMemory:
    def test_add_and_latest(self):
        store = TimelineStore()
        ev = store.add("task-a", "PLAN", message="生成计划")
        assert store.latest() is ev
        ev2 = store.add("task-a", "CONFIRM", status="WAITING_CONFIRMATION", message="等确认")
        assert store.latest() is ev2
        assert store.latest("task-a") is ev2

    def test_filter_by_task_phase_status(self):
        store = TimelineStore()
        store.add("a", "PLAN", message="m1")
        store.add("a", "EXECUTE", status="RUNNING", message="m2")
        store.add("b", "PLAN", message="m3")
        assert len(store.events(task_id="a")) == 2
        assert len(store.events(phase="PLAN")) == 2
        assert len(store.events(status="RUNNING")) == 1
        assert len(store.events(task_id="a", phase="PLAN")) == 1

    def test_update_status_and_progress(self):
        store = TimelineStore()
        ev = store.add("a", "EXECUTE", message="跑")
        ok, ev2 = store.update(ev.event_id, status="SUCCEEDED", progress=100, message="完成")
        assert ok is True
        assert ev2.status == "SUCCEEDED"
        assert ev2.progress == 100
        assert ev2.updated_at >= ev2.created_at

    def test_illegal_transition_rejected_with_warning(self):
        store = TimelineStore()
        ev = store.add("a", "EXECUTE", status="SUCCEEDED", message="已完成")
        ok, ev2 = store.update(ev.event_id, status="RUNNING")
        assert ok is False
        assert ev2.status == "SUCCEEDED"  # 状态未变
        warnings = ev2.details.get("warnings", [])
        assert any("非法状态迁移" in w for w in warnings)


# ---------------------------------------------------------------------------
# 账本持久化（原子写 + 恢复语义）
# ---------------------------------------------------------------------------


class TestLedgerPersistence:
    def _store(self, tmp_path: Path, **kw) -> TimelineStore:
        return TimelineStore(ledger_path=str(tmp_path / "timeline_ledger.json"), **kw)

    def test_save_writes_json_file(self, tmp_path):
        store = self._store(tmp_path)
        store.add("a", "PLAN", message="m")
        store.save()
        assert (tmp_path / "timeline_ledger.json").is_file()
        data = json.loads((tmp_path / "timeline_ledger.json").read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["task_id"] == "a"

    def test_save_atomic_no_tmp_leftover(self, tmp_path):
        store = self._store(tmp_path)
        for i in range(5):
            store.add("a", "EXECUTE", status="RUNNING", message=f"step {i}")
        store.save()
        leftovers = [p for p in tmp_path.iterdir() if p.suffix in (".tmp", ".bak")]
        assert leftovers == []
        data = json.loads((tmp_path / "timeline_ledger.json").read_text(encoding="utf-8"))
        assert len(data) == 5

    def test_load_restores_events(self, tmp_path):
        store = self._store(tmp_path)
        store.add("a", "PLAN", message="m1")
        store.add("a", "EXECUTE", status="SUCCEEDED", message="m2", progress=100)
        store.save()
        store2 = self._store(tmp_path)
        n = store2.load()
        assert n == 2
        assert len(store2.events()) == 2
        restored = store2.events(status="SUCCEEDED")[0]
        assert restored.progress == 100
        assert restored.event_id  # 事件身份保持

    def test_restored_from_disk_flag(self, tmp_path):
        store = self._store(tmp_path)
        store.add("a", "PLAN", message="m")
        store.save()
        assert store.restored_from == "memory"
        store2 = self._store(tmp_path)
        store2.load()
        assert store2.restored_from == "disk"

    def test_process_restart_restore_new_store(self, tmp_path):
        """进程重启模拟：新实例读同一 ledger，事件完整恢复（历史标注由 UI 决定）。"""
        store = self._store(tmp_path)
        ev = store.add("a", "CONFIRM", status="WAITING_CONFIRMATION", message="等确认")
        store.save()
        fresh = self._store(tmp_path)
        fresh.load()
        restored = fresh.events(task_id="a")[0]
        assert restored.event_id == ev.event_id
        assert restored.status == "WAITING_CONFIRMATION"
        # 新 store 追加事件不丢失旧事件
        fresh.add("a", "QUEUED", status="QUEUED", message="入队")
        fresh.save()
        fresh2 = self._store(tmp_path)
        fresh2.load()
        assert len(fresh2.events(task_id="a")) == 2


# ---------------------------------------------------------------------------
# 安全与摘要
# ---------------------------------------------------------------------------


class TestSafetyAndSummary:
    def test_summary_no_absolute_paths(self, tmp_path):
        store = TimelineStore()
        store.add(
            "a",
            "REGISTER",
            status="SUCCEEDED",
            message="产物已登记",
            artifacts=["data/out/a_mask.tif"],
            details={"asset_id": "a-2024"},
        )
        summary = store.compact_summary()
        assert "C:" not in summary and "Z:" not in summary
        assert "/home/" not in summary and "data/out/a_mask.tif" in summary

    def test_details_no_sensitive_values(self):
        store = TimelineStore()
        store.add(
            "a",
            "EXECUTE",
            status="FAILED",
            message="失败",
            details={"token": "sk-abc", "path": "Z:/secret/model.pth", "msg": "ok"},
        )
        ev = store.latest()
        assert "token" not in ev.details
        assert "sk-abc" not in json.dumps(ev.details)
        assert "Z:/" not in json.dumps(ev.details)
        assert ev.details["msg"] == "ok"

    def test_artifacts_relative_only(self):
        store = TimelineStore()
        store.add("a", "REGISTER", status="SUCCEEDED", message="m",
                  artifacts=["C:/Users/x/out.tif", "data/out/b.tif"])
        ev = store.latest()
        assert all(not os.path.isabs(a) for a in ev.artifacts)
        assert "data/out/b.tif" in ev.artifacts

    def test_ledger_no_database(self, tmp_path):
        """账本必须纯 JSON 文件，不允许任何数据库文件出现。"""
        store = TimelineStore(ledger_path=str(tmp_path / "timeline_ledger.json"))
        store.add("a", "PLAN", message="m")
        store.save()
        suffixes = {p.suffix.lower() for p in tmp_path.iterdir()}
        assert not (suffixes & {".db", ".sqlite", ".sqlite3", ".duckdb"})


# ---------------------------------------------------------------------------
# 与 agent_task_framework 集成
# ---------------------------------------------------------------------------


class TestFrameworkIntegration:
    def test_framework_reexports_timeline(self):
        import agent_task_framework as atf

        assert atf.TimelineEvent is TimelineEvent
        assert atf.TimelineStore is TimelineStore
        assert hasattr(atf, "STATE_PENDING_PLAN")

    def test_confirm_gate_still_enforced(self):
        import agent_task_framework as atf

        assert atf.require_confirm(False, label="M5") == "M5需用户确认后才能执行（confirmed=true 或侧栏确认）。"
        assert atf.require_confirm(True) is None

    def test_plan_ready_blocks_run(self):
        from agent_task_framework import TaskPlan

        plan = TaskPlan(schema="m5", ready=False, blockers=["当期 SHP 缺失"])
        assert plan.ready is False
        assert plan.blockers == ["当期 SHP 缺失"]
        assert atf_require_confirm(plan) is not None


def atf_require_confirm(plan) -> str:
    import agent_task_framework as atf

    return atf.require_confirm(plan.ready, label="任务")
