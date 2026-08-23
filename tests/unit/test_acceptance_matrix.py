"""AGENT-012 acceptance matrix is opt-in and report-safe."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tests.acceptance import run_acceptance_matrix as matrix  # noqa: E402


def test_external_probes_are_skipped_without_explicit_global_and_provider_flags(monkeypatch):
    monkeypatch.delenv("RUN_EXTERNAL_ACCEPTANCE", raising=False)
    monkeypatch.delenv("RUN_DASHSCOPE_ACCEPTANCE", raising=False)
    result = matrix.run_dashscope_probe()
    assert result["status"] == "SKIPPED"
    assert "RUN_EXTERNAL_ACCEPTANCE" in result["reason"]


def test_report_keeps_budgets_and_sanitizes_secrets(monkeypatch):
    monkeypatch.setenv("RUN_EXTERNAL_ACCEPTANCE", "0")
    with mock.patch.object(matrix, "run_offline_tests", return_value={"status": "PASS", "output_tail": "sk-secret /Users/chl/private/file"}):
        report = matrix.build_report(include_external=False)
    assert report["schema"] == "cstf_acceptance_matrix_v1"
    assert report["budgets"]["gee_max_aoi_km2"] == 25
    assert "sk-secret" not in str(report)
    assert "/Users/" not in str(report)


def test_report_sanitizer_covers_bare_keys_and_cross_platform_paths():
    clean = matrix._sanitize(
        r"sk-1234567890 C:\Users\chl\secret.pth \\server\share\result.tif /Volumes/External/a.tif"
    )
    assert "sk-1234567890" not in clean
    assert r"C:\Users\chl" not in clean
    assert r"\\server\share" not in clean
    assert "/Volumes/" not in clean


def test_gee_probe_geometry_stays_within_declared_budget():
    west, south, east, north = matrix.GEE_AOI_BOUNDS
    assert east > west and north > south
    # At 30°N, 0.04° x 0.04° is conservatively below 25 km².
    assert (east - west) <= 0.0400001
    assert (north - south) <= 0.0400001


def test_offline_subprocess_clears_external_configuration(monkeypatch):
    for key in ("EE_PROJECT", "DASHSCOPE_API_KEY", "HTTP_PROXY"):
        monkeypatch.setenv(key, "developer-value")
    completed = mock.Mock(returncode=0, stdout="offline ok", stderr="")
    with mock.patch.object(matrix.subprocess, "run", return_value=completed) as run:
        result = matrix.run_offline_tests()
    assert result["status"] == "PASS"
    child_env = run.call_args.kwargs["env"]
    assert child_env["EE_PROJECT"] == ""
    assert child_env["DASHSCOPE_API_KEY"] == ""
    assert child_env["HTTP_PROXY"] == ""


def test_ci_core_install_declares_runtime_dependencies_used_by_acceptance(monkeypatch):
    """CI must not silently rely on whatever packages the hosted runner preloads."""
    ci = (Path(_ROOT) / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    install_block = ci.split("- name: Install test deps", 1)[1].split("- name: Run unit tests", 1)[0]
    for package in ("fastapi", "uvicorn", "httpx", "aiohttp", "Pillow", "reportlab", "sentence-transformers"):
        assert package in install_block, f"CI install block missing core dependency: {package}"
