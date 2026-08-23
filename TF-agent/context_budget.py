# -*- coding: utf-8 -*-
"""Agent 对话上下文预算器。"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from agent_context_policy import redact_spatial_metadata, sanitize_external_text


def bound_messages(
    messages: Iterable[Dict[str, Any]], *, max_messages: int = 24, max_chars: int = 40_000,
    allow_spatial_metadata: bool = False,
) -> List[Dict[str, Any]]:
    rows = [dict(m) for m in messages if isinstance(m, dict)]
    if not rows:
        return []
    # 保留第一条系统/欢迎语与最新消息；总条数必须严格受预算约束。
    try:
        message_budget = max(0, int(max_messages))
    except (TypeError, ValueError):
        message_budget = 24
    if message_budget == 0:
        return []
    head = rows[:1] if rows[0].get("role") == "system" else []
    tail_count = max(0, message_budget - len(head))
    tail = rows[-tail_count:] if tail_count else []
    selected = head + [m for m in tail if m not in head]
    selected = [
        dict(
            m,
            content=(
                sanitize_external_text(m.get("content"))
                if allow_spatial_metadata
                else redact_spatial_metadata(sanitize_external_text(m.get("content")))
            ),
        )
        for m in selected
    ]
    total = sum(len(str(m.get("content") or "")) for m in selected)
    if total <= max_chars:
        return selected
    kept: List[Dict[str, Any]] = []
    used = 0
    for item in reversed(selected):
        content = str(item.get("content") or "")
        if used + len(content) > max_chars:
            remaining = max_chars - used
            if remaining > 80:
                kept.append({**item, "content": content[-remaining:]})
            break
        kept.append(item)
        used += len(content)
    return list(reversed(kept))


__all__ = ["bound_messages"]
