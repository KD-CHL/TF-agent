# -*- coding: utf-8 -*-
"""指数法执行适配器。

统一 Agent/UI 的计划、校验、执行和验证边界；底层算法仍由 ``index_engine``
提供。模块不导入 Streamlit，便于离线测试与后台线程调用。
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Callable, Dict, Optional, Tuple

from agent_context_policy import safe_error_summary, sanitize_external_text


def build_index_plan(
    *, task: str, input_dir: str, output_dir: str, points_shp: str,
    force_rerun: bool = False,
) -> Dict[str, Any]:
    return {
        "schema": "index_tidal_flat_plan_v1",
        "plan_id": f"index_{uuid.uuid4().hex}",
        "task": str(task or "").strip(),
        "input_dir": os.path.abspath(os.path.expanduser(str(input_dir or ""))),
        "output_dir": os.path.abspath(os.path.expanduser(str(output_dir or ""))),
        "points_shp": os.path.abspath(os.path.expanduser(str(points_shp or ""))),
        "force_rerun": bool(force_rerun),
    }


def validate_index_plan(plan: Dict[str, Any]) -> Tuple[bool, list[str]]:
    blockers = []
    if not str(plan.get("task") or "").strip():
        blockers.append("未选择有效目标任务。")
    input_dir = str(plan.get("input_dir") or "")
    if not os.path.isdir(input_dir):
        blockers.append("原始影像目录不存在。")
    elif not any(name.lower().endswith((".tif", ".tiff")) for name in os.listdir(input_dir)):
        blockers.append("原始影像目录没有可处理的 GeoTIFF。")
    points = str(plan.get("points_shp") or "")
    if not os.path.isfile(points):
        blockers.append("海洋种子点矢量不存在。")
    output_dir = str(plan.get("output_dir") or "")
    if not output_dir:
        blockers.append("指数法输出目录未配置。")
    return not blockers, blockers


def verify_index_result(result_path: Optional[str]) -> Dict[str, Any]:
    path = str(result_path or "")
    ok = bool(path and os.path.isfile(path) and os.path.getsize(path) > 0)
    return {"ok": ok, "result_path": path if ok else None, "error": None if ok else "指数法未生成有效结果文件。"}


def execute_index_plan(
    plan: Dict[str, Any], *, push_log: Callable[[str], None],
    push_progress: Callable[[int], None], stop_callback: Callable[[], bool],
    register_asset: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """执行一次已校验计划，返回真实结果，不伪造成功。"""
    valid, blockers = validate_index_plan(plan)
    if not valid:
        return {"success": False, "status": "BLOCKED", "blockers": blockers, "result_path": None}
    if stop_callback():
        return {"success": False, "status": "CANCELLED", "error": "用户已中断。", "result_path": None}

    import index_engine

    output_dir = plan["output_dir"]
    os.makedirs(output_dir, exist_ok=True)
    output_tif = os.path.join(output_dir, f"{plan['task']}_Index_Final.tif")
    work_dir = os.path.join(output_dir, "index_work")
    push_progress(0)
    push_log(f"INIT INDEX TASK: {plan['task']}")
    try:
        result = index_engine.run_index_pipeline(
            input_dir=plan["input_dir"], output_tif=output_tif,
            points_shp=plan["points_shp"], work_dir=work_dir,
            push_log=push_log, push_progress=push_progress,
            stop_callback=stop_callback,
        )
    except Exception as exc:  # noqa: BLE001
        push_log(f"指数法异常: {safe_error_summary(exc)}")
        return {"success": False, "status": "FAILED", "error": "指数法执行异常。", "result_path": None}
    if stop_callback():
        return {"success": False, "status": "CANCELLED", "error": "用户已中断。", "result_path": None}
    verification = verify_index_result(result)
    if not verification["ok"]:
        return {"success": False, "status": "FAILED", "error": verification["error"], "result_path": None}
    if register_asset:
        register_asset(result)
    push_progress(100)
    return {"success": True, "status": "SUCCEEDED", "result_path": result, "verification": verification}


def summarize_index_result(result: Dict[str, Any]) -> str:
    if not result or result.get("success") is not True:
        error = sanitize_external_text((result or {}).get("error") or "计划被阻断或未生成有效成果。")[:240]
        return f"指数法未完成：{error}"
    return "指数法潮滩提取完成，结果已通过文件存在性校验并登记。"


__all__ = [
    "build_index_plan", "validate_index_plan", "verify_index_result",
    "execute_index_plan", "summarize_index_result",
]
