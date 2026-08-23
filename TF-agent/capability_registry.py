# -*- coding: utf-8 -*-
"""动态能力状态注册表（B 阶段）。

- 9 项能力：map_navigation / map_layer_display / deep_learning_inference / gee_download /
  e1_quality_evaluation / m5_change_detection / autotune / pdf_report / knowledge_search。
- cheap（TTL 10s）：导入探测 / 路径存在 / 环境键存在（只查键名不读值）。
- expensive（TTL 60s）：模型文件元信息冒烟 / 字体探测等。
- 缓存不落盘；不存 token/密钥/绝对路径值；检查异常 → UNKNOWN（仅 message，无堆栈）。
- Agent 不可改写状态：snapshot_for_agent() 返回白名单拷贝。
"""
from __future__ import annotations

import importlib.util
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

AVAILABLE = "AVAILABLE"
CONDITIONAL = "CONDITIONAL"
BLOCKED = "BLOCKED"
UNAVAILABLE = "UNAVAILABLE"
UNKNOWN = "UNKNOWN"

STATUS_ORDER = (AVAILABLE, CONDITIONAL, BLOCKED, UNAVAILABLE, UNKNOWN)

_CHEAP_TTL_SEC = 10.0
_EXPENSIVE_TTL_SEC = 60.0

_SENSITIVE_KEY_SUBSTRINGS = ("token", "secret", "password", "api_key", "ion")
_SENSITIVE_VALUE_SUBSTRINGS = ("token=", "key=")
_ABSOLUTE_PATH_RE = re.compile(
    r"(?:^|[\s(=])(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|private|tmp|var|opt|Volumes|mnt|srv|workspace|app|data)/)"
)


@dataclass
class CapabilityStatus:
    capability_id: str          # 能力 id
    label: str                  # 中文名
    status: str = UNKNOWN       # AVAILABLE / CONDITIONAL / BLOCKED / UNAVAILABLE / UNKNOWN
    summary: str = ""           # 一句话结论（人读）
    requirements: List[str] = field(default_factory=list)   # 依赖项（不含绝对路径值）
    blockers: List[str] = field(default_factory=list)       # 阻断原因（可读，不含绝对路径）
    warnings: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)  # 校验证据（布尔/版本，不存 token/路径值）
    recommended_actions: List[str] = field(default_factory=list)
    validation_level: str = "static"  # static=依赖/路径探测；runtime=真实任务验证
    checked_at: str = ""
    expires_at: str = ""

    def to_summary_line(self) -> str:
        return f"{self.capability_id}({self.status})"


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _module_importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _path_ok(p: Any) -> bool:
    if not p:
        return False
    try:
        return os.path.exists(os.path.normpath(str(p)))
    except (TypeError, ValueError):
        return False


def _env_key_present(key: str) -> bool:
    """只查键名是否存在，绝不读值（避免密钥进入缓存/日志）。"""
    try:
        return key in os.environ
    except (TypeError, ValueError):
        return False


def _safe_error_summary(error: BaseException, *, limit: int = 240) -> str:
    """Return a bounded, path/credential-free check failure summary."""
    text = str(error or "").strip()
    text = re.sub(
        r"(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|private|tmp|var|opt|Volumes|mnt|srv|workspace|app|data)/)[^\s,;，；)）]+",
        "<local-path>",
        text,
    )
    text = re.sub(
        r"(?i)(?:api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[^\s,;，；]+",
        "<redacted>",
        text,
    )
    text = re.sub(r"(?i)(https?://)([^/@\s]+):([^/@\s]+)@", r"\1<redacted>@", text)
    return text[:limit] or type(error).__name__


def _sanitize_evidence(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """剔除可能含敏感值的键（token/key/secret）与绝对路径字符串值。"""
    clean: Dict[str, Any] = {}
    for k, v in (evidence or {}).items():
        kl = str(k).lower()
        if any(s in kl for s in _SENSITIVE_KEY_SUBSTRINGS):
            continue
        if isinstance(v, str):
            if any(s in v for s in _SENSITIVE_VALUE_SUBSTRINGS):
                continue
            if _ABSOLUTE_PATH_RE.search(v):
                continue
        clean[k] = v
    return clean


class CapabilityRegistry:
    """能力注册表：注册检查函数 → 分层 TTL 缓存 → 白名单快照。

    context: 消费方提供的只读上下文（model_path / task / 目录等）。
    now_fn:  可注入时钟（测试用）。
    """

    def __init__(
        self,
        context: Optional[Dict[str, Any]] = None,
        now_fn: Optional[Callable[[], float]] = None,
    ):
        self._context: Dict[str, Any] = dict(context or {})
        self._now_fn: Callable[[], float] = now_fn or time.time
        self._checks: Dict[str, Tuple[str, Callable[[Dict[str, Any]], CapabilityStatus], str]] = {}
        self._cache: Dict[str, CapabilityStatus] = {}
        self._cached_at: Dict[str, float] = {}
        self._runtime_verified: set[str] = set()
        self._register_defaults()

    # ---- 注册 ----

    def register(
        self,
        capability_id: str,
        label: str,
        tier: str,
        check_fn: Callable[[Dict[str, Any]], CapabilityStatus],
    ) -> None:
        if tier not in ("cheap", "expensive"):
            tier = "cheap"
        self._checks[capability_id] = (label, check_fn, tier)

    def _register_defaults(self) -> None:
        self.register("map_navigation", "地图导航", "cheap", self._check_map_navigation)
        self.register("map_layer_display", "地图图层", "cheap", self._check_map_layer_display)
        self.register("deep_learning_inference", "潮滩智能提取", "expensive", self._check_deep_learning)
        self.register("gee_download", "获取卫星影像", "cheap", self._check_gee_download)
        self.register("e1_quality_evaluation", "潮滩精度评价", "expensive", self._check_e1_evaluation)
        self.register("m5_change_detection", "潮滩变化分析", "expensive", self._check_m5_detection)
        self.register("autotune", "参数自动优化", "expensive", self._check_autotune)
        self.register("pdf_report", "成果报告", "cheap", self._check_pdf_report)
        self.register("knowledge_search", "知识库检索", "cheap", self._check_knowledge_search)

    # ---- 查询 ----

    def ids(self) -> List[str]:
        return list(self._checks)

    def labels(self) -> Dict[str, str]:
        return {cid: self._checks[cid][0] for cid in self._checks}

    def tiers(self) -> Dict[str, str]:
        return {cid: self._checks[cid][2] for cid in self._checks}

    def check(self, capability_id: str, force: bool = False) -> CapabilityStatus:
        entry = self._checks.get(capability_id)
        if entry is None:
            return CapabilityStatus(
                capability_id=capability_id,
                label=capability_id,
                status=UNKNOWN,
                summary=f"未注册的能力: {capability_id}",
                checked_at=_now_str(),
                expires_at=_now_str(),
            )
        label, check_fn, tier = entry
        now = self._now_fn()
        ttl = _CHEAP_TTL_SEC if tier == "cheap" else _EXPENSIVE_TTL_SEC
        cached = self._cache.get(capability_id)
        if cached is not None and not force:
            cached_at = self._cached_at.get(capability_id, 0.0)
            if (now - cached_at) < ttl:
                return cached

        try:
            st = check_fn(self._context)
        except Exception as e:  # noqa: BLE001 —— 检查异常统一转 UNKNOWN，吞掉堆栈
            st = CapabilityStatus(
                capability_id=capability_id,
                label=label,
                status=UNKNOWN,
                summary=f"能力检查异常: {_safe_error_summary(e)}",
                checked_at=_now_str(),
                expires_at=_now_str(),
            )
        st.capability_id = capability_id
        st.label = label
        if not st.checked_at:
            st.checked_at = _now_str()
        if not st.expires_at:
            st.expires_at = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(now + ttl)
            )
        st.evidence = _sanitize_evidence(st.evidence)
        st.validation_level = "runtime" if capability_id in self._runtime_verified else "static"
        self._cache[capability_id] = st
        self._cached_at[capability_id] = now
        return st

    def check_all(self, force: bool = False) -> Dict[str, CapabilityStatus]:
        return {cid: self.check(cid, force=force) for cid in self.ids()}

    def statuses(self) -> Dict[str, str]:
        return {cid: self.check(cid).status for cid in self.ids()}

    # ---- 失效 ----

    def bump(self) -> None:
        """全部失效（任务切换 / model_path 变化 / 手动刷新）。"""
        self._cache.clear()
        self._cached_at.clear()

    def invalidate(self, capability_id: str) -> None:
        self._cache.pop(capability_id, None)
        self._cached_at.pop(capability_id, None)

    def mark_runtime_verified(self, capability_id: str) -> None:
        """由真实执行闭环在验证通过后调用，不允许 Agent prompt 自行提升状态。"""
        if capability_id in self._checks:
            self._runtime_verified.add(capability_id)
            self.invalidate(capability_id)

    def clear_runtime_verification(self) -> None:
        self._runtime_verified.clear()
        self.bump()

    # ---- 白名单快照（注入 Copilot / 展示） ----

    def snapshot_for_agent(self) -> Dict[str, Dict[str, str]]:
        """白名单快照：仅 id → {status, summary}；不含路径/密钥/证据。"""
        snap: Dict[str, Dict[str, str]] = {}
        for cid in self.ids():
            st = self.check(cid)
            snap[cid] = {"status": st.status, "summary": st.summary}
        return snap

    def grouped_summary(self) -> Dict[str, List[str]]:
        """按状态分组的能力 id 列表（AVAILABLE/CONDITIONAL/BLOCKED/UNAVAILABLE/UNKNOWN）。"""
        groups: Dict[str, List[str]] = {s: [] for s in STATUS_ORDER}
        for cid, status in self.statuses().items():
            groups.setdefault(status, []).append(cid)
        return groups

    # ---- 各能力检查（默认实现，消费方可覆盖/补充 context） ----

    def _check_map_navigation(self, ctx: Dict[str, Any]) -> CapabilityStatus:
        reqs = ["globe 引擎可导入", "本地地球服务可启动"]
        ok_engine = _module_importable("globe_engine") and _module_importable("globe_server")
        if not ok_engine:
            return CapabilityStatus(
                capability_id="map_navigation", label="地图导航", status=BLOCKED,
                summary="三维地球引擎不可用",
                requirements=reqs, blockers=["globe 引擎导入失败"],
                recommended_actions=["检查 TF-agent 目录与依赖安装"],
            )
        return CapabilityStatus(
            capability_id="map_navigation", label="地图导航", status=AVAILABLE,
            summary="三维地球导航可用",
            requirements=reqs,
            evidence={"engine_importable": True},
        )

    def _check_map_layer_display(self, ctx: Dict[str, Any]) -> CapabilityStatus:
        reqs = ["globe 引擎可导入", "图层渲染路径可用"]
        ok_engine = _module_importable("globe_engine")
        if not ok_engine:
            return CapabilityStatus(
                capability_id="map_layer_display", label="地图图层展示", status=BLOCKED,
                summary="图层渲染引擎不可用",
                requirements=reqs, blockers=["globe 引擎导入失败"],
                recommended_actions=["检查 TF-agent 目录与依赖安装"],
            )
        return CapabilityStatus(
            capability_id="map_layer_display", label="地图图层展示", status=AVAILABLE,
            summary="成果图层渲染可用",
            requirements=reqs,
            evidence={"engine_importable": True},
        )

    def _check_deep_learning(self, ctx: Dict[str, Any]) -> CapabilityStatus:
        model_path = ctx.get("model_path")
        reqs = ["模型文件存在", "torch 可导入"]
        if not model_path:
            return CapabilityStatus(
                capability_id="deep_learning_inference", label="深度学习推理", status=UNAVAILABLE,
                summary="未配置模型路径",
                requirements=reqs, blockers=["缺少 model_path"],
                recommended_actions=["在侧栏配置模型文件后重试"],
            )
        if not _path_ok(model_path):
            return CapabilityStatus(
                capability_id="deep_learning_inference", label="深度学习推理", status=BLOCKED,
                summary="模型文件不存在",
                requirements=reqs, blockers=["模型文件不存在"],
                recommended_actions=["检查模型路径配置"],
            )
        if not _module_importable("torch"):
            return CapabilityStatus(
                capability_id="deep_learning_inference", label="深度学习推理", status=BLOCKED,
                summary="深度学习依赖不可用",
                requirements=reqs, blockers=["torch 不可导入"],
                recommended_actions=["安装 PyTorch"],
            )
        return CapabilityStatus(
            capability_id="deep_learning_inference", label="深度学习推理", status=AVAILABLE,
            summary="推理能力可用",
            requirements=reqs,
            evidence={"model_present": True, "torch_importable": True},
        )

    def _check_gee_download(self, ctx: Dict[str, Any]) -> CapabilityStatus:
        reqs = ["GEE 项目配置（环境/凭据）", "ee 包可导入"]
        has_ee = _module_importable("ee")
        if not has_ee:
            return CapabilityStatus(
                capability_id="gee_download", label="GEE 遥感下载", status=BLOCKED,
                summary="影像获取依赖不可用",
                requirements=reqs, blockers=["ee 包不可导入"],
                recommended_actions=["安装 earthengine-api（提供 ee 包）"],
            )
        # 项目解析：env → 凭据文件 → credentials JSON（与 m4_engine._resolve_ee_project 同语义）
        project = None
        for key in ("EE_PROJECT", "GOOGLE_CLOUD_PROJECT", "EARTHENGINE_PROJECT"):
            v = os.environ.get(key)
            if v and str(v).strip():
                project = str(v).strip()
                break
        if not project:
            try:
                cred_dir = os.path.join(os.path.expanduser("~"), ".config", "earthengine")
                for fname in ("project", "project_id"):
                    p = os.path.join(cred_dir, fname)
                    if os.path.isfile(p):
                        with open(p, "r", encoding="utf-8") as f:
                            v = f.read().strip()
                        if v:
                            project = v
                            break
            except Exception:  # noqa: BLE001
                project = None
        if not project:
            try:
                cred_path = os.path.join(os.path.expanduser("~"), ".config",
                                         "earthengine", "credentials")
                if os.path.isfile(cred_path):
                    import json
                    with open(cred_path, "r", encoding="utf-8") as f:
                        cred = json.load(f)
                    for key in ("project", "project_id", "cloud_project"):
                        if cred.get(key):
                            project = str(cred[key])
                            break
            except Exception:  # noqa: BLE001
                project = None
        if not project:
            return CapabilityStatus(
                capability_id="gee_download", label="GEE 遥感下载", status=UNAVAILABLE,
                summary="未配置影像获取项目（env / ~/.config/earthengine）",
                requirements=reqs, blockers=["缺少 GEE 项目配置"],
                recommended_actions=["配置 GEE_PROJECT/EE_PROJECT 环境变量或 earthengine 凭据后重启"],
            )
        return CapabilityStatus(
            capability_id="gee_download", label="GEE 遥感下载", status=CONDITIONAL,
            summary="影像获取已配置，需项目与网络代理可用",
            requirements=reqs,
            evidence={"gee_project_configured": True, "ee_importable": True},
            warnings=["下载需 GEE 认证与网络可达"],
        )

    def _check_e1_evaluation(self, ctx: Dict[str, Any]) -> CapabilityStatus:
        reqs = ["e1_engine 可导入", "E1 数据集根目录", "参考产品已配置"]
        if not _module_importable("e1_engine"):
            return CapabilityStatus(
                capability_id="e1_quality_evaluation", label="E1 质量评估", status=BLOCKED,
                summary="精度评价引擎不可用",
                requirements=reqs, blockers=["e1_engine 不可导入"],
                recommended_actions=["检查 E1 引擎与研究脚本依赖"],
            )
        data_root = str(ctx.get("e1_data_root") or "")
        reference = str(ctx.get("e1_reference") or "")
        if not data_root or not _path_ok(data_root) or not reference:
            return CapabilityStatus(
                capability_id="e1_quality_evaluation", label="E1 质量评估", status=CONDITIONAL,
                summary="精度评价引擎可用，但尚未配置数据集或参考产品",
                requirements=reqs, blockers=["缺少 E1 数据集根目录或 reference"],
                recommended_actions=["配置 E1_DATA_ROOT 与参考产品后再执行"],
                evidence={"engine_importable": True, "dataset_configured": bool(data_root), "reference_configured": bool(reference)},
            )
        return CapabilityStatus(
            capability_id="e1_quality_evaluation", label="E1 质量评估", status=AVAILABLE,
            summary="精度评价前置条件已配置",
            requirements=reqs,
            evidence={"engine_importable": True, "dataset_configured": True, "reference_configured": True},
        )

    def _check_m5_detection(self, ctx: Dict[str, Any]) -> CapabilityStatus:
        reqs = ["m5_engine 可导入", "同区域历史基线或已登记成果"]
        if not _module_importable("m5_engine"):
            return CapabilityStatus(
                capability_id="m5_change_detection", label="M5 时空变化检测", status=BLOCKED,
                summary="变化分析引擎不可用",
                requirements=reqs, blockers=["m5_engine 不可导入"],
                recommended_actions=["检查 M5 引擎与空间依赖"],
            )
        baseline = str(ctx.get("m5_baseline_shp") or "")
        if not baseline or not _path_ok(baseline):
            return CapabilityStatus(
                capability_id="m5_change_detection", label="M5 时空变化检测", status=CONDITIONAL,
                summary="变化分析引擎可用，需运行时解析同区域历史基线",
                requirements=reqs, blockers=["当前未提供可验证基线"],
                recommended_actions=["配置历史基线 SHP 或先登记历史成果"],
                evidence={"engine_importable": True, "baseline_configured": bool(baseline)},
            )
        return CapabilityStatus(
            capability_id="m5_change_detection", label="M5 时空变化检测", status=AVAILABLE,
            summary="变化分析前置条件已配置",
            requirements=reqs,
            evidence={"engine_importable": True, "baseline_configured": True},
        )

    def _check_autotune(self, ctx: Dict[str, Any]) -> CapabilityStatus:
        script = ctx.get("autotune_script")
        reqs = ["auto_tune 脚本存在"]
        if not script or not _path_ok(script):
            return CapabilityStatus(
                capability_id="autotune", label="自动调参", status=BLOCKED,
                summary="自动调参脚本不可用",
                requirements=reqs, blockers=["脚本缺失"],
                recommended_actions=["检查 auto_tune 脚本"],
            )
        return CapabilityStatus(
            capability_id="autotune", label="自动调参", status=AVAILABLE,
            summary="自动调参可用",
            requirements=reqs,
            evidence={"script_present": True},
        )

    def _check_pdf_report(self, ctx: Dict[str, Any]) -> CapabilityStatus:
        reqs = ["reportlab 可导入"]
        has_reportlab = _module_importable("reportlab")
        if not has_reportlab:
            return CapabilityStatus(
                capability_id="pdf_report", label="PDF 报告", status=BLOCKED,
                summary="报告生成依赖不可用",
                requirements=reqs, blockers=["reportlab 不可导入"],
                recommended_actions=["安装 reportlab"],
            )
        # 中文字体探测：缺字体时有降级（CONDITIONAL），不阻断
        font_ok = _find_chinese_font()
        st = CapabilityStatus(
            capability_id="pdf_report", label="PDF 报告", status=AVAILABLE,
            summary="报告生成可用",
            requirements=reqs,
            evidence={"reportlab_importable": True, "chinese_font_found": bool(font_ok)},
        )
        if not font_ok:
            st.status = CONDITIONAL
            st.summary = "报告生成可用（缺中文字体，中文可能显示异常）"
            st.warnings = ["未检测到中文字体（msyh.ttc 等），建议安装以正常显示中文"]
            st.recommended_actions = ["安装中文字体（如微软雅黑 msyh.ttc）"]
        return st

    def _check_knowledge_search(self, ctx: Dict[str, Any]) -> CapabilityStatus:
        kb_dir = ctx.get("knowledge_db_dir")
        reqs = ["知识库目录存在", "chromadb 可导入"]
        has_chroma = _module_importable("chromadb")
        if not kb_dir or not _path_ok(kb_dir):
            return CapabilityStatus(
                capability_id="knowledge_search", label="知识库检索", status=BLOCKED,
                summary="知识库目录不可用",
                requirements=reqs, blockers=["知识库目录缺失"],
                recommended_actions=["配置 knowledge_db_dir"],
            )
        if not has_chroma:
            return CapabilityStatus(
                capability_id="knowledge_search", label="知识库检索", status=BLOCKED,
                summary="知识库检索依赖不可用",
                requirements=reqs, blockers=["chromadb 不可导入"],
                recommended_actions=["安装 chromadb"],
            )
        return CapabilityStatus(
            capability_id="knowledge_search", label="知识库检索", status=AVAILABLE,
            summary="知识库检索可用",
            requirements=reqs,
            evidence={"chromadb_importable": True},
        )


def _find_chinese_font() -> Optional[str]:
    """探测常见中文字体路径（仅探测存在性，返回文件名级信息，不泄漏绝对路径给摘要）。"""
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for p in candidates:
        if os.path.exists(p):
            return os.path.basename(p)
    return None


def build_context(
    *,
    app_dir: Optional[str] = None,
    model_path: Any = "",
    task: Any = "",
    knowledge_db_dir: Optional[str] = None,
    autotune_script: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """构造 UI、Agent 与 Workflow 共用的能力检查上下文。

    返回值仅供本地检查使用；快照层仍只暴露状态和摘要，不向模型回声路径。
    """
    root = os.path.abspath(str(app_dir or os.path.dirname(__file__)))
    default_kb_dir = os.path.normpath(os.path.join(root, "..", "rs_knowledge_db"))
    try:
        # Keep capability checks on the same resolver used by Agent queries
        # and the ingestion CLI; the fallback preserves the historical repo
        # layout when no environment override is configured.
        from knowledge_store import knowledge_db_path

        resolved_kb_dir = knowledge_db_path(default_kb_dir)
    except Exception:  # noqa: BLE001
        resolved_kb_dir = default_kb_dir
    context: Dict[str, Any] = {
        "model_path": model_path or "",
        "autotune_script": autotune_script or os.path.join(root, "auto_tune.py"),
        "knowledge_db_dir": knowledge_db_dir or resolved_kb_dir,
        "e1_data_root": extra.pop("e1_data_root", None) or os.environ.get("E1_DATA_ROOT"),
        "e1_reference": extra.pop("e1_reference", None) or os.environ.get("E1_REFERENCE", "师姐_2020"),
        "m5_baseline_shp": extra.pop("m5_baseline_shp", None) or os.environ.get("M5_BASELINE_SHP"),
        "task": task or "",
    }
    context.update(extra)
    return context


def default_registry(context: Optional[Dict[str, Any]] = None) -> CapabilityRegistry:
    return CapabilityRegistry(context=context)
