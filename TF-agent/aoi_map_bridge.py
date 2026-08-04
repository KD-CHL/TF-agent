# -*- coding: utf-8 -*-
"""
AOI → 地图图层回声桥（Phase D §6.5）。

- 收到 CSTF_AOI_SELECTED → 校验 → 存 _active_aoi → 回发 CSTF_LAYER_ADD
  {layer_id: "aoi:<id>", kind: geojson, data: 规范化几何}（稳定 aoi_id）。
- 重复选择同区域：先 REMOVE 再 ADD，避免叠加。
- 清除 AOI：仅 LAYER_REMOVE aoi:<id>，**不清除业务图层**。
- 校验失败：不回声，ack 为 error。
- 选定 ≠ 确认：不触碰任何确认门闩；不自动触发任务。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from aoi_context import AOIContext, compact_summary, validate_aoi


def build_echo_messages(aoi: AOIContext, previous_aoi_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """构建回声消息列表。校验失败 → 空列表（不回声）。

    重复选择（含同 id 重新高亮）：先 REMOVE 再 ADD，避免叠加。
    """
    if not aoi.valid:
        return []
    msgs: List[Dict[str, Any]] = []
    layer_id = f"aoi:{aoi.aoi_id}"
    if previous_aoi_id:
        msgs.append(
            {
                "type": "CSTF_LAYER_REMOVE",
                "version": 1,
                "layer_id": f"aoi:{previous_aoi_id}",
                "reason": "aoi_replace",
            }
        )
    msgs.append(
        {
            "type": "CSTF_LAYER_ADD",
            "version": 1,
            "layer_id": layer_id,
            "kind": "geojson",
            "data": aoi.geometry,
            "label": aoi.label,
        }
    )
    return msgs


def build_clear_messages(aoi_id: str, business_layer_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """清除 AOI 消息：只移除 aoi:<id>，绝不触碰业务图层。"""
    return [
        {
            "type": "CSTF_LAYER_REMOVE",
            "version": 1,
            "layer_id": f"aoi:{aoi_id}",
            "reason": "aoi_clear",
        }
    ]


def process_aoi_selected(state: Dict[str, Any], *, geometry, source: str,
                         label: Optional[str] = None) -> Dict[str, Any]:
    """处理 CSTF_AOI_SELECTED。返回 {ok, aoi, echo, errors}。"""
    aoi = validate_aoi(geometry, source=source, label=label)
    prev_id = None
    prev = state.get("_active_aoi")
    if isinstance(prev, AOIContext):
        prev_id = prev.aoi_id
    elif isinstance(prev, dict):
        prev_id = prev.get("aoi_id")
    if not aoi.valid:
        return {
            "ok": False,
            "aoi": aoi,
            "echo": None,
            "errors": list(aoi.warnings),
            "confirmed": None,
        }
    state["_active_aoi"] = aoi
    msgs = build_echo_messages(aoi, previous_aoi_id=prev_id)
    return {
        "ok": True,
        "aoi": aoi,
        "echo": msgs,  # 可能同时含 REMOVE(旧 AOI) + ADD(新 AOI)
        "errors": [],
        "confirmed": None,  # 选定 AOI 永不携带确认语义
    }


def process_aoi_cleared(state: Dict[str, Any]) -> Dict[str, Any]:
    """处理 CSTF_AOI_CLEARED。仅移除 AOI 图层回声。"""
    prev = state.get("_active_aoi")
    prev_id = None
    if isinstance(prev, AOIContext):
        prev_id = prev.aoi_id
    elif isinstance(prev, dict):
        prev_id = prev.get("aoi_id")
    state["_active_aoi"] = None
    if prev_id:
        msgs = build_clear_messages(prev_id)
        return {"ok": True, "echo": msgs[0], "errors": []}
    return {"ok": True, "echo": None, "errors": []}


def aoi_recommendation_text(aoi: AOIContext, capabilities: Optional[Dict[str, str]]) -> str:
    """Copilot 注入：AOI 摘要 + 基于能力的推荐（白名单字段）。"""
    lines = [compact_summary(aoi)]
    if not aoi.valid:
        lines.append("该 AOI 无效，仅作参考，不推荐任何执行。")
        return "\n".join(lines)
    caps = capabilities or {}
    suggestions = []
    if caps.get("deep_learning_inference") == "AVAILABLE":
        suggestions.append("可对 AOI 区域发起推理")
    if caps.get("gee_download") == "AVAILABLE":
        suggestions.append("可发起 GEE 下载")
    elif caps.get("gee_download") == "BLOCKED":
        suggestions.append("GEE 下载不可用，不建议发起下载")
    elif caps.get("gee_download") == "CONDITIONAL":
        suggestions.append("GEE 下载受限，需确认前置条件")
    if not suggestions:
        suggestions.append("暂无推荐执行项")
    lines.append("推荐: " + "；".join(suggestions))
    lines.append("注意: AOI 仅提供空间上下文，任何推理/下载/M5/E1 仍需用户确认。")
    return "\n".join(lines)


def parse_aoi_message(data: Dict[str, Any]) -> Dict[str, Any]:
    """解析 Cesium 发来的 AOI 消息。返回 {kind, ok, payload, errors}。"""
    msg_type = data.get("type", "")
    errors = []
    payload = {}
    if msg_type == "CSTF_AOI_SELECTED":
        geometry = data.get("geometry")
        source = data.get("source") or "map_polygon"
        label = data.get("label")
        if not geometry or geometry.get("type") != "Polygon":
            errors.append("AOI 消息缺少合法 Polygon geometry")
        else:
            payload = {"geometry": geometry, "source": source, "label": label}
    elif msg_type == "CSTF_AOI_CLEARED":
        pass
    else:
        errors.append(f"未知 AOI 消息类型: {msg_type}")
    return {
        "kind": msg_type,
        "ok": not errors,
        "payload": payload,
        "errors": errors,
    }
