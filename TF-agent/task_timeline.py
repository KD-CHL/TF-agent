# -*- coding: utf-8 -*-
"""
统一任务执行时间线（Phase C）。

- 事件模型 TimelineEvent：PLAN→VALIDATE→CONFIRM→QUEUED→EXECUTE→VERIFY→REGISTER→MAP→REPORT
- 状态机：非法迁移拒绝并记 WARNING（如 SUCCEEDED→RUNNING）。
- 账本：TF-agent/data/timeline_ledger.json，原子写（临时文件 + os.replace），无数据库。
- 恢复语义：内存（rerun）/ 磁盘（refresh / process-restart），restored_from 区分。
- 安全：details 过滤 token/密钥/绝对路径；artifacts 仅相对路径。
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# 阶段与状态枚举（与设计文档 5.1 一致）
# INFERENCE / POST_PROCESS 为本地潮滩推理闭环细化阶段；
# M5/E1 等既有闭环继续使用通用 EXECUTE，向后兼容。
PHASES: Tuple[str, ...] = (
    "PLAN",
    "VALIDATE",
    "CONFIRM",
    "QUEUED",
    "INFERENCE",
    "POST_PROCESS",
    "EXECUTE",
    "GEE_EXPORT",
    "WAIT_REMOTE",
    "FETCH_OUTPUT",
    "VERIFY",
    "REGISTER",
    "MAP",
    "REPORT",
)
STATUSES: Tuple[str, ...] = (
    "PENDING",
    "WAITING_CONFIRMATION",
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "BLOCKED",
    "CANCELLED",
    "WARNING",
)

# 合法状态迁移：PENDING 之外的状态各定义可去向
_TRANSITIONS: Dict[str, frozenset] = {
    "PENDING": frozenset(
        {"WAITING_CONFIRMATION", "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED", "WARNING"}
    ),
    "WAITING_CONFIRMATION": frozenset({"QUEUED", "CANCELLED", "BLOCKED", "WARNING", "RUNNING"}),
    "QUEUED": frozenset({"RUNNING", "CANCELLED", "BLOCKED", "FAILED", "WARNING"}),
    "RUNNING": frozenset({"SUCCEEDED", "FAILED", "BLOCKED", "WARNING"}),
    "SUCCEEDED": frozenset({"WARNING"}),
    "FAILED": frozenset({"WARNING"}),
    "BLOCKED": frozenset({"WARNING", "RUNNING"}),
    "CANCELLED": frozenset({"WARNING"}),
    "WARNING": frozenset({"RUNNING", "SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED", "WARNING"}),
}

# 敏感键 / 敏感值（与 capability_registry 同策略）
_SENSITIVE_KEY_SUBSTRINGS = ("token", "secret", "password", "api_key", "ion", "key")
_SENSITIVE_VALUE_SUBSTRINGS = ("Z:/", "C:\\", "/home/", "token=", "key=", "sk-")


def _now_str(now_fn=None) -> str:
    if now_fn is not None:
        return now_fn()
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sanitize_details(details: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in details.items():
        kl = str(k).lower()
        if any(s in kl for s in _SENSITIVE_KEY_SUBSTRINGS):
            continue
        if isinstance(v, str):
            vl = v.lower()
            if any(s.lower() in vl for s in _SENSITIVE_VALUE_SUBSTRINGS):
                continue
        out[k] = v
    return out


def _relative_artifacts(artifacts: List[str]) -> List[str]:
    out = []
    for a in artifacts or []:
        if not a:
            continue
        if os.path.isabs(a):
            continue
        out.append(a)
    return out


@dataclass
class TimelineEvent:
    """时间线事件（设计文档 5.1）。"""

    task_id: str
    phase: str
    message: str
    status: str = "PENDING"
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    plan_id: Optional[str] = None
    tool: Optional[str] = None
    progress: Optional[int] = None
    details: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[str] = field(default_factory=list)
    error: Optional[str] = None
    created_at: str = field(default_factory=_now_str)
    updated_at: str = field(default_factory=_now_str)

    def __post_init__(self) -> None:
        if self.phase not in PHASES:
            raise ValueError(f"非法阶段: {self.phase}")
        if self.status not in STATUSES:
            raise ValueError(f"非法状态: {self.status}")
        if self.progress is not None and not (0 <= int(self.progress) <= 100):
            raise ValueError(f"progress 越界: {self.progress}")
        self.artifacts = _relative_artifacts(self.artifacts or [])
        self.details = _sanitize_details(self.details or {})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in _TRANSITIONS.get(from_status, frozenset())


class TimelineStore:
    """事件存储 + 原子 JSON 账本。

    - 内存态：events 列表（rerun-restore 自然保留）。
    - 磁盘态：ledger_path 原子写；load() 恢复（refresh / process-restart）。
    - restored_from: "memory"（本进程新建）| "disk"（从账本恢复）。
    """

    def __init__(self, ledger_path: Optional[str] = None, now_fn=None) -> None:
        self._events: List[TimelineEvent] = []
        self._now_fn = now_fn
        self.restored_from: str = "memory"
        self.ledger_path: Optional[str] = ledger_path

    # ---- 写 ----
    def add(
        self,
        task_id: str,
        phase: str,
        message: str,
        *,
        status: str = "PENDING",
        plan_id: Optional[str] = None,
        tool: Optional[str] = None,
        progress: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
        artifacts: Optional[List[str]] = None,
        error: Optional[str] = None,
    ) -> TimelineEvent:
        ev = TimelineEvent(
            task_id=task_id,
            phase=phase,
            message=message,
            status=status,
            plan_id=plan_id,
            tool=tool,
            progress=progress,
            details=details or {},
            artifacts=artifacts or [],
            error=error,
            created_at=_now_str(self._now_fn),
            updated_at=_now_str(self._now_fn),
        )
        self._events.append(ev)
        return ev

    def update(
        self,
        event_id: str,
        *,
        status: Optional[str] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Tuple[bool, Optional[TimelineEvent]]:
        """更新事件；非法迁移拒绝并记 WARNING。返回 (ok, event)。"""
        for ev in self._events:
            if ev.event_id != event_id:
                continue
            if status is not None and status != ev.status:
                if not can_transition(ev.status, status):
                    warns = list(ev.details.get("warnings", []))
                    warns.append(f"非法状态迁移: {ev.status}→{status} 被拒绝")
                    ev.details = dict(ev.details)
                    ev.details["warnings"] = warns
                    ev.updated_at = _now_str(self._now_fn)
                    return False, ev
                ev.status = status
            if progress is not None:
                if not (0 <= int(progress) <= 100):
                    return False, ev
                ev.progress = int(progress)
            if message is not None:
                ev.message = message
            if error is not None:
                ev.error = error
            ev.updated_at = _now_str(self._now_fn)
            return True, ev
        return False, None

    # ---- 读 ----
    def events(
        self,
        *,
        task_id: Optional[str] = None,
        phase: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[TimelineEvent]:
        out = list(self._events)
        if task_id is not None:
            out = [e for e in out if e.task_id == task_id]
        if phase is not None:
            out = [e for e in out if e.phase == phase]
        if status is not None:
            out = [e for e in out if e.status == status]
        if limit is not None:
            out = out[-int(limit):]
        return out

    def latest(self, task_id: Optional[str] = None) -> Optional[TimelineEvent]:
        evs = self.events(task_id=task_id)
        return evs[-1] if evs else None

    def compact_summary(self, limit: int = 20) -> str:
        """人读紧凑摘要（按时间倒序），不含绝对路径/敏感值。"""
        lines = []
        for ev in reversed(self.events(limit=limit)):
            art = ",".join(ev.artifacts) if ev.artifacts else "-"
            lines.append(
                f"[{ev.updated_at}] {ev.phase}/{ev.status} {ev.message} (产物: {art})"
            )
        return "\n".join(lines) if lines else "(暂无时间线事件)"

    # ---- 持久化 ----
    def save(self) -> None:
        if not self.ledger_path:
            return
        ledger_dir = os.path.dirname(os.path.abspath(self.ledger_path))
        os.makedirs(ledger_dir, exist_ok=True)
        payload = json.dumps(
            [e.to_dict() for e in self._events], ensure_ascii=False, indent=2
        )
        fd, tmp_path = tempfile.mkstemp(
            prefix=".timeline_ledger_", suffix=".tmp", dir=ledger_dir
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_path, self.ledger_path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def load(self) -> int:
        """从账本恢复事件。返回恢复数量；无账本返回 0。"""
        if not self.ledger_path or not os.path.isfile(self.ledger_path):
            return 0
        with open(self.ledger_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            return 0
        restored = 0
        for item in raw:
            try:
                ev = TimelineEvent(**item)
            except (TypeError, ValueError, KeyError):
                continue  # 坏记录跳过，不中断恢复
            self._events.append(ev)
            restored += 1
        if restored:
            self.restored_from = "disk"
        return restored
