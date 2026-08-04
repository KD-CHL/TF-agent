# -*- coding: utf-8 -*-
"""
通用 Agent 任务框架（从 M5 可信闭环提炼，供未来 E1 / GEE / 推理复用）。

设计原则：
- 只抽象「验收中反复出现」的结构，不提前迁移现有 M5 业务逻辑。
- 业务引擎（m5_engine / e1_engine / …）仍由各闭环自行调用。
- Streamlit / 线程 / 资产登记仍由 app 层编排。

推荐生命周期（与 M5 已验证路径同构）：

    intent → propose(plan) → user confirm → run(engine) → verify → register → map → grounded reply
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class TaskPlan:
    """结构化执行计划：ready=False 时禁止进入 run。"""

    schema: str
    ready: bool
    blockers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.update(self.payload)
        return d


@dataclass
class VerifyResult:
    ok: bool
    checks: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def require_confirm(confirmed: bool, *, label: str = "任务") -> Optional[str]:
    """确认门闩：未确认返回错误文案，已确认返回 None。"""
    if confirmed:
        return None
    return f"{label}需用户确认后才能执行（confirmed=true 或侧栏确认）。"


def format_plan_markdown(
    title: str,
    plan: TaskPlan,
    *,
    confirm_hint: str = "请回复「确认」或点击确认按钮后开始",
) -> str:
    lines = [f"## {title}", ""]
    if plan.ready:
        lines.append(f"**状态：可执行**（{confirm_hint}）")
    else:
        lines.append("**状态：暂不可执行**")
        for b in plan.blockers:
            lines.append(f"- 阻塞：{b}")
    for w in plan.warnings:
        lines.append(f"- 注意：{w}")
    if plan.steps:
        lines.append("")
        lines.append("步骤：")
        for i, s in enumerate(plan.steps, 1):
            lines.append(f"{i}. {s}")
    return "\n".join(lines)


def grounded_summary(
    title: str,
    *,
    facts: List[str],
    verification: Optional[VerifyResult] = None,
    footnote: str = "以上内容均来自本次工具真实输出，而非模型臆测。",
) -> str:
    lines = [f"## {title}", ""]
    lines.extend(f"- {f}" for f in facts)
    if verification is not None:
        lines.append(f"- 输出校验：{'通过' if verification.ok else '未完全通过'}")
    lines.append("")
    lines.append(footnote)
    return "\n".join(lines)


# 各业务闭环建议在 state 中使用的键约定（可选）
STATE_PENDING_PLAN = "_agent_pending_plan"
STATE_PLAN_CONFIRMED = "_agent_plan_confirmed"
STATE_JOB_KIND = "job_kind"
