# -*- coding: utf-8 -*-
"""运行 Agent 验收矩阵。

默认只执行离线核心测试；DashScope/GEE/GPU/浏览器验收必须分别显式开启。
脚本不打印密钥、不把绝对路径写入报告，外部测试的临时输出在 finally 中清理。

示例：
    python tests/acceptance/run_acceptance_matrix.py
    RUN_EXTERNAL_ACCEPTANCE=1 RUN_DASHSCOPE_ACCEPTANCE=1 \
      python tests/acceptance/run_acceptance_matrix.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = ROOT / "tests" / "acceptance" / "_out" / "acceptance_matrix.json"
EXTERNAL_SWITCHES = {
    "dashscope": "RUN_DASHSCOPE_ACCEPTANCE",
    "gee": "RUN_GEE_ACCEPTANCE",
    "gpu": "RUN_GPU_ACCEPTANCE",
    "browser": "RUN_BROWSER_ACCEPTANCE",
}
BUDGETS = {
    "gee_max_aoi_km2": 25,
    "gee_max_days": 31,
    "gee_max_scenes": 3,
    "dashscope_max_requests": 5,
    "dashscope_max_tokens": 512,
    "dashscope_max_image_bytes": 2 * 1024 * 1024,
    "gpu_max_fixtures": 1,
}
# Keep the fixed probe geometry safely below the 25 km² budget at 30°N.
GEE_AOI_BOUNDS = (120.0, 30.0, 120.04, 30.04)
_SECRET_RE = re.compile(r"(?i)(?:api[_-]?key|token|secret|password|authorization)[=: ]+[^\s,;]+")
_BARE_PROVIDER_KEY_RE = re.compile(r"(?i)\bsk-[A-Za-z0-9][A-Za-z0-9_-]{8,}")
_ABS_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|private|tmp|var|opt|Volumes|mnt|srv|workspace|app|data)/)\S*"
)


def _sanitize(value: Any, *, limit: int = 800) -> Any:
    if isinstance(value, dict):
        return {str(k): _sanitize(v, limit=limit) for k, v in value.items() if "key" not in str(k).lower() and "token" not in str(k).lower() and "secret" not in str(k).lower()}
    if isinstance(value, list):
        return [_sanitize(v, limit=limit) for v in value[:40]]
    if isinstance(value, str):
        text = _SECRET_RE.sub("<redacted>", value)
        text = _BARE_PROVIDER_KEY_RE.sub("<redacted>", text)
        text = _ABS_PATH_RE.sub("<path>", text)
        return text[:limit]
    return value


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _case(name: str, fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        payload = fn() or {}
        status = str(payload.pop("status", "PASS"))
        return _sanitize({"name": name, "status": status, "duration_s": round(time.monotonic() - started, 3), **payload})
    except Exception as exc:  # noqa: BLE001
        return _sanitize({
            "name": name, "status": "FAIL", "duration_s": round(time.monotonic() - started, 3),
            "error_type": type(exc).__name__, "error": str(exc),
        })


def _offline_command() -> List[str]:
    return [
        sys.executable, "-m", "pytest",
        "tests/smoke/test_app_boot.py",
        "tests/smoke/test_streamlit_apptest.py",
        "tests/unit/test_agent_commands.py",
        "tests/unit/test_p0_hardening.py",
        "tests/unit/test_workflow_orchestrator.py",
        "tests/unit/test_agent_task_timeline.py",
        "tests/unit/test_acceptance_matrix.py",
        "tests/unit/test_gateway_auth.py",
        "tests/unit/test_local_api_auth.py",
        "-q", "--tb=short", "-p", "no:cacheprovider",
    ]


def run_offline_tests() -> Dict[str, Any]:
    command = _offline_command()
    started = time.monotonic()
    env = os.environ.copy()
    # The offline subprocess must remain deterministic even when the parent
    # shell loaded a developer .env for an external probe.
    for key in ("RUN_EXTERNAL_ACCEPTANCE", *EXTERNAL_SWITCHES.values()):
        env[key] = "0"
    for key in (
        "DASHSCOPE_API_KEY", "CSTF_LLM_API_KEY", "QWEN_API_KEY",
        "CSTF_LLM_BACKEND", "CSTF_LLM_MODEL", "CSTF_LLM_BASE_URL",
        "CSTF_LOCAL_SUPPORTS_TOOLS", "CSTF_LOCAL_SUPPORTS_VISION",
        "QWEN_OPENAI_BASE_URL", "QWEN_CHAT_MODEL",
        "EE_PROJECT", "GOOGLE_CLOUD_PROJECT", "EARTHENGINE_PROJECT",
        "CSTF_GPU_MODEL_PATH", "MODEL_PATH",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ):
        env[key] = ""
    proc = subprocess.run(command, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return {
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "command": " ".join(command),
        "returncode": proc.returncode,
        "duration_s": round(time.monotonic() - started, 3),
        "output_digest": _digest(combined),
        "output_tail": combined[-1600:],
        "network_opt_in": False,
    }


def _skip_external(name: str, reason: str) -> Dict[str, Any]:
    return {"status": "SKIPPED", "provider": name, "reason": reason, "budget": BUDGETS}


def _external_allowed(name: str) -> bool:
    return os.environ.get("RUN_EXTERNAL_ACCEPTANCE", "0") == "1" and os.environ.get(EXTERNAL_SWITCHES[name], "0") == "1"


def run_dashscope_probe() -> Dict[str, Any]:
    if not _external_allowed("dashscope"):
        return _skip_external("dashscope", "需要 RUN_EXTERNAL_ACCEPTANCE=1 且 RUN_DASHSCOPE_ACCEPTANCE=1")
    api_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("CSTF_LLM_API_KEY")
    if not api_key:
        return _skip_external("dashscope", "未配置 DashScope API key")
    try:
        from openai import OpenAI
    except ImportError:
        return _skip_external("dashscope", "当前环境未安装 openai 客户端")
    client = OpenAI(
        api_key=api_key,
        base_url=os.environ.get("QWEN_OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        timeout=20.0,
        max_retries=0,
    )
    response = client.chat.completions.create(
        model=os.environ.get("QWEN_CHAT_MODEL", "qwen-plus"),
        messages=[{"role": "user", "content": "仅回答：验收连通。"}],
        max_tokens=32,
    )
    return {"status": "PASS", "provider": "dashscope", "requests": 1, "max_tokens": 32, "response_digest": _digest(str(response))}


def run_gee_probe() -> Dict[str, Any]:
    if not _external_allowed("gee"):
        return _skip_external("gee", "需要 RUN_EXTERNAL_ACCEPTANCE=1 且 RUN_GEE_ACCEPTANCE=1")
    try:
        import ee
    except ImportError:
        return _skip_external("gee", "当前环境未安装 earthengine-api")
    project = os.environ.get("EE_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("EARTHENGINE_PROJECT")
    if not project:
        return _skip_external("gee", "未配置 GEE project")
    ee.Initialize(project=project)
    # 只验证受限时间窗/集合查询，不自动扩大 AOI 或重试消费配额。
    geometry = ee.Geometry.Rectangle(list(GEE_AOI_BOUNDS))
    collection = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(geometry).filterDate("2024-01-01", "2024-01-31").limit(BUDGETS["gee_max_scenes"])
    count = int(collection.size().getInfo())
    return {"status": "PASS", "provider": "gee", "aoi_km2_upper_bound": 25, "date_days": 31, "scene_count": min(count, BUDGETS["gee_max_scenes"])}


def run_gpu_probe() -> Dict[str, Any]:
    if not _external_allowed("gpu"):
        return _skip_external("gpu", "需要 RUN_EXTERNAL_ACCEPTANCE=1 且 RUN_GPU_ACCEPTANCE=1")
    try:
        import torch
    except ImportError:
        return _skip_external("gpu", "当前环境未安装 torch")
    model_path = os.environ.get("CSTF_GPU_MODEL_PATH") or os.environ.get("MODEL_PATH")
    if not model_path or not os.path.isfile(model_path):
        return _skip_external("gpu", "未配置真实权重路径（CSTF_GPU_MODEL_PATH/MODEL_PATH）")
    checksum = _digest(Path(model_path).read_bytes()[:1024 * 1024].hex())
    return {"status": "PASS", "provider": "gpu", "device": "cuda" if torch.cuda.is_available() else "cpu", "weights_head_digest": checksum, "fixtures": 1}


def run_browser_probe() -> Dict[str, Any]:
    if not _external_allowed("browser"):
        return _skip_external("browser", "需要 RUN_EXTERNAL_ACCEPTANCE=1 且 RUN_BROWSER_ACCEPTANCE=1")
    try:
        import playwright  # noqa: F401
    except ImportError:
        return _skip_external("browser", "当前环境未安装 Playwright")
    command = [sys.executable, "-m", "pytest", "tests/browser/test_streamlit_ui.py", "-q", "--tb=short", "-p", "no:cacheprovider"]
    proc = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, timeout=120, env=os.environ.copy())
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        return {"status": "FAIL", "provider": "browser", "returncode": proc.returncode, "output_digest": _digest(output), "output_tail": output[-1600:]}
    if " skipped" in output.lower() and " passed" not in output.lower():
        return _skip_external("browser", "Playwright 已安装但 Chromium 运行时未安装")
    return {"status": "PASS", "provider": "browser", "returncode": 0, "output_digest": _digest(output), "output_tail": output[-1600:]}


def build_report(*, include_external: bool = True) -> Dict[str, Any]:
    started = time.monotonic()
    cases = [_case("offline_core", run_offline_tests)]
    if include_external:
        cases.extend([
            _case("dashscope", run_dashscope_probe),
            _case("gee", run_gee_probe),
            _case("gpu", run_gpu_probe),
            _case("browser", run_browser_probe),
        ])
    return _sanitize({
        "schema": "cstf_acceptance_matrix_v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "root_output_isolated": True,
        "external_opt_in": os.environ.get("RUN_EXTERNAL_ACCEPTANCE", "0") == "1",
        "budgets": BUDGETS,
        "cases": cases,
        "duration_s": round(time.monotonic() - started, 3),
    })


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline/external CSTF acceptance matrix")
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--offline-only", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report_path = Path(args.report).expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # External hooks get an isolated temporary root even when a provider fails.
    with tempfile.TemporaryDirectory(prefix="cstf-acceptance-") as output_root:
        os.environ["CSTF_ACCEPTANCE_OUTPUT_ROOT"] = output_root
        report = build_report(include_external=not args.offline_only)
        report["temporary_output_cleaned"] = True
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "cases": report["cases"]}, ensure_ascii=False))
    return 0 if all(item["status"] in {"PASS", "SKIPPED"} for item in report["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
