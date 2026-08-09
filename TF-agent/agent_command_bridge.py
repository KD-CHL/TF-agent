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

# 重型工具确认门闩：未确认时仅记录待确认请求，不写入 pending_task/pending_autotune。
HEAVY_ACTION_LABELS = {
    "run_pipeline": "推理任务（深度学习/指数法推理）",
    "run": "推理任务（深度学习/指数法推理）",
    "": "推理任务（深度学习/指数法推理）",
    "run_inference": "本地潮滩推理（可信执行闭环）",
    "run_m4": "GEE 影像下载",
    "run_gee_download": "GEE 影像下载（可信执行闭环）",
    "run_autotune": "AutoTune 阈值搜索",
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
    m5_plan: Optional[Dict[str, Any]] = None
    m5_plan_text: str = ""
    e1_plan: Optional[Dict[str, Any]] = None
    e1_plan_text: str = ""
    inference_plan: Optional[Dict[str, Any]] = None
    inference_plan_text: str = ""
    gee_plan: Optional[Dict[str, Any]] = None
    gee_plan_text: str = ""


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
    """从 Agent 回复中提取 JSON 指令；兼容 legacy COMMAND 行与自然语言坐标。"""
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
    if "map" not in cmd:
        nl = _parse_natural_map_command(text)
        if nl:
            cmd["map"] = {"lat": nl[0], "lon": nl[1], "zoom": nl[2]}
    return cmd or None


_RE_MAP_COORDS_NSEW = re.compile(
    r"([-\d.]+)\s*[°º]?\s*[Nn北]\s*[,，/]\s*([-\d.]+)\s*[°º]?\s*[Ee东]",
)
_RE_MAP_COORDS_PLAIN = re.compile(
    r"(?:中心点|中心|坐标|定位(?:至|到)?|跳转(?:至|到)?|视角)\s*"
    r"[（(]?\s*([-\d.]+)\s*[,，]\s*([-\d.]+)\s*[)）]?",
)
_RE_MAP_ZOOM = re.compile(
    r"(?:缩放(?:级别|等级)?|zoom)\s*(?:为|到|=|：|:)?\s*(\d{1,2})",
    re.IGNORECASE,
)
_RE_MAP_INTENT = re.compile(
    r"(已定位|已跳转|已将地图|地图视角|视角已|飞到|定位到|跳转到|挪到|中心点)",
)


def _parse_natural_map_command(text: str) -> Optional[Tuple[float, float, int]]:
    """从自然语言回复中提取 lat/lon/zoom（仅定位语境）。"""
    if not text or not _RE_MAP_INTENT.search(text):
        return None
    flat = re.sub(r"[`\*_]+", " ", text)
    flat = re.sub(r"[\n\r]+", " ", flat)
    lat = lon = None
    m = _RE_MAP_COORDS_NSEW.search(flat)
    if m:
        try:
            lat, lon = float(m.group(1)), float(m.group(2))
        except (ValueError, TypeError):
            lat = lon = None
    if lat is None:
        m = _RE_MAP_COORDS_PLAIN.search(flat)
        if m:
            try:
                lat, lon = float(m.group(1)), float(m.group(2))
            except (ValueError, TypeError):
                lat = lon = None
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    zoom = 9
    mz = _RE_MAP_ZOOM.search(flat)
    if mz:
        try:
            zoom = max(1, min(18, int(mz.group(1))))
        except (ValueError, TypeError):
            zoom = 9
    return lat, lon, zoom


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


def _pick_float(val: Any, lo: float, hi: float, fallback: float) -> float:
    """安全解析浮点：非法/空值回退 fallback，越界 clamp（与侧栏 _coerce_float 一致）。"""
    r = _coerce_float(val, lo, hi)
    return fallback if r is None else r


def _pick_int(val: Any, lo: int, hi: int, fallback: int) -> int:
    """安全解析整数：非法/空值回退 fallback，越界 clamp（与侧栏 _coerce_int 一致）。"""
    r = _coerce_int(val, lo, hi)
    return fallback if r is None else r


def _apply_sidebar_delta(state: Dict[str, Any], sidebar: Dict[str, Any], result: ApplyResult) -> None:
    if not sidebar:
        return
    # 字段统一：规范字段为 workflow_tab；兼容旧 Prompt 输出的 workspace_tab。
    # 两者同时出现时规范字段优先，旧字段忽略（不允许静默失效）。
    if "workspace_tab" in sidebar:
        sidebar = dict(sidebar)
        if "workflow_tab" not in sidebar:
            sidebar["workflow_tab"] = sidebar.pop("workspace_tab")
        else:
            sidebar.pop("workspace_tab")
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
        # 字符串路径类：仅文件系统路径做 normpath；
        # URL/代理地址/公网地址（m4_gee_proxy、m4_gee_project）与名称类绝不做 normpath，
        # 否则 http://127.0.0.1:7890 会被 Windows normpath 破坏为 http:\\127.0.0.1:7890。
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
                is_url_like = json_key in ("m4_gee_proxy", "m4_gee_project") or s.lower().startswith(
                    ("http://", "https://")
                )
                is_name_like = json_key in ("selected_task", "m4_roi_name", "e1_reference")
                needs_norm = (
                    not is_url_like
                    and not is_name_like
                    and ("/" in s or "\\" in s or ":" in s)
                )
                state[ss_key] = os.path.normpath(s) if needs_norm else s
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
    try:
        import m5_agent_loop

        lines.append(
            m5_agent_loop.build_m5_context_for_agent(
                final_root=str(s.get("ui_final_root") or ""),
                current_task=str(s.get("ui_selected_task") or ""),
                pending_plan=state.get("_m5_pending_plan")
                if isinstance(state.get("_m5_pending_plan"), dict)
                else None,
            )
        )
    except Exception:
        pass
    try:
        import e1_agent_loop

        lines.append(
            e1_agent_loop.build_e1_context_for_agent(
                final_root=str(s.get("ui_final_root") or ""),
                current_task=str(s.get("ui_selected_task") or ""),
                data_root=str(s.get("ui_e1_data_root") or ""),
                reference=str(s.get("ui_e1_reference") or "师姐_2020"),
                pending_plan=state.get("_e1_pending_plan")
                if isinstance(state.get("_e1_pending_plan"), dict)
                else None,
            )
        )
    except Exception:
        pass
    try:
        import inference_agent_loop

        lines.append(
            inference_agent_loop.build_inference_context_for_agent(
                root_dir=str(s.get("ui_root_dir") or ""),
                task_options=None,
                model_path=str(s.get("ui_model_path") or ""),
                prob_threshold=s.get("ui_prob_th"),
                count_threshold=s.get("ui_min_cnt"),
                device="",
                pending_plan=state.get("_inference_pending_plan")
                if isinstance(state.get("_inference_pending_plan"), dict)
                else None,
            )
        )
    except Exception:
        pass
    return "\n".join(lines)


def propose_e1_plan(state: Dict[str, Any], action: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """根据磁盘资产与侧栏生成 E1 计划并写入 state['_e1_pending_plan']。"""
    import e1_agent_loop

    action = action or {}
    init_ui_session_defaults(state)
    task = (
        (action.get("task") or state.get("ui_selected_task") or "")
        .strip()
        or None
    )
    if task:
        state["ui_selected_task"] = task
    prob = action.get("prob_th")
    if prob is None:
        prob = state.get("ui_prob_th")
    cnt = action.get("min_cnt")
    if cnt is None:
        cnt = state.get("ui_min_cnt")
    try:
        prob_f = float(prob) if prob is not None else None
    except (TypeError, ValueError):
        prob_f = None
    try:
        cnt_i = int(cnt) if cnt is not None else None
    except (TypeError, ValueError):
        cnt_i = None

    reference = (
        action.get("reference")
        or action.get("e1_reference")
        or state.get("ui_e1_reference")
        or "师姐_2020"
    )
    data_root = (
        action.get("data_root")
        or action.get("e1_data_root")
        or state.get("ui_e1_data_root")
        or ""
    )
    compare = action.get("compare_sources") or state.get("ui_e1_compare_sources")
    if compare is not None and not isinstance(compare, list):
        compare = None

    plan = e1_agent_loop.build_e1_preflight(
        final_root=str(state.get("ui_final_root") or ""),
        current_task=str(task or ""),
        data_root=str(data_root or ""),
        reference=str(reference),
        compare_sources=compare,
        prob=prob_f,
        cnt=cnt_i,
        task_aoi_shp=str(state.get("ui_task_aoi_shp") or "") or None,
        export_disagreement_maps=bool(
            action.get("export_disagreement_maps")
            if action.get("export_disagreement_maps") is not None
            else state.get("ui_e1_export_maps", True)
        ),
        export_multi_product_heatmap=bool(
            action.get("export_multi_product_heatmap")
            if action.get("export_multi_product_heatmap") is not None
            else state.get("ui_e1_export_heatmap", True)
        ),
    )
    state["_e1_pending_plan"] = plan
    state["_e1_plan_confirmed"] = False
    errors = list(plan.get("blockers") or []) if not plan.get("ready") else []
    return plan, errors


def propose_m5_plan(state: Dict[str, Any], action: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """根据磁盘资产账本生成 M5 计划并写入 state['_m5_pending_plan']。"""
    import m5_agent_loop

    action = action or {}
    init_ui_session_defaults(state)
    task = (
        (action.get("task") or state.get("ui_selected_task") or "")
        .strip()
        or None
    )
    if task:
        state["ui_selected_task"] = task
    prob = action.get("prob_th")
    if prob is None:
        prob = state.get("ui_prob_th")
    cnt = action.get("min_cnt")
    if cnt is None:
        cnt = state.get("ui_min_cnt")
    try:
        prob_f = float(prob) if prob is not None else None
    except (TypeError, ValueError):
        prob_f = None
    try:
        cnt_i = int(cnt) if cnt is not None else None
    except (TypeError, ValueError):
        cnt_i = None

    baseline_shp = (
        action.get("baseline_shp")
        or state.get("ui_m5_baseline_shp")
        or ""
    ).strip() or None
    baseline_task = (action.get("baseline_task") or "").strip() or None

    plan = m5_agent_loop.build_m5_preflight(
        final_root=str(state.get("ui_final_root") or ""),
        current_task=str(task or ""),
        task_options=None,
        prob=prob_f,
        cnt=cnt_i,
        baseline_task=baseline_task,
        baseline_shp_override=baseline_shp,
    )
    state["_m5_pending_plan"] = plan
    state["_m5_plan_confirmed"] = False
    errors = list(plan.get("blockers") or []) if not plan.get("ready") else []
    return plan, errors

def propose_inference_plan(state: Dict[str, Any], action: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """根据侧栏合法配置与输入目录生成本地潮滩推理计划，写入 state['_inference_pending_plan']。

    路径一律来自侧栏合法值；task 必须存在于 root_dir 子目录（build_inference_plan 校验）。
    """
    import inference_agent_loop

    action = action or {}
    init_ui_session_defaults(state)
    task = (
        (action.get("task") or state.get("ui_selected_task") or "")
        .strip()
        or None
    )
    if task:
        state["ui_selected_task"] = task
    prob = action.get("prob_th")
    if prob is None:
        prob = state.get("ui_prob_th")
    cnt = action.get("min_cnt")
    if cnt is None:
        cnt = state.get("ui_min_cnt")
    try:
        prob_f = float(prob) if prob is not None else None
    except (TypeError, ValueError):
        prob_f = None
    try:
        cnt_i = int(cnt) if cnt is not None else None
    except (TypeError, ValueError):
        cnt_i = None

    plan = inference_agent_loop.build_inference_plan(
        task_id=str(task or ""),
        root_dir=str(state.get("ui_root_dir") or ""),
        final_root=str(state.get("ui_final_root") or ""),
        mask_root=str(state.get("ui_mask_root") or ""),
        model_path=str(state.get("ui_model_path") or ""),
        prob_threshold=prob_f if prob_f is not None else 0.05,
        count_threshold=cnt_i if cnt_i is not None else 2,
        input_asset_id=action.get("input_asset_id"),
        weight_id=action.get("weight_id"),
        device_policy=str(action.get("device_policy") or "auto"),
        shp_path=str(state.get("ui_shp_path") or ""),
    )
    state["_inference_pending_plan"] = plan
    state["_inference_plan_confirmed"] = set()
    errors = list(plan.get("blockers") or []) if not plan.get("ready") else []
    return plan, errors


def confirm_inference_plan(state: Dict[str, Any], plan_id: str) -> Tuple[bool, Optional[str]]:
    """与 UI 确认按钮共用的推理计划确认门闩（委托 inference_agent_loop）。"""
    import inference_agent_loop

    return inference_agent_loop.confirm_inference_plan(state, plan_id)


def is_inference_plan_confirmed(state: Dict[str, Any], plan_id: Optional[str]) -> bool:
    import inference_agent_loop

    return inference_agent_loop.is_plan_confirmed(state, plan_id)


def propose_gee_plan(state: Dict[str, Any], action: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """根据侧栏 GEE 配置与当前 AOI 生成 GEE 下载执行计划，写入 state['_gee_pending_plan']。

    - AOI：优先 action.aoi / action.aoi_id / state['_active_aoi']（地图 AOI）；
      否则用侧栏 ui_m4_roi_path + ui_m4_roi_name 仅做轻量 bbox 提取（无真实面）。
    - bands 默认 ["B4","B3","B2"]（B3 铁律），来自 action.bands 或侧栏。
    - 任何参数修改 → 重新 build → 新 plan_id（旧确认集同步重置）。
    """
    import gee_agent_loop
    from aoi_context import aoi_from_bbox, validate_aoi, compact_summary

    action = action or {}
    init_ui_session_defaults(state)
    task = (action.get("task") or action.get("roi_name")
            or state.get("ui_m4_roi_name") or state.get("ui_selected_task")
            or "").strip() or None
    if task:
        state["ui_m4_roi_name"] = task

    # ---- AOI 解析 ----
    aoi_dict: Dict[str, Any] = {}
    aoi_warnings: List[str] = []
    aoi_src = action.get("aoi")
    if isinstance(aoi_src, dict) and aoi_src.get("type") == "Polygon":
        try:
            ctx = validate_aoi(aoi_src, source=action.get("aoi_source") or "map_polygon",
                               label=task or None)
            aoi_dict = ctx.to_dict()
            aoi_warnings = list(ctx.warnings or [])
        except Exception as e:  # noqa: BLE001
            aoi_warnings.append(f"AOI 解析失败: {e}")
    elif action.get("aoi_id"):
        aoi_dict = {"aoi_id": str(action["aoi_id"]), "valid": True}
    elif state.get("_active_aoi"):
        aoi_dict = dict(state["_active_aoi"])
    elif state.get("ui_m4_roi_path"):
        # 矢量文件：仅提取 bbox（轻量，不加载全量几何进 LLM）
        try:
            import geopandas as gpd
            gdf = gpd.read_file(state["ui_m4_roi_path"])
            if not gdf.empty:
                b = gdf.total_bounds
                ctx = aoi_from_bbox(float(b[0]), float(b[1]), float(b[2]), float(b[3]),
                                    source="shapefile_bbox", label=task or None)
                aoi_dict = ctx.to_dict()
                aoi_warnings.append("AOI 来自矢量文件外接矩形（bbox），非精确面。")
        except Exception as e:  # noqa: BLE001
            aoi_warnings.append(f"读取 AOI 矢量失败: {e}")
    if not aoi_dict or not aoi_dict.get("valid"):
        aoi_warnings.append("未解析到有效 AOI（请先在三维地图绘制 AOI 或配置 ROI 矢量）。")

    # B3：默认波段 B4/B3/B2（RGB 顺序，匹配 pre_engine）。
    # 侧栏 ui_m4_bands 沿用旧 M4 工作流的 5 波段默认值，视为“未显式选择”，
    # 仅当 action 显式传 bands 或侧栏值确实非默认时才覆盖。
    _LEGACY_M4_BANDS = ["B8", "B4", "B3", "B2", "B11"]
    _sidebar_bands = state.get("ui_m4_bands")
    if action.get("bands"):
        bands = list(action["bands"])
    elif isinstance(_sidebar_bands, list) and _sidebar_bands and \
            [str(b) for b in _sidebar_bands] != _LEGACY_M4_BANDS:
        bands = [str(b) for b in _sidebar_bands]
    else:
        bands = list(gee_agent_loop.DEFAULT_BANDS)
    index_bands = action.get("index_bands") or gee_agent_loop.DEFAULT_INDEX_BANDS
    try:
        cloud = int(action.get("cloud_limit") if action.get("cloud_limit") is not None
                    else state.get("ui_m4_cloud_limit", 60))
    except (TypeError, ValueError):
        cloud = 60
    try:
        scale = int(action.get("scale") if action.get("scale") is not None
                    else state.get("ui_m4_scale", 10))
    except (TypeError, ValueError):
        scale = 10
    export_to = str(action.get("export_to") or state.get("ui_m4_export_to") or "local").lower()
    local_dir = str(action.get("local_out_dir")
                    or state.get("ui_m4_local_dir")
                    or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "gee_downloads"))
    drive_folder = str(action.get("drive_folder")
                       or state.get("ui_m4_drive_folder") or "GEE_Downloads")

    plan = gee_agent_loop.build_gee_download_plan(
        task_id=str(task or ""),
        aoi=aoi_dict,
        start_date=str(action.get("start_date") or state.get("ui_m4_start_date") or ""),
        end_date=str(action.get("end_date") or state.get("ui_m4_end_date") or ""),
        collection=str(action.get("collection") or "COPERNICUS/S2_SR_HARMONIZED"),
        bands=bands,
        index_bands=index_bands,
        cloud_limit=cloud,
        min_land_pct=float(action.get("min_land_pct") if action.get("min_land_pct") is not None
                           else state.get("ui_m4_min_land", 5.0)),
        max_land_pct=float(action.get("max_land_pct") if action.get("max_land_pct") is not None
                           else state.get("ui_m4_max_land", 95.0)),
        min_pixel_count=int(action.get("min_pixel_count") if action.get("min_pixel_count") is not None
                            else state.get("ui_m4_min_pixel_count", 1000)),
        scale=scale,
        export_to=export_to,
        drive_folder=drive_folder,
        local_out_dir=local_dir,
        gee_proxy_url=str(action.get("gee_proxy_url") or state.get("ui_m4_gee_proxy") or ""),
        gee_project_id=str(action.get("gee_project_id") or state.get("ui_m4_gee_project") or ""),
    )
    for w in aoi_warnings:
        if w not in plan["warnings"]:
            plan["warnings"].append(w)
    state["_gee_pending_plan"] = plan
    state["_gee_plan_confirmed"] = set()
    errors = list(plan.get("blockers") or []) if not plan.get("ready") else []
    return plan, errors


def confirm_gee_plan(state: Dict[str, Any], plan_id: str) -> Tuple[bool, Optional[str]]:
    """与 UI 确认按钮共用的 GEE 下载计划确认门闩（委托 gee_agent_loop）。"""
    import gee_agent_loop

    return gee_agent_loop.confirm_gee_download_plan(state, plan_id)


def is_gee_plan_confirmed(state: Dict[str, Any], plan_id: Optional[str]) -> bool:
    import gee_agent_loop

    return gee_agent_loop.is_gee_plan_confirmed(state, plan_id)


def build_pending_task(state: Dict[str, Any], action: Dict[str, Any]) -> Tuple[Optional[Dict], Optional[Dict], List[str]]:
    """
    返回 (pending_task, pending_autotune, errors)
    schema 与侧栏按钮完全一致。
    """
    errors: List[str] = []
    atype = str(action.get("type") or "").strip().lower()
    task = (action.get("task") or state.get("ui_selected_task") or "").strip() or None

    if atype == "run_autotune":
        confirmed = bool(action.get("confirmed")) or bool(state.get("_autotune_plan_confirmed"))
        if not confirmed:
            errors.append("AutoTune 需用户确认后才能执行（confirmed=true）。")
            return None, None, errors
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
        confirmed = bool(action.get("confirmed")) or bool(state.get("_m4_plan_confirmed"))
        if not confirmed:
            errors.append("M4/GEE 下载需用户确认后才能执行（confirmed=true）。")
            return None, None, errors
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
        # bands 可能是逗号分隔字符串（模型输出），统一为列表；空列表回退默认。
        if isinstance(bands, str):
            bands = [b.strip() for b in bands.split(",") if b.strip()]
        else:
            bands = list(bands)
        if not bands:
            bands = ["B8", "B4", "B3", "B2", "B11"]
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
                "bands": bands,
                "cloud_limit": _pick_int(m4p.get("cloud_limit"), 0, 100, state.get("ui_m4_cloud_limit") or 60),
                "min_land_pct": _pick_float(m4p.get("min_land_pct"), 0.0, 100.0, state.get("ui_m4_min_land") or 5.0),
                "max_land_pct": _pick_float(m4p.get("max_land_pct"), 0.0, 100.0, state.get("ui_m4_max_land") or 95.0),
                "min_pixel_count": _pick_int(m4p.get("min_pixel_count"), 100, 500000, state.get("ui_m4_min_pixel_count") or 1000),
                "scale": _pick_int(m4p.get("scale"), 10, 30, state.get("ui_m4_scale") or 10),
                "gee_proxy_url": str(m4p.get("gee_proxy_url") or state.get("ui_m4_gee_proxy") or "").strip(),
                "gee_project_id": str(m4p.get("gee_project_id") or state.get("ui_m4_gee_project") or "").strip(),
            },
        }, None, errors

    if atype == "run_e1":
        confirmed = bool(action.get("confirmed")) or bool(state.get("_e1_plan_confirmed"))
        if not confirmed:
            errors.append("E1 需用户确认后才能执行（confirmed=true 或侧栏确认）。")
            return None, None, errors
        plan = state.get("_e1_pending_plan")
        if not isinstance(plan, dict) or not plan.get("ready"):
            plan, plan_errs = propose_e1_plan(state, action)
            errors.extend(plan_errs)
        if not isinstance(plan, dict) or not plan.get("ready"):
            errors.append("E1 执行条件未满足，无法启动。")
            return None, None, errors
        if task and task != plan.get("current_task"):
            action = dict(action)
            action["task"] = task
            plan, plan_errs = propose_e1_plan(state, action)
            errors.extend(plan_errs)
            if not plan.get("ready"):
                errors.append("指定任务的 E1 条件未满足。")
                return None, None, errors
        state["_e1_plan_confirmed"] = True
        return {
            "task": plan["current_task"],
            "mode": "e1",
            "prob": plan.get("prob"),
            "cnt": plan.get("cnt"),
            "e1": {
                "target_shp": plan["current_shp"],
                "data_root": plan["data_root"],
                "reference": plan.get("reference"),
                "compare_sources": plan.get("compare_sources"),
                "workspace_dir": plan.get("workspace_dir"),
                "task_aoi_shp": plan.get("task_aoi_shp"),
                "export_disagreement_maps": plan.get("export_disagreement_maps", True),
                "export_multi_product_heatmap": plan.get("export_multi_product_heatmap", True),
                "plan": plan,
            },
        }, None, errors

    if atype == "run_m5":
        confirmed = bool(action.get("confirmed")) or bool(state.get("_m5_plan_confirmed"))
        if not confirmed:
            errors.append("M5 需用户确认后才能执行（confirmed=true 或侧栏确认）。")
            return None, None, errors
        plan = state.get("_m5_pending_plan")
        if not isinstance(plan, dict) or not plan.get("ready"):
            plan, plan_errs = propose_m5_plan(state, action)
            errors.extend(plan_errs)
        if not isinstance(plan, dict) or not plan.get("ready"):
            errors.append("M5 执行条件未满足，无法启动。")
            return None, None, errors
        if task and task != plan.get("current_task"):
            # 用户指定了不同任务：重建计划
            action = dict(action)
            action["task"] = task
            plan, plan_errs = propose_m5_plan(state, action)
            errors.extend(plan_errs)
            if not plan.get("ready"):
                errors.append("指定任务的 M5 条件未满足。")
                return None, None, errors
        state["_m5_plan_confirmed"] = True
        return {
            "task": plan["current_task"],
            "mode": "m5",
            "prob": plan.get("prob"),
            "cnt": plan.get("cnt"),
            "m5": {
                "current_shp": plan["current_shp"],
                "baseline_shp": plan["baseline_shp"],
                "baseline_task": plan.get("baseline_task"),
                "plan": plan,
            },
        }, None, errors

    if atype == "run_inference":
        pid = action.get("plan_id") or (state.get("_inference_pending_plan") or {}).get("plan_id")
        confirmed = bool(action.get("confirmed")) or is_inference_plan_confirmed(state, pid)
        if not confirmed:
            errors.append("本地潮滩推理需用户确认后才能执行（confirmed=true 或侧栏确认）。")
            return None, None, errors
        plan = state.get("_inference_pending_plan")
        if not isinstance(plan, dict) or not plan.get("ready"):
            plan, plan_errs = propose_inference_plan(state, action)
            errors.extend(plan_errs)
        if not isinstance(plan, dict) or not plan.get("ready"):
            errors.append("推理执行条件未满足，无法启动。")
            return None, None, errors
        if pid and str(pid) != str(plan.get("plan_id")):
            errors.append(f"确认的 plan_id 与当前计划不一致（{pid} != {plan.get('plan_id')}）。")
            return None, None, errors
        # 与 UI 确认按钮同一逻辑（confirm_inference_plan），确保幂等集已写入
        if not is_inference_plan_confirmed(state, str(plan.get("plan_id"))):
            ok, cerr = confirm_inference_plan(state, str(plan.get("plan_id")))
            if not ok:
                errors.append(cerr or "推理计划确认失败。")
                return None, None, errors
        return {
            "task": plan["task_id"],
            "prob": plan.get("prob_threshold"),
            "cnt": plan.get("count_threshold"),
            "mode": "dl",
            "plan_id": plan["plan_id"],
            "inference_plan": plan,
            "points_shp": None,
            "force_rerun": False,
        }, None, errors

    if atype == "run_gee_download":
        pid = action.get("plan_id") or (state.get("_gee_pending_plan") or {}).get("plan_id")
        confirmed = bool(action.get("confirmed")) or is_gee_plan_confirmed(state, pid)
        if not confirmed:
            errors.append("GEE 影像下载需用户确认后才能执行（confirmed=true 或侧栏确认）。")
            return None, None, errors
        plan = state.get("_gee_pending_plan")
        if not isinstance(plan, dict) or not plan.get("ready"):
            plan, plan_errs = propose_gee_plan(state, action)
            errors.extend(plan_errs)
        if not isinstance(plan, dict) or not plan.get("ready"):
            errors.append("GEE 下载执行条件未满足，无法启动。")
            return None, None, errors
        if pid and str(pid) != str(plan.get("plan_id")):
            errors.append(f"确认的 plan_id 与当前计划不一致（{pid} != {plan.get('plan_id')}）。")
            return None, None, errors
        # 与 UI 确认按钮同一逻辑（confirm_gee_plan），确保幂等集已写入
        if not is_gee_plan_confirmed(state, str(plan.get("plan_id"))):
            ok, cerr = confirm_gee_plan(state, str(plan.get("plan_id")))
            if not ok:
                errors.append(cerr or "GEE 下载计划确认失败。")
                return None, None, errors
        return {
            "task": plan["task_id"],
            "mode": "gee",
            "plan_id": plan["plan_id"],
            "gee_plan": plan,
            "export_to": plan.get("export_to"),
        }, None, errors

    if atype in ("run_pipeline", "run", ""):
        confirmed = bool(action.get("confirmed")) or bool(state.get("_pipeline_plan_confirmed"))
        if not confirmed:
            errors.append("推理任务需用户确认后才能执行（confirmed=true）。")
            return None, None, errors
        run_mode = state.get("ui_run_mode") or "dl"
        if state.get("ui_inference_mode") == "指数法":
            run_mode = "index"
        prob = _pick_float(action.get("prob_th"), 0.01, 0.50, state.get("ui_prob_th") or 0.05)
        cnt = _pick_int(action.get("min_cnt"), 1, 10, state.get("ui_min_cnt") or 2)
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
                # 强制按 center 飞；优先 postMessage 到已有 iframe，避免重建 Cesium Viewer
                state["_map_prefer_center"] = True
                fly = {
                    "lat": float(lat),
                    "lon": float(lon),
                    "zoom": int(zoom),
                    "source": "agent",
                }
                preset = mp.get("preset")
                if preset:
                    fly["preset"] = str(preset)
                label = mp.get("label")
                if label:
                    fly["label"] = str(label)
                for k in ("height", "duration", "pitch", "heading"):
                    v = mp.get(k)
                    if v is not None:
                        try:
                            fly[k] = float(v)
                        except (TypeError, ValueError):
                            result.errors.append(f"map.{k} 参数无效: {v!r}")
                state["_pending_camera_fly"] = fly
                # 不递增 _globe_rev / 不清 iframe 缓存：仅相机变化时复用已加载地球页
                result.map_updated = True
            except (TypeError, ValueError) as e:
                result.errors.append(f"map 参数无效: {e}")

    sb = command.get("sidebar_states")
    if isinstance(sb, dict):
        _apply_sidebar_delta(state, sb, result)

    action = command.get("pending_action")
    if isinstance(action, dict) and action.get("type"):
        atype = str(action.get("type") or "").strip().lower()
        result.action_type = atype

        # M5 闭环：先提出计划（不启动线程），确认后再 run_m5
        if atype in ("propose_m5", "plan_m5"):
            import m5_agent_loop

            plan, errs = propose_m5_plan(state, action)
            result.m5_plan = plan
            result.m5_plan_text = m5_agent_loop.format_m5_plan_for_user(plan)
            result.errors.extend(errs)
            return result

        if atype == "confirm_m5":
            state["_m5_plan_confirmed"] = True
            action = {
                "type": "run_m5",
                "confirmed": True,
                "task": action.get("task"),
                "baseline_task": action.get("baseline_task"),
                "baseline_shp": action.get("baseline_shp"),
                "prob_th": action.get("prob_th"),
                "min_cnt": action.get("min_cnt"),
            }
            result.action_type = "run_m5"

        # E1 闭环：propose → confirm → run_e1
        if atype in ("propose_e1", "plan_e1"):
            import e1_agent_loop

            plan, errs = propose_e1_plan(state, action)
            result.e1_plan = plan
            result.e1_plan_text = e1_agent_loop.format_e1_plan_for_user(plan)
            result.errors.extend(errs)
            return result

        if atype == "confirm_e1":
            state["_e1_plan_confirmed"] = True
            action = {
                "type": "run_e1",
                "confirmed": True,
                "task": action.get("task"),
                "reference": action.get("reference") or action.get("e1_reference"),
                "data_root": action.get("data_root") or action.get("e1_data_root"),
                "compare_sources": action.get("compare_sources"),
                "prob_th": action.get("prob_th"),
                "min_cnt": action.get("min_cnt"),
            }
            result.action_type = "run_e1"

        # 本地潮滩推理闭环：propose → confirm → run_inference
        if atype in ("propose_inference", "plan_inference"):
            import inference_agent_loop

            plan, errs = propose_inference_plan(state, action)
            result.inference_plan = plan
            result.inference_plan_text = inference_agent_loop.format_inference_plan_for_user(plan)
            result.errors.extend(errs)
            return result

        if atype == "confirm_inference":
            pid = action.get("plan_id") or (
                state.get("_inference_pending_plan") or {}
            ).get("plan_id")
            if pid:
                ok, cerr = confirm_inference_plan(state, str(pid))
                if not ok:
                    result.errors.append(cerr or "推理计划确认失败。")
                    return result
            action = {
                "type": "run_inference",
                "confirmed": True,
                "task": action.get("task"),
                "plan_id": action.get("plan_id"),
                "prob_th": action.get("prob_th"),
                "min_cnt": action.get("min_cnt"),
            }
            result.action_type = "run_inference"

        # GEE 下载闭环：propose → confirm → run_gee_download
        if atype in ("propose_gee", "propose_gee_plan", "plan_gee"):
            import gee_agent_loop

            plan, errs = propose_gee_plan(state, action)
            result.gee_plan = plan
            result.gee_plan_text = gee_agent_loop.format_gee_plan_for_user(plan)
            result.errors.extend(errs)
            return result

        if atype == "confirm_gee":
            pid = action.get("plan_id") or (
                state.get("_gee_pending_plan") or {}
            ).get("plan_id")
            if pid:
                ok, cerr = confirm_gee_plan(state, str(pid))
                if not ok:
                    result.errors.append(cerr or "GEE 下载计划确认失败。")
                    return result
            action = {
                "type": "run_gee_download",
                "confirmed": True,
                "task": action.get("task"),
                "plan_id": action.get("plan_id"),
                "roi_name": action.get("roi_name"),
                "start_date": action.get("start_date"),
                "end_date": action.get("end_date"),
                "cloud_limit": action.get("cloud_limit"),
                "bands": action.get("bands"),
                "export_to": action.get("export_to"),
            }
            result.action_type = "run_gee_download"

        pt, at, errs = build_pending_task(state, action)
        result.errors.extend(errs)
        # 重型工具确认门闩：run_pipeline/run_inference/run_m4/run_gee_download/run_autotune
        # 未确认时，仅记录待确认请求（供 UI 弹出确认），绝不写入 pending_task/pending_autotune。
        # 手动侧栏按钮直接写 session_state 的路径不受影响。
        is_heavy = atype in ("run_pipeline", "run", "", "run_inference", "run_m4",
                             "run_gee_download", "run_autotune")
        if is_heavy and not bool(action.get("confirmed")):
            if errs and any("确认" in e for e in errs):
                state["_pending_heavy_confirm"] = {
                    "action_type": atype or "run_pipeline",
                    "label": HEAVY_ACTION_LABELS.get(atype or "run_pipeline", atype),
                    "task": action.get("task") or state.get("ui_selected_task"),
                    "action": action,
                }
            else:
                state.pop("_pending_heavy_confirm", None)
        elif is_heavy:
            state.pop("_pending_heavy_confirm", None)
        if pt and not errs:
            state["pending_task"] = pt
            state["is_running"] = True
            state["stop_requested"] = False
            state.pop("pending_autotune", None)
            if pt.get("mode") == "m5":
                state.pop("_m5_pending_plan", None)
            if pt.get("mode") == "e1":
                state.pop("_e1_pending_plan", None)
            if pt.get("inference_plan"):
                state.pop("_inference_pending_plan", None)
            if pt.get("mode") == "gee":
                state.pop("_gee_pending_plan", None)
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
        if one.m5_plan is not None:
            merged.m5_plan = one.m5_plan
            merged.m5_plan_text = one.m5_plan_text or merged.m5_plan_text
        if one.e1_plan is not None:
            merged.e1_plan = one.e1_plan
            merged.e1_plan_text = one.e1_plan_text or merged.e1_plan_text
        if one.inference_plan is not None:
            merged.inference_plan = one.inference_plan
            merged.inference_plan_text = one.inference_plan_text or merged.inference_plan_text
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
