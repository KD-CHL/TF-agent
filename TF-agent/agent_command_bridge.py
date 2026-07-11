# -*- coding: utf-8 -*-
"""
Agent ↔ Streamlit 双轨网桥：JSON 指令解析、差量合流、pending 任务构建。
可独立于 Streamlit 运行单元测试（传入 dict 模拟 session_state）。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

_JSON_BLOCK_RE = re.compile(
    r"\[SYSTEM_COMMAND_JSON\]\s*(\{.*?\})\s*\[/SYSTEM_COMMAND_JSON\]",
    re.DOTALL | re.IGNORECASE,
)
_RE_CMD_MAP_PIPE = re.compile(
    r"COMMAND_UPDATE_MAP\s*\|\s*([-\d.]+)\s*\|\s*([-\d.]+)\s*\|\s*(\d+)",
    re.IGNORECASE,
)
_RE_CMD_PIPELINE = re.compile(
    r"COMMAND_RUN_PIPELINE\s*\|\s*([^|\n]+?)\s*\|\s*([-\d.]+)\s*\|\s*(\d+)",
    re.IGNORECASE,
)

# session_state 键 ↔ sidebar_states JSON 字段
SIDEBAR_KEY_MAP = {
    "workflow_tab": "ui_workflow",
    "run_mode": "ui_run_mode",
    "selected_task": "ui_selected_task",
    "root_dir": "ui_root_dir",
    "mask_root": "ui_mask_root",
    "final_root": "ui_final_root",
    "model_path": "ui_model_path",
    "shp_path": "ui_shp_path",
    "points_shp": "ui_points_shp",
    "task_aoi_shp": "ui_task_aoi_shp",
    "prob_th": "ui_prob_th",
    "min_cnt": "ui_min_cnt",
    "inference_mode": "ui_inference_mode",
    "adaptive_mode": "ui_adaptive_mode",
    "force_rerun": "ui_force_rerun",
    "m5_enabled": "ui_m5_enabled",
    "m5_baseline_shp": "ui_m5_baseline_shp",
    "e1_enabled": "ui_e1_enabled",
    "e1_data_root": "ui_e1_data_root",
    "e1_reference": "ui_e1_reference",
    "e1_compare_sources": "ui_e1_compare_sources",
    "e1_export_maps": "ui_e1_export_maps",
    "e1_export_heatmap": "ui_e1_export_heatmap",
    "m4_roi_path": "ui_m4_roi_path",
    "m4_roi_name": "ui_m4_roi_name",
    "m4_start_date": "ui_m4_start_date",
    "m4_end_date": "ui_m4_end_date",
    "m4_export_to": "ui_m4_export_to",
    "m4_drive_folder": "ui_m4_drive_folder",
    "m4_local_dir": "ui_m4_local_dir",
    "m4_cloud": "ui_m4_cloud_limit",
    "m4_min_land": "ui_m4_min_land",
    "m4_max_land": "ui_m4_max_land",
    "m4_min_pix": "ui_m4_min_pixel_count",
    "m4_bands": "ui_m4_bands",
    "m4_scale": "ui_m4_scale",
    "m4_gee_proxy": "ui_m4_gee_proxy",
    "m4_gee_project": "ui_m4_gee_project",
}

WORKFLOW_ALIASES = {
    "潮滩推理": "潮滩推理",
    "推理": "潮滩推理",
    "gee数据下载": "GEE 数据下载",
    "gee 数据下载": "GEE 数据下载",
    "gee": "GEE 数据下载",
    "下载": "GEE 数据下载",
}

RUN_MODE_MAP = {"dl": "dl", "deep": "dl", "深度学习": "dl", "index": "index", "指数法": "index"}

AUTOTUNE_OBJECTIVE_MAP = {
    "max_iou": "iou",
    "iou": "iou",
    "max_f1": "f1",
    "f1": "f1",
    "iou_f1": "iou_f1",
    "均衡": "iou_f1",
}


@dataclass
class ApplyResult:
    applied: bool = False
    queued: bool = False
    map_updated: bool = False
    sidebar_keys_updated: List[str] = field(default_factory=list)
    action_type: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    clean_reply_hint: str = ""


PENDING_AGENT_COMMANDS_KEY = "_pending_agent_commands"


def init_ui_session_defaults(state: Dict[str, Any]) -> None:
    """初始化侧栏 UI 绑定键（仅缺省时写入，不覆盖用户/Agent 已有值）。"""
    defaults = {
        "ui_workflow": "潮滩推理",
        "ui_run_mode": "dl",
        "ui_root_dir": r"I:\GEE_data\20",
        "ui_mask_root": r"E:\Data\843mask",
        "ui_final_root": r"E:\Data\843output",
        "ui_model_path": r"E:\Code\GEE\best_train_loss_model_resnet50.pth",
        "ui_shp_path": r"E:\Code\GEE\jb\water-line\max_water_extent23.shp",
        "ui_points_shp": os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "jb", "point", "points_export.shp")
        ),
        "ui_task_aoi_shp": r"E:\Data\CHINA_tf_city\china_costal.shp",
        "ui_inference_mode": "深度学习",
        "ui_adaptive_mode": False,
        "ui_prob_th": 0.05,
        "ui_min_cnt": 2,
        "ui_force_rerun": False,
        "ui_m5_enabled": True,
        "ui_m5_baseline_shp": "",
        "ui_e1_enabled": False,
        "ui_e1_data_root": r"E:\潮滩数据集",
        "ui_e1_reference": "师姐_2020",
        "ui_e1_compare_sources": [],
        "ui_e1_export_maps": True,
        "ui_e1_export_heatmap": True,
        "ui_m4_roi_path": r"E:\Data\CHINA_tf_city\china_costal.shp",
        "ui_m4_roi_name": "",
        "ui_m4_start_date": "2020-01-01",
        "ui_m4_end_date": "2020-01-31",
        "ui_m4_export_to": "drive",
        "ui_m4_drive_folder": "GEE_Downloads",
        "ui_m4_local_dir": "",
        "ui_m4_cloud_limit": 60,
        "ui_m4_min_land": 5.0,
        "ui_m4_max_land": 95.0,
        "ui_m4_min_pixel_count": 1000,
        "ui_m4_bands": ["B8", "B4", "B3", "B2", "B11"],
        "ui_m4_scale": 10,
        "ui_m4_gee_proxy": "",
        "ui_m4_gee_project": os.environ.get("EE_PROJECT", "").strip(),
    }
    for k, v in defaults.items():
        if k not in state:
            state[k] = v


def parse_system_command(text: str) -> Optional[Dict[str, Any]]:
    """从 Agent 回复中提取 JSON 指令；兼容 legacy COMMAND 行。"""
    if not text:
        return None
    m = _JSON_BLOCK_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    cmd: Dict[str, Any] = {}
    mp = _RE_CMD_MAP_PIPE.search(text)
    if mp:
        cmd["map"] = {"lat": float(mp.group(1)), "lon": float(mp.group(2)), "zoom": int(mp.group(3))}
    pp = _RE_CMD_PIPELINE.search(text)
    if pp:
        cmd.setdefault("sidebar_states", {})
        cmd["sidebar_states"]["selected_task"] = pp.group(1).strip()
        cmd["sidebar_states"]["prob_th"] = float(pp.group(2))
        cmd["sidebar_states"]["min_cnt"] = int(pp.group(3))
        cmd["pending_action"] = {"type": "run_pipeline", "task": pp.group(1).strip()}
    return cmd or None


def _strip_json_block(text: str) -> str:
    t = _JSON_BLOCK_RE.sub("", text)
    t = _RE_CMD_MAP_PIPE.sub("", t)
    t = _RE_CMD_PIPELINE.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def _coerce_workflow(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    return WORKFLOW_ALIASES.get(s.lower(), s if s in ("潮滩推理", "GEE 数据下载") else None)


def _coerce_run_mode(val: Any) -> Optional[str]:
    if val is None:
        return None
    return RUN_MODE_MAP.get(str(val).strip().lower(), None)


def _coerce_bool(val: Any) -> Optional[bool]:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("true", "1", "yes", "on", "开", "开启", "打开"):
        return True
    if s in ("false", "0", "no", "off", "关", "关闭"):
        return False
    return None


def _coerce_float(val: Any, lo: float, hi: float) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
        return float(min(hi, max(lo, f)))
    except (TypeError, ValueError):
        return None


def _coerce_int(val: Any, lo: int, hi: int) -> Optional[int]:
    if val is None:
        return None
    try:
        i = int(val)
        return int(min(hi, max(lo, i)))
    except (TypeError, ValueError):
        return None


def _coerce_date_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, date):
        return val.isoformat()
    s = str(val).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return None


def _apply_sidebar_delta(state: Dict[str, Any], sidebar: Dict[str, Any], result: ApplyResult) -> None:
    if not sidebar:
        return
    inference_mode_from_run = None
    rm = _coerce_run_mode(sidebar.get("run_mode"))
    if rm == "index":
        inference_mode_from_run = "指数法"
    elif rm == "dl":
        inference_mode_from_run = "深度学习"

    for json_key, ss_key in SIDEBAR_KEY_MAP.items():
        if json_key not in sidebar:
            continue
        raw = sidebar.get(json_key)
        if raw is None:
            continue

        if json_key == "workflow_tab":
            v = _coerce_workflow(raw)
            if v:
                state[ss_key] = v
                result.sidebar_keys_updated.append(ss_key)
            continue
        if json_key == "run_mode":
            if inference_mode_from_run:
                state["ui_inference_mode"] = inference_mode_from_run
                state["ui_run_mode"] = rm
                result.sidebar_keys_updated.extend(["ui_inference_mode", "ui_run_mode"])
            continue
        if json_key == "inference_mode":
            s = str(raw).strip()
            if s in ("深度学习", "指数法"):
                state[ss_key] = s
                state["ui_run_mode"] = "index" if s == "指数法" else "dl"
                result.sidebar_keys_updated.extend([ss_key, "ui_run_mode"])
            continue
        if json_key in ("adaptive_mode", "force_rerun", "m5_enabled", "e1_enabled", "e1_export_maps", "e1_export_heatmap"):
            b = _coerce_bool(raw)
            if b is not None:
                state[ss_key] = b
                result.sidebar_keys_updated.append(ss_key)
            continue
        if json_key == "prob_th":
            f = _coerce_float(raw, 0.01, 0.50)
            if f is not None:
                state[ss_key] = f
                result.sidebar_keys_updated.append(ss_key)
            continue
        if json_key in ("min_cnt", "m4_scale", "m4_cloud", "m4_min_pix"):
            bounds = {"min_cnt": (1, 10), "m4_scale": (10, 30), "m4_cloud": (0, 100), "m4_min_pix": (100, 500000)}
            lo, hi = bounds.get(json_key, (0, 10**9))
            i = _coerce_int(raw, lo, hi)
            if i is not None:
                state[ss_key] = i
                result.sidebar_keys_updated.append(ss_key)
            continue
        if json_key in ("m4_min_land", "m4_max_land"):
            f = _coerce_float(raw, 0.0, 100.0)
            if f is not None:
                state[ss_key] = f
                result.sidebar_keys_updated.append(ss_key)
            continue
        if json_key in ("m4_start_date", "m4_end_date"):
            ds = _coerce_date_str(raw)
            if ds:
                state[ss_key] = ds
                result.sidebar_keys_updated.append(ss_key)
            continue
        if json_key == "m4_bands":
            if isinstance(raw, list) and raw:
                state[ss_key] = [str(x) for x in raw]
                result.sidebar_keys_updated.append(ss_key)
            continue
        if json_key == "m4_export_to":
            s = str(raw).strip().lower()
            if s in ("drive", "local"):
                state[ss_key] = s
                result.sidebar_keys_updated.append(ss_key)
            continue
        if json_key == "e1_compare_sources":
            if isinstance(raw, list):
                state[ss_key] = [str(x) for x in raw]
                result.sidebar_keys_updated.append(ss_key)
            continue
        # 字符串路径类
        s = str(raw).strip().strip('"').strip("'")
        if s:
            if json_key.endswith("_dir") or json_key.endswith("_path") or json_key.endswith("_shp") or json_key in (
                "selected_task",
                "m4_roi_name",
                "m4_drive_folder",
                "m4_local_dir",
                "e1_reference",
                "m4_gee_proxy",
                "m4_gee_project",
            ):
                state[ss_key] = os.path.normpath(s) if ("/" in s or "\\" in s or ":" in s) and json_key != "selected_task" and json_key not in ("m4_roi_name", "e1_reference", "m4_gee_project") else s
                result.sidebar_keys_updated.append(ss_key)


def _snapshot_sidebar(state: Dict[str, Any]) -> Dict[str, Any]:
    keys = set(SIDEBAR_KEY_MAP.values()) | {"ui_selected_task"}
    return {k: state.get(k) for k in keys if k in state}


def build_agent_sidebar_context(state: Dict[str, Any]) -> str:
    """生成注入 Agent System Prompt 的侧栏快照（帮助理解「按侧栏默认」与省略参数）。"""
    init_ui_session_defaults(state)
    s = _snapshot_sidebar(state)

    def _fmt_date(v: Any) -> str:
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v) if v else "—"

    lines = [
        "【当前侧栏状态快照】",
        "用户说「按侧栏/默认/当前设置/别改参数」时：只改其明确提到的项，其余省略；",
        "用户说「跑一下/开始/启动」且未给 prob/cnt/task 时：优先用下方快照中的值。",
        f"- 工作台: {s.get('ui_workflow', '—')}",
        f"- 目标任务: {s.get('ui_selected_task') or '（未选）'}",
        f"- 原始影像目录: {s.get('ui_root_dir', '—')}",
        f"- 推理方式: {s.get('ui_inference_mode', '—')} (run_mode={'index' if s.get('ui_inference_mode') == '指数法' else 'dl'})",
        f"- AutoTune 开关: {s.get('ui_adaptive_mode', False)}",
        f"- 概率阈值 prob_th: {s.get('ui_prob_th', '—')}",
        f"- 频次阈值 min_cnt: {s.get('ui_min_cnt', '—')}",
        f"- 强制重跑 force_rerun: {s.get('ui_force_rerun', False)}",
        f"- M5: {'开' if s.get('ui_m5_enabled') else '关'} | 基线: {s.get('ui_m5_baseline_shp') or '自动'}",
        f"- E1: {'开' if s.get('ui_e1_enabled') else '关'} | 参考: {s.get('ui_e1_reference', '—')}",
        f"- M4 云量: {s.get('ui_m4_cloud_limit', '—')}% | 日期: {_fmt_date(s.get('ui_m4_start_date'))} ~ {_fmt_date(s.get('ui_m4_end_date'))}",
        f"- M4 ROI: {s.get('ui_m4_roi_name') or '—'} | 导出: {s.get('ui_m4_export_to', '—')}",
        f"- 地图中心: {state.get('map_center', '—')} zoom={state.get('map_zoom', '—')}",
        f"- 任务运行中: {bool(state.get('is_running'))}",
    ]
    return "\n".join(lines)

def build_pending_task(state: Dict[str, Any], action: Dict[str, Any]) -> Tuple[Optional[Dict], Optional[Dict], List[str]]:
    """
    返回 (pending_task, pending_autotune, errors)
    schema 与侧栏按钮完全一致。
    """
    errors: List[str] = []
    atype = str(action.get("type") or "").strip().lower()
    task = (action.get("task") or state.get("ui_selected_task") or "").strip() or None

    if atype == "run_autotune":
        ap = action.get("autotune_params") or {}
        ref = ap.get("reference_id") or action.get("reference_id") or action.get("reference") or state.get("ui_autotune_reference_id")
        obj = ap.get("objective") or action.get("objective") or "iou_f1"
        obj = AUTOTUNE_OBJECTIVE_MAP.get(str(obj).strip().lower(), str(obj))
        if not task:
            errors.append("AutoTune 需要指定目标任务 task。")
            return None, None, errors
        if not ref:
            errors.append("AutoTune 需要 reference_id（参考真值数据集 id）。")
            return None, None, errors
        aoi = state.get("ui_task_aoi_shp") or ""
        aoi_use = aoi if aoi and os.path.isfile(aoi) else None
        return None, {
            "task": task,
            "reference_id": ref,
            "objective": obj,
            "task_aoi_shp": aoi_use,
        }, errors

    if atype == "run_m4":
        m4p = action.get("m4_params") or {}
        roi_path = m4p.get("roi_path") or state.get("ui_m4_roi_path")
        roi_name = m4p.get("roi_name") or state.get("ui_m4_roi_name") or task or "zhejiang1"
        start = _coerce_date_str(m4p.get("start_date")) or state.get("ui_m4_start_date") or "2020-01-01"
        end = _coerce_date_str(m4p.get("end_date")) or state.get("ui_m4_end_date") or "2020-01-31"
        export_to = m4p.get("export_to") or state.get("ui_m4_export_to") or "drive"
        drive_folder = m4p.get("drive_folder") or state.get("ui_m4_drive_folder") or (task or "GEE_Downloads")
        root = state.get("ui_root_dir") or r"I:\GEE_data\20"
        local_dir = m4p.get("local_out_dir") or state.get("ui_m4_local_dir") or os.path.join(root, drive_folder)
        bands = m4p.get("bands") or state.get("ui_m4_bands") or ["B8", "B4", "B3", "B2", "B11"]
        return {
            "task": task,
            "mode": "m4",
            "m4": {
                "roi_path": str(roi_path).strip(),
                "roi_name": str(roi_name).strip(),
                "start_date": start,
                "end_date": end,
                "export_to": export_to,
                "local_out_dir": os.path.normpath(str(local_dir).strip()),
                "drive_folder": str(drive_folder).strip(),
                "bands": list(bands),
                "cloud_limit": int(m4p.get("cloud_limit") or state.get("ui_m4_cloud_limit") or 60),
                "min_land_pct": float(m4p.get("min_land_pct") or state.get("ui_m4_min_land") or 5.0),
                "max_land_pct": float(m4p.get("max_land_pct") or state.get("ui_m4_max_land") or 95.0),
                "min_pixel_count": int(m4p.get("min_pixel_count") or state.get("ui_m4_min_pixel_count") or 1000),
                "scale": int(m4p.get("scale") or state.get("ui_m4_scale") or 10),
                "gee_proxy_url": str(m4p.get("gee_proxy_url") or state.get("ui_m4_gee_proxy") or "").strip(),
                "gee_project_id": str(m4p.get("gee_project_id") or state.get("ui_m4_gee_project") or "").strip(),
            },
        }, None, errors

    if atype in ("run_pipeline", "run", ""):
        run_mode = state.get("ui_run_mode") or "dl"
        if state.get("ui_inference_mode") == "指数法":
            run_mode = "index"
        prob = float(action.get("prob_th") or state.get("ui_prob_th") or 0.05)
        cnt = int(action.get("min_cnt") or state.get("ui_min_cnt") or 2)
        if not task:
            errors.append("运行推理需要指定 task（目标任务名）。")
            return None, None, errors
        pts = state.get("ui_points_shp") if run_mode == "index" else None
        return {
            "task": task,
            "prob": prob,
            "cnt": cnt,
            "mode": "index" if run_mode == "index" else "dl",
            "points_shp": (pts or "").strip() if pts else None,
            "force_rerun": bool(state.get("ui_force_rerun", False)),
        }, None, errors

    errors.append(f"未知 pending_action.type: {atype}")
    return None, None, errors


def apply_system_command(state: Dict[str, Any], command: Dict[str, Any]) -> ApplyResult:
    """差量合流：仅更新 JSON 中非 null 字段；可选触发 pending 动作。"""
    result = ApplyResult(applied=True)
    init_ui_session_defaults(state)

    mp = command.get("map")
    if isinstance(mp, dict):
        lat = mp.get("lat")
        lon = mp.get("lon")
        zoom = mp.get("zoom", 8)
        if lat is not None and lon is not None:
            try:
                state["map_center"] = [float(lat), float(lon)]
                state["map_zoom"] = int(zoom)
                state["_map_view_synced_for"] = None
                result.map_updated = True
            except (TypeError, ValueError) as e:
                result.errors.append(f"map 参数无效: {e}")

    sb = command.get("sidebar_states")
    if isinstance(sb, dict):
        _apply_sidebar_delta(state, sb, result)

    action = command.get("pending_action")
    if isinstance(action, dict) and action.get("type"):
        result.action_type = str(action.get("type"))
        pt, at, errs = build_pending_task(state, action)
        result.errors.extend(errs)
        if pt and not errs:
            state["pending_task"] = pt
            state["is_running"] = True
            state["stop_requested"] = False
            state.pop("pending_autotune", None)
        elif at and not errs:
            state["pending_autotune"] = at
            state["is_running"] = True
            state["stop_requested"] = False
            state.pop("pending_task", None)

    return result


def queue_agent_command(state: Dict[str, Any], command: Dict[str, Any]) -> None:
    """将指令入队，待下一轮 rerun 在侧栏 widget 实例化之前合流（避免 Streamlit key 冲突）。"""
    if not command:
        return
    pending = state.get(PENDING_AGENT_COMMANDS_KEY)
    if not isinstance(pending, list):
        pending = []
    pending.append(command)
    state[PENDING_AGENT_COMMANDS_KEY] = pending


def flush_pending_agent_commands(state: Dict[str, Any]) -> ApplyResult:
    """
    在 app.py 侧栏渲染前调用：执行队列中全部 Agent 指令。
    Streamlit 禁止在带 key 的 widget 实例化后修改 st.session_state[key]。
    """
    pending = state.pop(PENDING_AGENT_COMMANDS_KEY, None)
    if not pending:
        return ApplyResult(applied=False)

    merged = ApplyResult(applied=True)
    for cmd in pending:
        if not isinstance(cmd, dict):
            continue
        one = apply_system_command(state, cmd)
        merged.map_updated = merged.map_updated or one.map_updated
        merged.sidebar_keys_updated.extend(one.sidebar_keys_updated)
        merged.errors.extend(one.errors)
        if one.action_type:
            merged.action_type = one.action_type
    return merged


def _preview_apply_result(command: Dict[str, Any]) -> ApplyResult:
    """不入队、不写 state，仅用于聊天区展示 action_type。"""
    result = ApplyResult(applied=True, queued=True)
    action = command.get("pending_action")
    if isinstance(action, dict) and action.get("type"):
        result.action_type = str(action.get("type"))
    if command.get("map"):
        result.map_updated = True
    if command.get("sidebar_states"):
        result.sidebar_keys_updated = list(command.get("sidebar_states") or {})
    return result


def process_agent_reply(state: Dict[str, Any], reply: str) -> Tuple[ApplyResult, str]:
    """解析 Agent 回复并入队（不在本轮修改 ui_* widget 键）。"""
    cmd = parse_system_command(reply)
    clean = _strip_json_block(reply)
    if not cmd:
        return ApplyResult(applied=False), reply
    queue_agent_command(state, cmd)
    result = _preview_apply_result(cmd)
    result.clean_reply_hint = clean
    return result, clean or reply


def apply_agent_reply_immediate(state: Dict[str, Any], reply: str) -> Tuple[ApplyResult, str]:
    """测试/非 Streamlit 环境：直接合流，不经过队列。"""
    cmd = parse_system_command(reply)
    clean = _strip_json_block(reply)
    if not cmd:
        return ApplyResult(applied=False), reply
    result = apply_system_command(state, cmd)
    result.clean_reply_hint = clean
    return result, clean or reply
