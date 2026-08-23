# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sys

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

import dataset_assets  # noqa: E402


def test_corrupt_registry_is_preserved_before_empty_fallback(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("DATASET_ASSETS_REGISTRY_PATH", str(path))
    try:
        dataset_assets.load_registry()
    except ValueError as exc:
        assert "JSON 无效" in str(exc)
    else:
        raise AssertionError("corrupt registry unexpectedly accepted")
    backups = list(tmp_path.glob("registry.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{broken"


def test_non_object_registry_is_preserved(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("DATASET_ASSETS_REGISTRY_PATH", str(path))
    try:
        dataset_assets.load_registry()
    except ValueError as exc:
        assert "顶层结构无效" in str(exc)
    else:
        raise AssertionError("non-object registry unexpectedly accepted")
    assert list(tmp_path.glob("registry.json.corrupt-*"))


def test_malformed_registry_entry_is_preserved_and_rejected(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"broken": "not-an-object"}), encoding="utf-8")
    monkeypatch.setenv("DATASET_ASSETS_REGISTRY_PATH", str(path))
    try:
        dataset_assets.load_registry()
    except ValueError as exc:
        assert "记录结构无效" in str(exc)
    else:
        raise AssertionError("malformed registry entry unexpectedly accepted")
    assert list(tmp_path.glob("registry.json.corrupt-*"))


def test_registry_save_is_atomic_and_leaves_valid_json(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    monkeypatch.setenv("DATASET_ASSETS_REGISTRY_PATH", str(path))
    dataset_assets.save_registry({"d1": {"id": "d1"}})
    assert json.loads(path.read_text(encoding="utf-8")) == {"d1": {"id": "d1"}}
    assert not list(tmp_path.glob(".dataset_registry_*.tmp"))
