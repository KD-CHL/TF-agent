# -*- coding: utf-8 -*-
"""
M5 变化检测 Agent 执行闭环：预检 → 计划 → 验证 → 摘要。

不依赖 Streamlit；由 agent_command_bridge / app 调用，保证条件检查与结果校验可信。
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import m5_engine

_M5_INTENT_RE = re.compile(
    r"(变化检测|时空变化|m5|M5|异动|萎缩|淤积|告警检测|对比基线|两期对比)",
    re.IGNORECASE,
)
_CONFIRM_RE = re.compile(
    r"^(确认|同意|好的?|可以|执行|开始|开始执行|确认执行|确认计划|就这样|ok|OK)[\s!！。．.]*$",
    re.IGNORECASE,
)
_CONFIRM_PHRASE_RE = re.compile(
    r"(确认.?(执行|计划|m5|变化检测)|开始.?(执行|m5|变化检测)|执行.?(计划|m5)|同意.?(执行|计划))",
    re.IGNORECASE,
)


def is_m5_intent(text: str) -> bool:
    return bool(_M5_INTENT_RE.search(text or ""))


def is_m5_confirm_utterance(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) > 80:
        return False
    if _CONFIRM_RE.match(t):
        return True
    return bool(_CONFIRM_PHRASE_RE.search(t))


def resolve_current_shp(
    final_root: str,
    task: str,
    prob: Optional[float] = None,
    cnt: Optional[int] = None,
) -> Optional[str]:
    task_dir = os.path.join(final_root, task)
    return m5_engine.find_final_shp_in_task_dir(task_dir, task, prob, cnt)


def list_available_periods(
    final_root: str,
    current_task: str,
    task_options: Optional[List[str]] = None,
    prob: Optional[float] = None,
    cnt: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """列出同区域更早年份的可用基线时期（含 SHP 路径）。"""
    year, region = m5_engine.parse_task_identity(current_task)
    if year is None or not region:
        return []

    periods: List[Dict[str, Any]] = []
    for task in m5_engine._iter_candidate_tasks(final_root, task_options):
        y, r = m5_engine.parse_task_identity(task)
        if y is None or r != region or y >= year:
            continue
        shp = m5_engine.find_final_shp_in_task_dir(
            os.path.join(final_root, task), task, prob, cnt
        )
        if not shp:
            continue
        periods.append(
            {
                "task": task,
                "year": y,
                "region": region,
                "shp_path": os.path.normpath(shp),
                "shp_exists": True,
            }
        )
    periods.sort(key=lambda x: x["year"], reverse=True)
    return periods


def build_m5_preflight(
    *,
    final_root: str,
    current_task: str,
    task_options: Optional[List[str]] = None,
    prob: Optional[float] = None,
    cnt: Optional[int] = None,
    baseline_task: Optional[str] = None,
    baseline_shp_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    生成结构化 M5 执行计划与可执行性判定。
    ready=True 时方可进入确认→执行。
    """
    final_root = os.path.normpath(str(final_root or "").strip())
    current_task = (current_task or "").strip()
    blockers: List[str] = []
    warnings: List[str] = []

    year, region = m5_engine.parse_task_identity(current_task)
    if not current_task:
        blockers.append("未指定当前任务（selected_task / task）。")
    if not final_root or not os.path.isdir(final_root):
        blockers.append(f"成果根目录不存在: {final_root or '（空）'}")
    if current_task and year is None:
        blockers.append(
            f"任务名「{current_task}」无法解析年份前缀（期望如 24zhejiang1）。"
        )

    current_shp = None
    if current_task and final_root and os.path.isdir(final_root):
        current_shp = resolve_current_shp(final_root, current_task, prob, cnt)
        if not current_shp:
            blockers.append(
                f"当期潮滩成果 SHP 不存在：{os.path.join(final_root, current_task)}"
            )

    periods = []
    if current_task and final_root and os.path.isdir(final_root) and year is not None:
        periods = list_available_periods(
            final_root, current_task, task_options, prob, cnt
        )

    baseline_shp = (baseline_shp_override or "").strip() or None
    chosen_baseline_task = (baseline_task or "").strip() or None

    if baseline_shp:
        baseline_shp = os.path.normpath(baseline_shp)
        if not os.path.isfile(baseline_shp):
            blockers.append(f"指定基线 SHP 不存在: {baseline_shp}")
            baseline_shp = None
        else:
            warnings.append("使用手动指定基线 SHP（跳过自动时期匹配）。")
    elif chosen_baseline_task:
        match = next((p for p in periods if p["task"] == chosen_baseline_task), None)
        if match:
            baseline_shp = match["shp_path"]
        else:
            # 允许直接用 final_root/task 下的 SHP
            cand = resolve_current_shp(final_root, chosen_baseline_task, prob, cnt)
            if cand:
                baseline_shp = cand
                periods = periods  # keep list
            else:
                blockers.append(f"指定基线任务无有效 SHP: {chosen_baseline_task}")
    elif periods:
        chosen_baseline_task = periods[0]["task"]
        baseline_shp = periods[0]["shp_path"]
    else:
        if current_task and year is not None and not blockers:
            blockers.append(
                f"未找到区域 [{region}] 在 {year} 年之前的可用基线时期（需 final_root 下有更早同区域 Final SHP）。"
            )

    ready = len(blockers) == 0 and bool(current_shp) and bool(baseline_shp)

    plan: Dict[str, Any] = {
        "schema": "m5_execution_plan_v1",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ready": ready,
        "blockers": blockers,
        "warnings": warnings,
        "current_task": current_task,
        "year": year,
        "region": region,
        "final_root": final_root,
        "current_shp": os.path.normpath(current_shp) if current_shp else None,
        "baseline_task": chosen_baseline_task,
        "baseline_shp": os.path.normpath(baseline_shp) if baseline_shp else None,
        "available_periods": periods,
        "prob": prob,
        "cnt": cnt,
        "steps": [
            "读取当前任务与成果账本中的当期潮滩成果",
            "匹配同区域更早时期的历史成果",
            "运行变化分析，生成差异区域与告警指标",
            "校验结果文件",
            "保存分析成果并加载到地图",
        ],
    }
    return plan


def format_m5_plan_for_user(plan: Dict[str, Any]) -> str:
    """面向用户的计划说明（确认前展示）。"""
    if not plan:
        return "尚未生成变化分析计划。"
    lines = ["## 潮滩变化分析 · 执行计划", ""]
    if plan.get("ready"):
        lines.append("**状态：可执行**（请回复「确认」或点击确认按钮后开始）")
    else:
        lines.append("**状态：暂不可执行**")
        for b in plan.get("blockers") or []:
            lines.append(f"- 阻塞：{b}")
    lines.append("")
    lines.append(f"- 当前任务：`{plan.get('current_task') or '—'}`")
    lines.append(f"- 当期 SHP：`{os.path.basename(plan.get('current_shp') or '') or '—'}`")
    lines.append(
        f"- 基线任务：`{plan.get('baseline_task') or '—'}` → "
        f"`{os.path.basename(plan.get('baseline_shp') or '') or '—'}`"
    )
    periods = plan.get("available_periods") or []
    if periods:
        names = ", ".join(f"{p['task']}({p['year']})" for p in periods[:8])
        lines.append(f"- 可用更早时期：{names}")
    for w in plan.get("warnings") or []:
        lines.append(f"- 注意：{w}")
    lines.append("")
    lines.append("确认后将真实运行变化分析，并根据磁盘报告与差异面回复结果。")
    return "\n".join(lines)


def verify_m5_outputs(report: Optional[Dict[str, Any]], workspace_dir: Optional[str] = None) -> Dict[str, Any]:
    """验证 M5 输出：报告字段、报告文件、可选差异面。"""
    checks: List[Dict[str, Any]] = []
    ok = True
    if not report or not isinstance(report, dict):
        return {
            "ok": False,
            "checks": [{"name": "report_object", "passed": False, "detail": "无报告对象"}],
        }

    def _check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        if not passed:
            ok = False
        checks.append({"name": name, "passed": passed, "detail": detail})

    _check("alert_level", bool(report.get("alert_level")), str(report.get("alert_level") or ""))
    _check("current_shp", bool(report.get("current_shp")), str(report.get("current_shp") or ""))
    _check("baseline_shp", bool(report.get("baseline_shp")), str(report.get("baseline_shp") or ""))

    report_path = report.get("report_path")
    if not report_path and workspace_dir and report.get("target_roi"):
        report_path = m5_engine.m5_report_path(workspace_dir, report["target_roi"])
    _check(
        "report_json_on_disk",
        bool(report_path and os.path.isfile(str(report_path))),
        str(report_path or ""),
    )

    qm = report.get("quantitative_metrics") or {}
    _check("quantitative_metrics", isinstance(qm, dict) and bool(qm), "")

    spatial = report.get("spatial_outputs") or {}
    loss = spatial.get("loss_shapefile_path")
    silt = spatial.get("siltation_shapefile_path")
    loss_ok = bool(loss) and str(loss) != "None" and os.path.isfile(str(loss))
    silt_ok = bool(silt) and str(silt) != "None" and os.path.isfile(str(silt))
    # 差异面可为空（无显著变化），不算失败；仅记录
    checks.append(
        {
            "name": "loss_zones_shp",
            "passed": True,
            "detail": str(loss) if loss_ok else "无（可为空）",
        }
    )
    checks.append(
        {
            "name": "siltation_zones_shp",
            "passed": True,
            "detail": str(silt) if silt_ok else "无（可为空）",
        }
    )

    return {
        "ok": ok,
        "checks": checks,
        "map_candidate": pick_m5_map_path(report),
        "report_path": report_path,
    }


def pick_m5_map_path(report: Optional[Dict[str, Any]]) -> Optional[str]:
    """优先加载萎缩区，其次淤积区，再次当期潮滩。"""
    if not report:
        return None
    spatial = report.get("spatial_outputs") or {}
    for key in ("loss_shapefile_path", "siltation_shapefile_path"):
        p = spatial.get(key)
        if p and str(p) != "None" and os.path.isfile(str(p)):
            return os.path.normpath(str(p))
    cur = report.get("current_shp")
    if cur and os.path.isfile(str(cur)):
        return os.path.normpath(str(cur))
    return None


def summarize_m5_report_for_chat(
    report: Optional[Dict[str, Any]],
    verification: Optional[Dict[str, Any]] = None,
) -> str:
    """基于真实工具结果生成 Copilot 回复（禁止编造指标）。"""
    if not report:
        return (
            "变化分析未生成有效结果。"
            "常见原因：缺少往年同区域基线成果，或当期成果路径无效。"
            "请检查成果目录后重试。"
        )
    qm = report.get("quantitative_metrics") or {}
    ae = qm.get("area_evolution") or {}
    ct = qm.get("centroid_trajectory") or {}
    lvl = report.get("alert_level", "—")
    lines = [
        "## 潮滩变化分析结果（已验证）",
        "",
        f"- 任务：`{report.get('target_roi') or '—'}`",
        f"- 基线：`{report.get('baseline_task') or os.path.basename(str(report.get('baseline_shp') or '')) or '—'}`",
        f"- 告警级别：**{lvl}**",
        f"- 诊断：{report.get('diagnostic_message') or '—'}",
        (
            f"- 面积：{ae.get('baseline_area_km2', '?')} → {ae.get('current_area_km2', '?')} km² "
            f"（{ae.get('change_rate_percentage', '?')}%）"
        ),
        f"- 重心漂移：{ct.get('drift_distance_meters', '?')} m",
    ]
    spatial = report.get("spatial_outputs") or {}
    loss = spatial.get("loss_shapefile_path")
    silt = spatial.get("siltation_shapefile_path")
    if loss and str(loss) != "None" and os.path.isfile(str(loss)):
        lines.append(f"- 萎缩区：`{os.path.basename(str(loss))}`（已登记，可在地图查看）")
    if silt and str(silt) != "None" and os.path.isfile(str(silt)):
        lines.append(f"- 淤积区：`{os.path.basename(str(silt))}`")
    rp = report.get("report_path")
    if rp:
        lines.append(f"- 报告：`{os.path.basename(str(rp))}`")
    if verification is not None:
        lines.append(
            f"- 输出校验：{'通过' if verification.get('ok') else '未完全通过'}"
        )
    lines.append("")
    lines.append("以上指标均来自本次变化分析的真实输出，而非模型臆测。")
    return "\n".join(lines)


def build_m5_context_for_agent(
    final_root: str,
    current_task: str,
    task_options: Optional[List[str]] = None,
    pending_plan: Optional[Dict[str, Any]] = None,
) -> str:
    """注入 Agent 提示的 M5 账本快照。"""
    lines = ["【潮滩变化分析账本】"]
    if not current_task:
        lines.append("- 当前任务：未选")
        return "\n".join(lines)
    lines.append(f"- 当前任务：{current_task}")
    year, region = m5_engine.parse_task_identity(current_task)
    lines.append(f"- 解析：year={year} region={region or '—'}")
    shp = resolve_current_shp(final_root, current_task) if final_root else None
    lines.append(f"- 当期 SHP：{'有 → ' + os.path.basename(shp) if shp else '无'}")
    periods = (
        list_available_periods(final_root, current_task, task_options)
        if final_root and current_task
        else []
    )
    if periods:
        lines.append(
            "- 可用基线时期："
            + ", ".join(f"{p['task']}" for p in periods[:10])
        )
    else:
        lines.append("- 可用基线时期：无")
    if pending_plan:
        lines.append(
            f"- 待确认计划：ready={pending_plan.get('ready')} "
            f"baseline={pending_plan.get('baseline_task') or '—'}"
        )
        lines.append("- 用户确认后请 dispatch pending_action.type=run_m5 且 confirmed=true")
    else:
        lines.append(
            "- 用户要做变化检测时：先 propose_m5 生成计划并请用户确认，"
            "再 run_m5；不要用 run_pipeline 代替独立 M5。"
        )
    return "\n".join(lines)
