# -*- coding: utf-8 -*-
"""UI 与 Agent 共用的已验证执行请求契约。

该对象只描述“要执行什么、由谁确认、使用哪份计划”，不包含第二套执行逻辑。
真正的算法入口仍由各自可信闭环负责。
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional


EXECUTION_ENTRYPOINTS = {
    "dl": "inference_agent_loop",
    "index": "index_agent_loop",
    "gee": "gee_agent_loop",
    "m4": "gee_agent_loop",
    "m5": "m5_agent_loop",
    "e1": "e1_agent_loop",
    "workflow": "workflow_orchestrator",
    "autotune": "autotune",
}

_MODE_ALIASES = {
    "dl_inference": "dl",
    "deep_learning": "dl",
    "deep": "dl",
    "inference": "dl",
    "gee_download": "gee",
    "run_gee_download": "gee",
    "run_m4": "m4",
    "run_m5": "m5",
    "run_e1": "e1",
    "run_workflow": "workflow",
    "run_autotune": "autotune",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    mode = _MODE_ALIASES.get(mode, mode)
    if mode not in EXECUTION_ENTRYPOINTS:
        raise ValueError(f"不支持的执行模式: {mode or '空'}")
    return mode


@dataclass(frozen=True)
class ExecutionRequest:
    task: str
    mode: str
    plan_id: Optional[str] = None
    confirmation_source: str = "unknown"
    params: Dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: f"exec_{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not str(self.task or "").strip():
            raise ValueError("执行请求必须包含 task。")
        object.__setattr__(self, "task", str(self.task).strip())
        object.__setattr__(self, "mode", normalize_mode(self.mode))
        if self.plan_id is not None:
            object.__setattr__(self, "plan_id", str(self.plan_id))
        object.__setattr__(self, "confirmation_source", str(self.confirmation_source or "unknown"))
        object.__setattr__(self, "params", copy.deepcopy(dict(self.params or {})))

    @property
    def entrypoint(self) -> str:
        return EXECUTION_ENTRYPOINTS[self.mode]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["entrypoint"] = self.entrypoint
        data["schema"] = "execution_request_v1"
        return data


def from_pending_task(
    pending: Mapping[str, Any], *, confirmation_source: str = "unknown"
) -> Dict[str, Any]:
    """从 UI/Agent 的兼容 pending schema 生成同一份契约快照。"""
    raw = dict(pending or {})
    mode = raw.get("mode") or raw.get("job_kind")
    if not mode and raw.get("inference_plan"):
        mode = "dl"
    if not mode and raw.get("gee_plan"):
        mode = "gee"
    plan = raw.get("inference_plan") or raw.get("gee_plan") or raw.get("workflow_plan") or {}
    plan_id = raw.get("plan_id") or (plan.get("plan_id") if isinstance(plan, dict) else None)
    if normalize_mode(mode) == "workflow" and not plan_id:
        # Workflow requests do not always carry a child plan_id.  The
        # workflow identity is the stable idempotency boundary for JobStore.
        plan_id = raw.get("workflow_id") or (
            plan.get("workflow_id") if isinstance(plan, dict) else None
        )
    params = {
        key: copy.deepcopy(value)
        for key, value in raw.items()
        if key not in {"execution_request", "request_id", "created_at"}
    }
    existing = raw.get("execution_request")
    preserved_identity: Dict[str, str] = {}
    if isinstance(existing, Mapping) and existing.get("schema") == "execution_request_v1":
        try:
            existing_mode = normalize_mode(existing.get("mode"))
        except ValueError:
            existing_mode = ""
        existing_plan = existing.get("plan_id")
        same_plan = (str(existing_plan) if existing_plan is not None else None) == (
            str(plan_id) if plan_id is not None else None
        )
        if (
            str(existing.get("task") or "") == str(raw.get("task") or "")
            and existing_mode == normalize_mode(mode)
            and same_plan
            and str(existing.get("confirmation_source") or "unknown") == str(confirmation_source or "unknown")
            and existing.get("params") == params
            and str(existing.get("request_id") or "").strip()
        ):
            preserved_identity = {
                "request_id": str(existing["request_id"]),
                "created_at": str(existing.get("created_at") or ""),
            }
    request = ExecutionRequest(
        task=str(raw.get("task") or ""),
        mode=str(mode or ""),
        plan_id=plan_id,
        confirmation_source=confirmation_source,
        params=params,
        **preserved_identity,
    )
    return request.to_dict()


def attach_execution_request(
    pending: Mapping[str, Any], *, confirmation_source: str = "unknown"
) -> Dict[str, Any]:
    result = dict(pending or {})
    result["execution_request"] = from_pending_task(
        result, confirmation_source=confirmation_source
    )
    return result


__all__ = [
    "EXECUTION_ENTRYPOINTS", "ExecutionRequest", "attach_execution_request",
    "from_pending_task", "normalize_mode",
]
