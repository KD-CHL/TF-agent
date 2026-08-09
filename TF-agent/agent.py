import os
import re
import base64
import io
import threading
from typing import Optional

# Workaround for Windows OpenMP runtime duplication from mixed scientific deps.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import numpy as np
from dotenv import load_dotenv
import chromadb
from chromadb.utils import embedding_functions
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage
from langgraph.prebuilt import create_react_agent

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_THIS_DIR, ".env"), override=True)
load_dotenv(override=False)


# ==========================================
# 1. 定义 Agent 的工具箱 (Tools)
# ==========================================

# 文献库懒加载：避免「每次打开 Copilot / 任意首轮对话」就拉 Chroma + 下载/加载 BGE（与问题内容无关）
_kb_collection = None
_kb_lock = threading.Lock()


def _get_knowledge_collection():
    """仅在首次调用 search_knowledge_base 时初始化本地向量库与嵌入模型。"""
    global _kb_collection
    with _kb_lock:
        if _kb_collection is not None:
            return _kb_collection
        print("[CSTF-Agent] 首次触发文献检索，正在连接本地 Chroma 并加载 BGE 嵌入模型…")
        _db_default = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rs_knowledge_db")
        db_path = os.environ.get("CHROMA_RS_DB_PATH", _db_default)
        os.makedirs(db_path, exist_ok=True)
        chroma_client = chromadb.PersistentClient(path=db_path)
        bge_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="BAAI/bge-small-zh-v1.5"
        )
        _kb_collection = chroma_client.get_or_create_collection(
            name="remote_sensing_papers",
            embedding_function=bge_ef,
        )
        return _kb_collection


@tool
def search_knowledge_base(keywords: str) -> str:
    """
    【学术与政策检索工具】
    当用户询问关于遥感文献（如潮滩分割、注意力机制、反演公式）或政策法规（如《国家湿地保护法》、管控红线）时，必须调用此工具！
    参数 `keywords` 必须是你提炼出的核心检索词，多个词之间用空格隔开。
    例如："多尺度注意力机制 潮滩边缘" 或 "国家湿地保护法 红树林 管控"
    """
    print(f"\n[Agent 后台动作] 🚀 正在调用 ChromaDB 检索文献，关键词：{keywords}")

    collection = _get_knowledge_collection()
    results = collection.query(
        query_texts=[keywords],
        n_results=2
    )

    docs_batch = results.get("documents") or []
    if not docs_batch or not docs_batch[0]:
        return "本地知识库中未检索到相关文献或法规，请告知用户该领域暂无数据支撑。"

    metas_batch = results.get("metadatas") or [[]]
    retrieved_context = "【系统从本地数据库中检索到的权威资料如下】：\n"
    for i, (doc, meta) in enumerate(zip(docs_batch[0], metas_batch[0] if metas_batch else [])):
        retrieved_context += f"文献 {i+1} (来源: {meta['source']}): {doc}\n"
        
    retrieved_context += "\n请基于以上检索到的真实资料回答用户的问题，必须在回答中引用文献来源，严禁自行编造公式或法规内容！"
    
    return retrieved_context

@tool
def dispatch_system_command(command_json: str) -> str:
    """
    【系统控制主工具 · 凡改侧栏/跑流程/跳地图必用】
    参数 command_json 为合法 JSON 字符串（不要 markdown 代码块）。

    结构：
    {
      "map": {"lat": 30.2, "lon": 121.5, "zoom": 10},          // 可选，仅跳地图
      "sidebar_states": { ... },                                  // 可选，差量更新侧栏
      "pending_action": { "type": "run_pipeline"|"run_m4"|"run_autotune"|"propose_m5"|"run_m5"|"confirm_m5"|"propose_e1"|"run_e1"|"confirm_e1", ... }  // 可选，启动后台
    }

    sidebar_states 全部可用键（未提及则省略，禁止脑补）：
    workflow_tab(潮滩推理|GEE 数据下载), selected_task, run_mode(dl|index), inference_mode(深度学习|指数法),
    prob_th(0.01~0.50), min_cnt(1~10), adaptive_mode, force_rerun,
    root_dir, mask_root, final_root, model_path, shp_path, points_shp, task_aoi_shp,
    m5_enabled, m5_baseline_shp, e1_enabled, e1_data_root, e1_reference, e1_compare_sources[],
    e1_export_maps, e1_export_heatmap,
    m4_roi_path, m4_roi_name, m4_start_date, m4_end_date, m4_export_to(drive|local),
    m4_drive_folder, m4_local_dir, m4_cloud, m4_min_land, m4_max_land, m4_min_pix,
    m4_bands[], m4_scale, m4_gee_proxy, m4_gee_project

    pending_action：
    - run_pipeline: 需要 task（可来自 selected_task 或快照）；prob/cnt 可省略则用侧栏
    - run_m4: m4_params 可含 roi_path/roi_name/start_date/end_date/cloud_limit 等
    - run_autotune: autotune_params.reference_id + objective(iou|f1|iou_f1)；需 adaptive_mode=true
    - propose_m5: 仅生成 M5 变化检测计划（读账本/时期），不启动线程；推荐用 prepare_m5_change_detection
    - run_m5 / confirm_m5: 用户确认后执行独立 M5（confirmed=true）；推荐用 confirm_and_run_m5
    - propose_e1: 仅生成 E1 多源一致性计划；推荐用 prepare_e1_consistency_check
    - run_e1 / confirm_e1: 用户确认后执行独立 E1；推荐用 confirm_and_run_e1

    重要（重型工具确认门闩）：run_pipeline / run_m4 / run_autotune 属于重型操作，
    必须先以 confirmed=true 显式确认（一般只在用户明确说「开始/执行/启动/下载」时给出）；
    未确认时系统不会启动任务，仅提示用户确认。不可绕过。

    口语速查：
    「跑/推理/合成/开始」→ pending_action.run_pipeline（confirmed=true）
    「本地影像推理/潮滩推理/模型跑图」→ local_tidal_flat_inference，确认后 confirm_inference
    「下载/GEE/下影像」→ gee_download_plan（按地图 AOI 下载，计划展示后确认 confirm_gee_download；
       下载完成后不会自动启动推理，如需推理请再发起推理任务）
    「调参/搜最优/AutoTune」→ adaptive_mode=true + run_autotune（confirmed=true）
    「5%/百分之五」→ prob_th=0.05；「频次2/两次」→ min_cnt=2
    「关M5/不要E1」→ m5_enabled/e1_enabled=false
    「变化检测/M5/两期对比/萎缩淤积」→ 先 prepare_m5_change_detection，确认后再 confirm_and_run_m5
    「多源一致性/E1/和师姐比/分歧图」→ 先 prepare_e1_consistency_check，确认后再 confirm_and_run_e1
    """
    cmd = command_json.strip()
    if cmd.startswith("```"):
        cmd = cmd.strip("`").strip()
        if cmd.lower().startswith("json"):
            cmd = cmd[4:].strip()
    return f"[SYSTEM_COMMAND_JSON]\n{cmd}\n[/SYSTEM_COMMAND_JSON]"


@tool
def prepare_m5_change_detection(
    task: Optional[str] = None,
    baseline_task: Optional[str] = None,
) -> str:
    """
    【M5 时空变化检测 · 预检与计划】
    用户要对「已有潮滩成果」做变化检测 / M5 / 两期对比 / 萎缩淤积告警时，必须先调用本工具。
    会生成可验证执行计划，等待用户确认；不会立刻跑推理流水线。
    task 可省略（用侧栏当前任务）；baseline_task 可省略（自动选最近更早同区域时期）。
    """
    import json as _json

    action: dict = {"type": "propose_m5"}
    if task and str(task).strip():
        action["task"] = str(task).strip()
    if baseline_task and str(baseline_task).strip():
        action["baseline_task"] = str(baseline_task).strip()
    payload = {"pending_action": action, "sidebar_states": {"m5_enabled": True}}
    if task and str(task).strip():
        payload["sidebar_states"]["selected_task"] = str(task).strip()
    return (
        "[SYSTEM_COMMAND_JSON]\n"
        + _json.dumps(payload, ensure_ascii=False)
        + "\n[/SYSTEM_COMMAND_JSON]"
    )


@tool
def confirm_and_run_m5(task: Optional[str] = None) -> str:
    """
    【M5 确认执行】
    仅在用户已明确确认执行计划后调用（如「确认」「开始执行」）。
    将真实调用现有 M5 引擎；禁止在未确认时调用。
    """
    import json as _json

    action: dict = {"type": "run_m5", "confirmed": True}
    if task and str(task).strip():
        action["task"] = str(task).strip()
    payload = {"pending_action": action}
    return (
        "[SYSTEM_COMMAND_JSON]\n"
        + _json.dumps(payload, ensure_ascii=False)
        + "\n[/SYSTEM_COMMAND_JSON]"
    )


@tool
def prepare_e1_consistency_check(
    task: Optional[str] = None,
    reference: Optional[str] = None,
) -> str:
    """
    【E1 多源一致性诊断 · 预检与计划】
    用户要对「已有潮滩成果」做多源一致性 / E1 / 和师姐对比 / 分歧图时，必须先调用本工具。
    生成可验证计划并等待确认；不跑推理、不下载 GEE。
    task / reference 可省略（用侧栏当前任务与 ui_e1_reference）。
    """
    import json as _json

    action: dict = {"type": "propose_e1"}
    if task and str(task).strip():
        action["task"] = str(task).strip()
    sb: dict = {"e1_enabled": True}
    if task and str(task).strip():
        sb["selected_task"] = str(task).strip()
    if reference and str(reference).strip():
        sb["e1_reference"] = str(reference).strip()
        action["reference"] = str(reference).strip()
    payload = {"pending_action": action, "sidebar_states": sb}
    return (
        "[SYSTEM_COMMAND_JSON]\n"
        + _json.dumps(payload, ensure_ascii=False)
        + "\n[/SYSTEM_COMMAND_JSON]"
    )


@tool
def confirm_and_run_e1(task: Optional[str] = None) -> str:
    """
    【E1 确认执行】
    仅在用户已明确确认 E1 计划后调用。真实调用 e1_engine；禁止未确认执行。
    """
    import json as _json

    action: dict = {"type": "run_e1", "confirmed": True}
    if task and str(task).strip():
        action["task"] = str(task).strip()
    payload = {"pending_action": action}
    return (
        "[SYSTEM_COMMAND_JSON]\n"
        + _json.dumps(payload, ensure_ascii=False)
        + "\n[/SYSTEM_COMMAND_JSON]"
    )


@tool
def analyze_workflow(
    target_year: Optional[int] = None,
    baseline_year: Optional[int] = None,
    need_e1: Optional[bool] = None,
    need_m5: Optional[bool] = None,
    skip_e1: Optional[bool] = None,
    skip_m5: Optional[bool] = None,
    region: Optional[str] = None,
    task: Optional[str] = None,
    goal: Optional[str] = None,
) -> str:
    """
    【端到端潮滩分析 Workflow · 生成执行计划（必须先调用！）】
    当用户要求「分析当前 AOI 的 XXXX 年潮滩 / 和 XXXX 年比较变化 / 评价精度 / 生成报告」时，
    必须先调用本工具生成确定性执行计划（GEE→本地推理→E1 精度评价→M5 变化检测→PDF 报告），
    展示给用户确认。未确认前绝不执行任何下载/推理。

    - target_year: 分析年份（如 2024）。省略则用侧栏默认（ui_workflow_target_year）。
    - baseline_year: 对比基线年份（如 2022）。省略则用侧栏默认；用户说「和XX年比较」时必填。
    - need_e1: 用户明确要求「评价精度/和真值对比/师姐」→ True（必做，缺真值则阻塞）。
      用户说「不要精度评价/跳过E1」→ False。省略 → 有真值才做，否则自动跳过。
    - need_m5: 用户明确要求「变化检测/M5/萎缩淤积」→ True（必做，缺基线则阻塞）。
      用户说「不要变化检测/跳过M5」→ False。省略 → 有基线才做，否则自动跳过。
    - skip_e1 / skip_m5: 等价于 need_e1/need_m5=False。
    - region: 区域标识（如 quanzhou）；省略则从当前 AOI 推导。
    - task: 可省略（用侧栏当前任务 / AOI 自动命名）。
    - goal: 可省略（由系统按年份/基线/意图自动生成）。
    """
    import json as _json

    action: dict = {"type": "propose_workflow"}
    if target_year is not None:
        action["target_year"] = int(target_year)
    if baseline_year is not None:
        action["baseline_year"] = int(baseline_year)
    if need_e1 is not None:
        action["need_e1"] = bool(need_e1)
    if need_m5 is not None:
        action["need_m5"] = bool(need_m5)
    if skip_e1 is True:
        action["skip_e1"] = True
    if skip_m5 is True:
        action["skip_m5"] = True
    if region and str(region).strip():
        action["region"] = str(region).strip()
    if task and str(task).strip():
        action["task"] = str(task).strip()
    if goal and str(goal).strip():
        action["goal"] = str(goal).strip()
    payload = {"pending_action": action}
    return (
        "[SYSTEM_COMMAND_JSON]\n"
        + _json.dumps(payload, ensure_ascii=False)
        + "\n[/SYSTEM_COMMAND_JSON]"
    )


@tool
def confirm_workflow(workflow_id: Optional[str] = None) -> str:
    """
    【端到端潮滩分析 Workflow · 确认执行】
    仅在用户已明确确认 Workflow 计划后调用（用户说「确认/开始/执行/就这么办」）。
    真实按依赖顺序调用既有闭环（GEE→推理→E1/M5→PDF）；禁止未确认执行。
    workflow_id 可省略（用当前待确认计划）。
    """
    import json as _json

    action: dict = {"type": "confirm_workflow"}
    if workflow_id and str(workflow_id).strip():
        action["workflow_id"] = str(workflow_id).strip()
    payload = {"pending_action": action}
    return (
        "[SYSTEM_COMMAND_JSON]\n"
        + _json.dumps(payload, ensure_ascii=False)
        + "\n[/SYSTEM_COMMAND_JSON]"
    )


@tool
def trigger_spatial_analysis(
    task_node: str,
    prob_th: float,
    min_cnt: int,
    run_mode: str = "dl",
    m5_enabled: Optional[bool] = None,
    e1_enabled: Optional[bool] = None,
) -> str:
    """
    【兼容工具 · 跑潮滩推理】用户明确要求跑模型/推理时调用。
    run_mode: dl=深度学习, index=指数法。prob/min_cnt 必须来自用户原话，禁止编造。
    推荐改用 dispatch_system_command 以同时调整 M5/E1。
    """
    import json as _json

    payload = {
        "sidebar_states": {
            "selected_task": task_node,
            "prob_th": prob_th,
            "min_cnt": int(min_cnt),
            "run_mode": run_mode,
        },
        "pending_action": {"type": "run_pipeline", "task": task_node},
    }
    if m5_enabled is not None:
        payload["sidebar_states"]["m5_enabled"] = m5_enabled
    if e1_enabled is not None:
        payload["sidebar_states"]["e1_enabled"] = e1_enabled
    return f"[SYSTEM_COMMAND_JSON]\n{_json.dumps(payload, ensure_ascii=False)}\n[/SYSTEM_COMMAND_JSON]"


@tool
def change_map_view(
    location_name: str,
    lat: float,
    lon: float,
    zoom: int,
    preset: Optional[str] = None,
    label: Optional[str] = None,
) -> str:
    """
    地图视角跳转。用户表达查看/定位/跳到某地时必须立即调用。
    可与 dispatch_system_command 合并；单独跳转时用本工具。
    preset: 可选，地名预设（如 杭州湾/乐清湾/中国），用于高度档位与展示。
    label: 可选，状态栏展示名（默认取 location_name）。
    """
    import json as _json

    payload = {"map": {"lat": lat, "lon": lon, "zoom": int(zoom)}}
    if preset:
        payload["map"]["preset"] = str(preset)
    if label:
        payload["map"]["label"] = str(label)
    elif location_name:
        payload["map"]["label"] = str(location_name)
    return f"[SYSTEM_COMMAND_JSON]\n{_json.dumps(payload, ensure_ascii=False)}\n[/SYSTEM_COMMAND_JSON]"


@tool
def assist_gee_download(
    region_name: str,
    year: int,
    cloud_limit: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    run_now: bool = False,
) -> str:
    """
    GEE Sentinel-2 下载（M4）。用户要下载遥感数据时调用。
    若用户说「启动下载/开始下」，设 run_now=true 并在 pending_action 中 type=run_m4。
    """
    import json as _json

    sd = start_date or f"{int(year)}-01-01"
    ed = end_date or f"{int(year)}-01-31"
    payload: dict = {
        "sidebar_states": {
            "workflow_tab": "GEE 数据下载",
            "m4_roi_name": region_name,
            "m4_start_date": sd,
            "m4_end_date": ed,
        },
    }
    if cloud_limit is not None:
        payload["sidebar_states"]["m4_cloud"] = int(cloud_limit)
    if run_now:
        payload["pending_action"] = {
            "type": "run_m4",
            "confirmed": True,  # run_now=true 即用户明确要求启动，满足重型工具确认门闩
            "task": region_name,
            "m4_params": {
                "roi_name": region_name,
                "start_date": sd,
                "end_date": ed,
                "cloud_limit": cloud_limit,
            },
        }
    return f"[SYSTEM_COMMAND_JSON]\n{_json.dumps(payload, ensure_ascii=False)}\n[/SYSTEM_COMMAND_JSON]"


@tool
def local_tidal_flat_inference(
    task_id: Optional[str] = None,
    prob_th: Optional[float] = None,
    cnt: Optional[int] = None,
    run_now: bool = False,
) -> str:
    """
    【本地潮滩推理 · 生成执行计划】
    用户要对「本地遥感影像」做潮滩推理（深度学习/CDNet/模型跑图/推理）时调用。
    只接收 task_id / prob_th / cnt / run_now，**不接收任何路径参数**（路径一律使用
    侧栏已配置的合法值或已登记资产，禁止编造路径）。
    - 先调用本工具生成计划（propose）；plan 展示后必须等用户确认，再调用 confirm_inference。
    - run_now=true 表示用户已明确要求启动（等价「开始/执行/跑」），此时会直接进入
      计划→校验→确认（自动确认）→执行闭环；否则只生成计划等待确认。
    prob_th 范围 0.01~0.50；cnt 范围 1~10；越界将由系统校验拒绝。
    """
    import json as _json

    action: dict = {"type": "propose_inference"}
    if task_id and str(task_id).strip():
        action["task"] = str(task_id).strip()
    if prob_th is not None:
        action["prob_th"] = float(prob_th)
    if cnt is not None:
        action["cnt"] = int(cnt)
    if run_now:
        action["run_now"] = True
    payload = {"pending_action": action}
    if task_id and str(task_id).strip():
        payload["sidebar_states"] = {"selected_task": str(task_id).strip()}
    return (
        "[SYSTEM_COMMAND_JSON]\n"
        + _json.dumps(payload, ensure_ascii=False)
        + "\n[/SYSTEM_COMMAND_JSON]"
    )


@tool
def confirm_inference(plan_id: Optional[str] = None) -> str:
    """
    【本地潮滩推理 · 确认执行】
    仅在用户已明确确认推理计划后调用（如「确认」「开始执行」）。
    同一 plan_id 只确认一次；将真实调用现有 pre_engine / post_engine。
    禁止在未确认时调用；禁止编造 plan_id（从计划中获取）。
    """
    import json as _json

    action: dict = {"type": "confirm_inference", "confirmed": True}
    if plan_id and str(plan_id).strip():
        action["plan_id"] = str(plan_id).strip()
    payload = {"pending_action": action}
    return (
        "[SYSTEM_COMMAND_JSON]\n"
        + _json.dumps(payload, ensure_ascii=False)
        + "\n[/SYSTEM_COMMAND_JSON]"
    )


@tool
def gee_download_plan(
    task_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    bands: Optional[str] = None,
    cloud_limit: Optional[int] = None,
    export_to: Optional[str] = None,
    run_now: bool = False,
) -> str:
    """
    【GEE 遥感影像下载 · 生成执行计划】
    用户要「下载/获取 GEE/哨兵/COPERNICUS 影像」「根据地图 AOI 取影像数据」时调用。
    使用当前地图绘制的 AOI（无需传 geometry）；也可传 task_id / 起止日期 / 波段等参数。
    - 波段 bands：逗号分隔字符串，如 "B4,B3,B2"（RGB 顺序，默认）；可含 index_bands 由系统追加。
    - 先调用本工具生成计划（propose）；plan 展示后必须等用户确认，再调用 confirm_gee_download。
    - run_now=true 表示用户已明确要求启动，直接进入计划→校验→确认→执行闭环；
      否则只生成计划等待确认（下载完成后**不会自动启动推理**）。
    """
    import json as _json

    action: dict = {"type": "propose_gee"}
    if task_id and str(task_id).strip():
        action["task"] = str(task_id).strip()
    if start_date and str(start_date).strip():
        action["start_date"] = str(start_date).strip()
    if end_date and str(end_date).strip():
        action["end_date"] = str(end_date).strip()
    if bands and str(bands).strip():
        action["bands"] = [b.strip() for b in str(bands).split(",") if b.strip()]
    if cloud_limit is not None:
        action["cloud_limit"] = int(cloud_limit)
    if export_to and str(export_to).strip():
        action["export_to"] = str(export_to).strip()
    if run_now:
        action["run_now"] = True
    payload = {"pending_action": action}
    if task_id and str(task_id).strip():
        payload["sidebar_states"] = {"m4_roi_name": str(task_id).strip()}
    return (
        "[SYSTEM_COMMAND_JSON]\n"
        + _json.dumps(payload, ensure_ascii=False)
        + "\n[/SYSTEM_COMMAND_JSON]"
    )


@tool
def confirm_gee_download(plan_id: Optional[str] = None) -> str:
    """
    【GEE 遥感影像下载 · 确认执行】
    仅在用户已明确确认 GEE 下载计划后调用（如「确认」「开始下载」）。
    同一 plan_id 只确认一次；将真实调用现有 m4_engine 下载（Drive 提交或本地下载）。
    下载完成后**不会自动启动推理**（如需推理请另行发起推理任务）。
    禁止在未确认时调用；禁止编造 plan_id（从计划中获取）。
    """
    import json as _json

    action: dict = {"type": "confirm_gee", "confirmed": True}
    if plan_id and str(plan_id).strip():
        action["plan_id"] = str(plan_id).strip()
    payload = {"pending_action": action}
    return (
        "[SYSTEM_COMMAND_JSON]\n"
        + _json.dumps(payload, ensure_ascii=False)
        + "\n[/SYSTEM_COMMAND_JSON]"
    )



# ==========================================
# 2. 通义千问 API（阿里云百炼 DashScope · OpenAI 兼容模式）
# ==========================================
# 在系统环境变量或 .env 中设置（勿把 Key 写进代码仓库）：
#   DASHSCOPE_API_KEY=sk-你的Key
# 可选：
#   QWEN_OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1  （默认即此）
#   QWEN_CHAT_MODEL=qwen-plus          # 纯文本+工具推荐：qwen-plus / qwen-max / qwen-turbo
#   QWEN_CHAT_MODEL=qwen-vl-plus       # 需要上传图片解译时改用 VL 系列（qwen-vl-plus / qwen-vl-max）
#
# 控制台与计费：https://bailian.console.aliyun.com/  → API-KEY
_dash_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY")
_qwen_base = os.environ.get("QWEN_OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
_qwen_model = os.environ.get("QWEN_CHAT_MODEL", "qwen-plus")
_tiff_mode = os.environ.get("YYNET_TIFF_MODE", "auto").strip().lower()  # auto/native/png
_attach_geo_meta = os.environ.get("YYNET_ATTACH_GEO_META", "1").strip().lower() not in {"0", "false", "no"}
_tiff_auto_png_mb = float(os.environ.get("YYNET_TIFF_AUTO_PNG_MB", "12"))
_vlm_max_side = int(os.environ.get("YYNET_VLM_MAX_SIDE", "2048"))

if not _dash_key:
    raise RuntimeError(
        "未检测到 API Key：请设置环境变量 DASHSCOPE_API_KEY（或 QWEN_API_KEY）。"
        "获取方式：阿里云百炼控制台 → API-KEY。"
    )

llm = ChatOpenAI(
    model=_qwen_model,
    api_key=_dash_key,
    base_url=_qwen_base,
    temperature=0.1,
)

tools = [
    dispatch_system_command,
    local_tidal_flat_inference,
    confirm_inference,
    gee_download_plan,
    confirm_gee_download,
    prepare_m5_change_detection,
    confirm_and_run_m5,
    prepare_e1_consistency_check,
    confirm_and_run_e1,
    analyze_workflow,
    confirm_workflow,
    trigger_spatial_analysis,
    change_map_view,
    assist_gee_download,
    search_knowledge_base,
]

# ==========================================
# 3. 组装新一代 LangGraph Agent
# ==========================================
system_prompt_base = """你是 CSTF-Copilot，遥感潮滩分析平台的对话控制中枢。
用户不会按固定句式提问；你必须从碎片化、口语化、多意图混杂的句子中还原真实企图，并调用正确工具。

═══════════════════════════════════════
第一步 · 意图分类（每轮必做，可多标签）
═══════════════════════════════════════
A. 纯问答 / 文献 / 图片解译 → 只回答或 search_knowledge_base，**禁止** dispatch_system_command
B. 只改侧栏、不立即运行 → dispatch_system_command，**不要** pending_action
   例：「概率改成 8%」「把 E1 打开」「切到下载页」「云量设 20」
C. 改侧栏并立即运行 → dispatch_system_command，sidebar_states + pending_action 同轮给出
   例：「用 5% 跑一下浙江」「下载杭州湾 2020 年 1 月影像并开始」
D. 只跳地图 → map 字段（可合并进 dispatch_system_command）
   例：「看看杭州湾」「定位到南流江口」「地图挪到舟山」
E. 承前省略 / 指代 → 结合【侧栏快照】与对话上文补全 task/参数
   例：「那就跑吧」「同样参数再跑一遍」「云量改成 20 再下」
H. **本地潮滩推理可信执行闭环**
   - 「对本地影像跑推理 / 潮滩推理 / 模型跑图」→ **local_tidal_flat_inference**（生成计划）
   - 展示计划后必须等用户确认；用户说「确认/开始执行」→ **confirm_inference(plan_id)**
   - **禁止**编造权重路径/输入目录；路径一律用侧栏合法值；参数越界由系统校验
   - run_now=true 仅当用户已明确说「开始/执行/跑」；否则只生成计划
   - 同一 plan_id 只确认一次；完成后 Copilot 只回复工具真实输出
F. **M5 时空变化检测闭环**
   - 「做变化检测 / 跑 M5 / 两期对比 / 看萎缩淤积」→ **prepare_m5_change_detection**（propose_m5）
   - 展示计划后等用户确认；用户说「确认/开始执行」→ **confirm_and_run_m5**
   - **禁止**用 run_pipeline 冒充独立 M5；**禁止**未确认就 run_m5
   - 必须结合【M5 变化检测账本】判断当期 SHP / 可用基线时期；条件不足时说明 blockers，不要假装已跑完
G. **E1 多源一致性闭环**
   - 「多源一致性 / 跑 E1 / 和师姐比 / 分歧图」→ **prepare_e1_consistency_check**（propose_e1）
   - 用户确认后 → **confirm_and_run_e1**
   - **禁止**用 run_pipeline 冒充独立 E1；**禁止**未确认就 run_e1
   - 结合【E1 账本】检查当期 SHP / data_root / reference；条件不足说明 blockers
I. **端到端潮滩分析 Workflow（GEE→推理→E1/M5→PDF）**
   - 「分析当前 AOI 的 2024 年潮滩」「和 2022 年比较变化」「评价精度/有真值就评」「生成报告」
     这类**多阶段综合请求** → **analyze_workflow**（生成确定性执行计划，展示后等确认）
   - 用户确认后 → **confirm_workflow**
   - 参数映射：分析年份→target_year；比较年份→baseline_year；
     「评价精度/和真值比」→need_e1=true；「不要E1/跳过精度」→skip_e1=true；
     「变化检测/M5/萎缩淤积」→need_m5=true；「不要M5」→skip_m5=true；
     区域（泉州湾→quanzhou）→region
   - **禁止**：把端到端请求拆成零散 run_pipeline/run_m4/run_e1 逐个手动拼装；
     未确认前绝不执行任何下载/推理；不得编造 scene_count/精度数值

═══════════════════════════════════════
第二步 · 工具选择
═══════════════════════════════════════
- **首选** dispatch_system_command：可同时改多个侧栏项 + 跳地图 + 启动流程
- local_tidal_flat_inference / confirm_inference：本地潮滩推理可信执行闭环（先计划后确认）
- prepare_m5_change_detection / confirm_and_run_m5：独立 M5 变化检测闭环
- prepare_e1_consistency_check / confirm_and_run_e1：独立 E1 多源一致性闭环
- analyze_workflow / confirm_workflow：**端到端潮滩分析**（GEE→推理→E1/M5→PDF，先计划后确认）
- change_map_view：仅当地图跳转且无任何侧栏/运行需求时用
- assist_gee_download：用户明确要 GEE 下载时可快捷调用（等价于 dispatch + run_m4）
- trigger_spatial_analysis：仅简单跑推理且无 M5/E1/Tab 变更时用
- search_knowledge_base：文献、法规、方法原理类问题

调用后必须在回复**末尾原样附上**工具返回的 [SYSTEM_COMMAND_JSON]...[/SYSTEM_COMMAND_JSON] 块。

═══════════════════════════════════════
第三步 · 口语 → JSON 映射规范
═══════════════════════════════════════

【任务名】
- 必须与「可用任务目录」列表模糊匹配：24浙江/浙江2024 → 24zhejiang1
- 不在列表中 → 明确告知无法运行，**禁止**编造任务名或强行 pending_action

【推理方式】
- 深度学习/CDNet/模型/神经网络 → run_mode=dl 或 inference_mode=深度学习
- 指数法/mNDWI/ACWI/不用模型 → run_mode=index 或 inference_mode=指数法

【阈值】
- 5%/百分之五/概率0.05 → prob_th=0.05（注意：5% 不是 5.0）
- 频次2/两次/最少2次/cnt=2 → min_cnt=2
- 「参数默认/按侧栏/当前设置」→ **省略** prob_th/min_cnt（前端保留快照值）

【M5 / E1】
- 开/启用/加上/要做 变化检测（作为推理后置）→ m5_enabled=true；关/不要/跳过 → false
- **独立 M5 闭环**（已有成果、只要变化检测）：prepare_m5_change_detection → 等确认 → confirm_and_run_m5
- **独立 E1 闭环**（多源一致性/分歧图）：prepare_e1_consistency_check → 等确认 → confirm_and_run_e1
- 仅改侧栏开 E1（不立刻跑）→ e1_enabled=true
- 师姐2020/参考2020 → e1_reference=师姐_2020（2022/2024/2025 同理）

【GEE 下载 M4】
- 下载/下数据/GEE/哨兵/Sentinel → workflow_tab=GEE数据下载
- 云量20/云小于30 → m4_cloud=20 或 30
- 2020年1月/2020-01 → m4_start_date/m4_end_date
- 「开始下载/启动M4/现在就下」→ pending_action.type=run_m4（confirmed=true）

【AutoTune】
- 自动调参/搜最优阈值/自适应 → adaptive_mode=true
- 「跑 AutoTune/开始调参」→ 另加 pending_action.type=run_autotune（confirmed=true）
- reference_id 从【数据集资产目录】选取，缺则追问；objective: iou | f1 | iou_f1

【路径】
- 用户给出盘符路径 → 写入对应 root_dir/mask_root/final_root/model_path 等键

【是否立即运行 · 关键判别】
含以下动词 → 通常要 pending_action：跑、执行、开始、启动、下载、推理、合成、调参、来一轮
仅含以下 → 通常**不要** pending_action：改成、设为、打开、关闭、切换、调到、看看（仅地图）

【端到端潮滩分析 Workflow】
- 「分析当前 AOI 的 2024 年潮滩，和 2022 年比较变化，有真值就评价精度，生成报告」
  → analyze_workflow(target_year=2024, baseline_year=2022)（need_e1/need_m5 省略=有条件才做）
- 用户明确要「评价精度」→ need_e1=true；「不要精度评价」→ skip_e1=true
- 用户明确要「变化检测/M5」→ need_m5=true；「不要M5」→ skip_m5=true
- 确认后 → confirm_workflow（一次确认，绝不逐个手动拼装）

═══════════════════════════════════════
第四步 · 差量更新铁律
═══════════════════════════════════════
- JSON 中**只写用户本轮明确提到或可从指代推断的字段**
- 未提及的参数：**省略键**或 null，严禁擅自填默认数字
- 缺关键信息无法安全执行时：**先追问**（task、prob、reference_id、ROI 日期等）

═══════════════════════════════════════
第五步 · 典型多意图句式（必须一次工具搞定）
═══════════════════════════════════════
① 「深度学习跑24zhejiang，5%两次，开M5关E1，开始」
   → selected_task, run_mode=dl, prob_th=0.05, min_cnt=2, m5_enabled=true, e1_enabled=false, run_pipeline(confirmed=true)
② 「指数法跑一下，别的按侧栏」→ run_mode=index, run_pipeline(confirmed=true)（prob/cnt 省略）
③ 「切下载，云量15，2020年6月，启动」→ workflow_tab, m4_cloud=15, 日期, run_m4(confirmed=true)
④ 「看看钱塘江然后跑当前任务」→ map + run_pipeline(confirmed=true)（task 取自快照）
⑤ 「E1打开参考2022，先别跑」→ e1_enabled, e1_reference，**无** pending_action

═══════════════════════════════════════
禁止事项
═══════════════════════════════════════
- 只口头说「已定位/已开始」却不调用工具
- 向用户解释 JSON/暗号/协议细节
- 纯地理知识问答时误触发跑图
- 任务不在硬盘列表时假装能跑"""


agent_executor = create_react_agent(llm, tools)


def _percentile_stretch_to_uint8(arr: np.ndarray, valid_mask: np.ndarray = None) -> np.ndarray:
    """Convert an arbitrary numeric array to uint8 using robust percentile stretch."""
    out = np.zeros(arr.shape, dtype=np.uint8)
    for c in range(arr.shape[-1]):
        ch = arr[..., c].astype(np.float32)
        finite = np.isfinite(ch)
        if valid_mask is not None:
            finite = finite & valid_mask
        if not finite.any():
            continue
        lo = np.percentile(ch[finite], 2)
        hi = np.percentile(ch[finite], 98)
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo = np.min(ch[finite])
            hi = np.max(ch[finite])
        if hi <= lo:
            out[..., c] = 0
            continue
        norm = (ch - lo) / (hi - lo)
        norm = np.clip(norm, 0.0, 1.0)
        norm = np.where(np.isfinite(norm), norm, 0.0)
        out[..., c] = (norm * 255.0).astype(np.uint8)
    return out


def _is_tiff_path(image_path: str) -> bool:
    return os.path.splitext(image_path)[1].lower() in (".tif", ".tiff")


def _needs_png_in_auto(image_path: str) -> bool:
    if not _is_tiff_path(image_path):
        return False
    try:
        size_mb = os.path.getsize(image_path) / (1024 * 1024)
    except OSError:
        size_mb = 0.0
    return size_mb >= _tiff_auto_png_mb


def _extract_geotiff_meta_text(image_path: str) -> str:
    """Return compact geospatial metadata text for model context."""
    if not _is_tiff_path(image_path):
        return ""
    try:
        import rasterio

        with rasterio.open(image_path) as ds:
            bounds = ds.bounds
            compress = ds.profile.get("compress", "none")
            tiled = bool(ds.profile.get("tiled", False))
            blockx = ds.profile.get("blockxsize")
            blocky = ds.profile.get("blockysize")
            xres, yres = ds.res if ds.res else (None, None)
            finite_ratio = None
            try:
                sample = ds.read(list(range(1, min(ds.count, 3) + 1)))
                finite_ratio = float(np.isfinite(sample).all(axis=0).mean())
            except Exception:
                finite_ratio = None
            return (
                "[GeoTIFF metadata]\n"
                f"- bands: {ds.count}\n"
                f"- size: {ds.width}x{ds.height}\n"
                f"- dtype: {ds.dtypes[0] if ds.dtypes else 'unknown'}\n"
                f"- crs: {ds.crs}\n"
                f"- resolution: x={xres}, y={yres}\n"
                f"- nodata: {ds.nodata}\n"
                f"- bounds: left={bounds.left:.6f}, bottom={bounds.bottom:.6f}, "
                f"right={bounds.right:.6f}, top={bounds.top:.6f}\n"
                f"- compression: {compress}\n"
                f"- tiled: {tiled}\n"
                + (f"- block_size: {blockx}x{blocky}\n" if blockx and blocky else "")
                + (f"- finite_pixel_ratio: {finite_ratio:.4f}\n" if finite_ratio is not None else "")
            )
    except Exception as exc:
        return f"[GeoTIFF metadata unavailable: {exc}]"


def _estimate_finite_pixel_ratio(image_path: str, sample_max_side: int = 1024) -> float:
    """Estimate finite pixel ratio (across first up to 3 bands) on a sampled grid."""
    if not _is_tiff_path(image_path):
        return 1.0
    try:
        import rasterio
        from rasterio.enums import Resampling

        with rasterio.open(image_path) as ds:
            bands = list(range(1, min(ds.count, 3) + 1))
            out_h = min(ds.height, sample_max_side)
            out_w = min(ds.width, sample_max_side)
            sample = ds.read(
                bands,
                out_shape=(len(bands), out_h, out_w),
                resampling=Resampling.nearest,
                masked=False,
            )
        return float(np.isfinite(sample).all(axis=0).mean())
    except Exception:
        return 1.0


def _build_image_data_url(image_path: str, force_png_for_tiff: bool = False) -> str:
    """Build a data URL for VLM; TIFF can be sent raw or converted to PNG preview."""
    ext = os.path.splitext(image_path)[1].lower()

    if ext in (".tif", ".tiff") and not force_png_for_tiff:
        with open(image_path, "rb") as img_file:
            img_b64 = base64.b64encode(img_file.read()).decode("utf-8")
        return f"data:image/tiff;base64,{img_b64}"

    if ext in (".tif", ".tiff") and force_png_for_tiff:
        try:
            import rasterio
            from PIL import Image

            with rasterio.open(image_path) as ds:
                data = ds.read(masked=True)
            if data.size == 0:
                raise ValueError("empty raster data")

            if data.shape[0] >= 3:
                rgb = np.moveaxis(data[:3, :, :], 0, -1)
            elif data.shape[0] == 2:
                two = np.moveaxis(data[:2, :, :], 0, -1)
                rgb = np.concatenate([two, two[..., 1:2]], axis=-1)
            else:
                one = data[0]
                rgb = np.repeat(one[:, :, None], 3, axis=2)

            if np.ma.isMaskedArray(rgb):
                rgb_plain = np.ma.filled(rgb, np.nan)
                valid_mask = (~np.ma.getmaskarray(rgb).any(axis=2)) & np.isfinite(rgb_plain).all(axis=2)
            else:
                valid_mask = np.isfinite(rgb).all(axis=2)
                rgb_plain = rgb

            if valid_mask is not None and valid_mask.any():
                valid_ratio = float(valid_mask.mean())
                if valid_ratio < 0.70:
                    ys, xs = np.where(valid_mask)
                    y0, y1 = ys.min(), ys.max()
                    x0, x1 = xs.min(), xs.max()
                    pad = 16
                    y0 = max(0, y0 - pad)
                    x0 = max(0, x0 - pad)
                    y1 = min(rgb_plain.shape[0] - 1, y1 + pad)
                    x1 = min(rgb_plain.shape[1] - 1, x1 + pad)
                    rgb_plain = rgb_plain[y0 : y1 + 1, x0 : x1 + 1, :]
                    valid_mask = valid_mask[y0 : y1 + 1, x0 : x1 + 1]

            rgb_u8 = _percentile_stretch_to_uint8(rgb_plain, valid_mask=valid_mask)
            img = Image.fromarray(rgb_u8, mode="RGB")
            if _vlm_max_side > 0:
                img.thumbnail((_vlm_max_side, _vlm_max_side), Image.Resampling.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('utf-8')}"
        except Exception as conv_err:
            raise RuntimeError(f"TIFF conversion failed: {conv_err}") from conv_err

    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "image/jpeg")
    with open(image_path, "rb") as img_file:
        img_b64 = base64.b64encode(img_file.read()).decode("utf-8")
    return f"data:{mime};base64,{img_b64}"


def chat_with_vlm(
    user_input: str,
    chat_history: list,
    image_path: str = None,
    available_tasks: list = None,
    dataset_catalog_text: str = None,
    sidebar_context: str = None,
    capability_summary: str = None,
) -> str:
    """处理对话，完美支持多模态视觉能力与物理感知"""

    task_list_str = ", ".join(available_tasks) if available_tasks else "目前硬盘中没有任何数据"
    dynamic_prompt = system_prompt_base + f"\n\n【🚨 硬盘可用任务目录（唯一合法 task 名来源）】\n{task_list_str}"
    if sidebar_context and sidebar_context.strip():
        dynamic_prompt += "\n\n" + sidebar_context.strip()
    if dataset_catalog_text and dataset_catalog_text.strip():
        dynamic_prompt += "\n\n【数据集资产目录 · AutoTune reference_id 从此选取】\n" + dataset_catalog_text.strip()
    if capability_summary and capability_summary.strip():
        dynamic_prompt += (
            "\n\n【能力状态（只读参考，每轮会话快照一次）】\n"
            + capability_summary.strip()
            + "\n铁律：已 BLOCKED/UNKNOWN 的能力不得声称执行成功；CONDITIONAL 的能力需先确认前置条件。"
        )

    messages = [SystemMessage(content=dynamic_prompt)]

    for msg in chat_history[:-1]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    if image_path and os.path.exists(image_path):
        if _is_tiff_path(image_path):
            finite_ratio_est = _estimate_finite_pixel_ratio(image_path)
            if finite_ratio_est <= 0.0:
                return (
                    "该 GeoTIFF 在采样检测中未发现任何有效像素（均为 NaN/Inf），"
                    "因此无法进行地物解译。请检查数据源、导出流程或尝试提供未损坏的影像。"
                )

        image_text = user_input
        if _attach_geo_meta:
            meta_text = _extract_geotiff_meta_text(image_path)
            if meta_text:
                image_text = f"{user_input}\n\n{meta_text}"

        use_force_png = False
        if _is_tiff_path(image_path):
            if _tiff_mode == "png":
                use_force_png = True
            elif _tiff_mode == "native":
                use_force_png = False
            else:
                use_force_png = _needs_png_in_auto(image_path)

        image_data_url = _build_image_data_url(image_path, force_png_for_tiff=use_force_png)
        multimodal_content = [
            {"type": "text", "text": image_text},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
        messages.append({"role": "user", "content": multimodal_content})
    else:
        messages.append({"role": "user", "content": user_input})

    try:
        response = agent_executor.invoke({"messages": messages})
    except Exception as e:
        if (
            image_path
            and os.path.exists(image_path)
            and _is_tiff_path(image_path)
            and _tiff_mode == "auto"
            and "image format is illegal" in str(e).lower()
        ):
            image_text = user_input
            if _attach_geo_meta:
                meta_text = _extract_geotiff_meta_text(image_path)
                if meta_text:
                    image_text = f"{user_input}\n\n{meta_text}"
            retry_url = _build_image_data_url(image_path, force_png_for_tiff=True)
            retry_messages = messages[:-1] + [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": image_text},
                        {"type": "image_url", "image_url": {"url": retry_url}},
                    ],
                }
            ]
            response = agent_executor.invoke({"messages": retry_messages})
        else:
            raise
    output_messages = response["messages"]
    final_reply = output_messages[-1].content

    for msg in output_messages:
        if msg.type == "tool" and (
            "[SYSTEM_COMMAND_JSON]" in str(msg.content)
            or "COMMAND_RUN_PIPELINE" in str(msg.content)
            or "COMMAND_UPDATE_MAP" in str(msg.content)
        ):
            return final_reply + "\n" + str(msg.content)

    if "COMMAND_SEARCH_KNOWLEDGE_BASE" in final_reply:
        print("\n🚨 [后台监控] 截获到大模型的查库暗号！正在悄悄执行检索...")

        match = re.search(r"COMMAND_SEARCH_KNOWLEDGE_BASE[\|:：\s]*(.*)", final_reply)
        keywords = match.group(1).strip() if match and match.group(1).strip() else user_input
        keywords = keywords.strip("。.,!！")

        retrieved_info = search_knowledge_base.invoke(keywords)
        print(f"✅ [后台监控] 查库完成，正在强迫大模型结合文献重新作答...\n")

        messages.append({"role": "assistant", "content": final_reply})
        messages.append(
            {
                "role": "user",
                "content": f"系统已自动从后台为您查阅了本地数据库，检索结果如下：\n{retrieved_info}\n请仔细阅读上述文献，直接回答我最初的问题。回答必须专业严谨，且在文末标注'(来源: XXX)'。严禁在本次回答中再输出 COMMAND 暗号。",
            }
        )

        response_phase2 = agent_executor.invoke({"messages": messages})
        return response_phase2["messages"][-1].content

    return final_reply