"""
参考 / 开源数据集资产库（与 assets_registry.json 的「模型 Final 成果」分离）。

设计目标：
  - 师姐全国 SHP：一条记录 = 一年份主 .shp（侧文件同目录），标注 coverage_scale=national。
  - 后续开源 TIF：可按「景」登记 coverage_scale=scene，并逐步补全 bounds / 与任务区关系。
  - Agent 通过 build_dataset_catalog_for_agent() 注入系统上下文，避免把「全国真值」与「单景数据」混为一谈。

师姐全国潮滩 SHP 默认物理目录：YYnet/DATA/sqq_TF_20-25/（命名 china_tidal_flat_projected_{year}.shp）。
一键登记：在 YYnet 目录执行  python dataset_assets.py seed-advisor  （缺 2021/2023 等年份会自动 skip）

环境变量（可选）：
  DATASET_ASSETS_REGISTRY_PATH — 自定义 registry JSON 路径（默认与本文件同目录下的 dataset_assets_registry.json）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import tempfile
from typing import Any, Dict, List, Optional

_VALID_SOURCES = frozenset({"advisor", "open", "other"})
_VALID_ROLES = frozenset(
    {
        "reference_truth",
        "benchmark",
        "auxiliary",
        "prediction",
        "other",
    }
)
_VALID_FORMATS = frozenset({"shapefile", "geotiff", "geojson", "other"})
_VALID_COVERAGE = frozenset({"national", "regional", "scene", "unknown"})

_DEFAULT_REGISTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset_assets_registry.json")


def registry_path() -> str:
    return os.environ.get("DATASET_ASSETS_REGISTRY_PATH", _DEFAULT_REGISTRY)


def _now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_registry() -> Dict[str, Dict[str, Any]]:
    p = registry_path()
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if not all(isinstance(entry, dict) for entry in data.values()):
                    _preserve_corrupt_registry(p)
                    raise ValueError("数据集资产注册表记录结构无效；原文件已保留，已停止写入。")
                return data
            _preserve_corrupt_registry(p)
            raise ValueError("数据集资产注册表顶层结构无效；原文件已保留，已停止写入。")
        except (json.JSONDecodeError, UnicodeError) as exc:
            _preserve_corrupt_registry(p)
            raise ValueError("数据集资产注册表 JSON 无效；原文件已保留，已停止写入。") from exc
        except OSError:
            # Permission/read failures are not an empty registry; propagate so
            # callers cannot overwrite evidence under a false fallback.
            raise
    return {}


def _preserve_corrupt_registry(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    backup = f"{path}.corrupt-{stamp}"
    suffix = 1
    while os.path.exists(backup):
        backup = f"{path}.corrupt-{stamp}-{suffix}"
        suffix += 1
    try:
        shutil.copy2(path, backup)
    except OSError:
        return None
    return backup


def save_registry(data: Dict[str, Dict[str, Any]]) -> None:
    p = registry_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".dataset_registry_", suffix=".tmp", dir=os.path.dirname(os.path.abspath(p)) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, p)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "_", s, flags=re.UNICODE)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "dataset"


def _abs_path(path: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(path or "")))


def resolve_stored_primary_path(raw: str) -> str:
    """
    解析 registry 中的 primary_path：绝对路径按原样规范化；
    相对路径则相对于 registry JSON 所在目录（默认即 YYnet 根目录）。
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        return _abs_path(raw)
    reg_dir = os.path.dirname(os.path.abspath(registry_path()))
    return _abs_path(os.path.join(reg_dir, raw.replace("/", os.sep)))


def _canonical_primary_path_for_store(resolved_abs: str) -> str:
    """在 YYnet 内的文件存相对路径，便于仓库迁移；否则存绝对路径。"""
    ap = os.path.normpath(os.path.abspath(resolved_abs))
    reg_dir = os.path.dirname(os.path.abspath(registry_path()))
    try:
        rel = os.path.relpath(ap, reg_dir)
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    except ValueError:
        pass
    return ap


def shapefile_sidecars(shp_path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not shp_path or not shp_path.lower().endswith(".shp"):
        return out
    base, _ = os.path.splitext(shp_path)
    for ext in (".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx"):
        p = base + ext
        if os.path.isfile(p):
            out[ext.lstrip(".")] = p
    return out


def validate_entry(entry: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    eid = entry.get("id")
    if not eid or not isinstance(eid, str):
        errs.append("缺少字符串字段 id")
    if entry.get("source") not in _VALID_SOURCES:
        errs.append(f"source 须为 {sorted(_VALID_SOURCES)} 之一")
    if entry.get("format") not in _VALID_FORMATS:
        errs.append(f"format 须为 {sorted(_VALID_FORMATS)} 之一")
    if entry.get("role") not in _VALID_ROLES:
        errs.append(f"role 须为 {sorted(_VALID_ROLES)} 之一")
    cov = entry.get("coverage_scale") or "unknown"
    if cov not in _VALID_COVERAGE:
        errs.append(f"coverage_scale 须为 {sorted(_VALID_COVERAGE)} 之一")
    pp = entry.get("primary_path")
    if not pp:
        errs.append("缺少 primary_path")
    else:
        ap = resolve_stored_primary_path(str(pp))
        if not os.path.isfile(ap):
            errs.append(f"primary_path 不是现有文件: {ap}")
        elif entry.get("format") == "shapefile" and not ap.lower().endswith(".shp"):
            errs.append("format=shapefile 时 primary_path 应指向 .shp")
        elif entry.get("format") == "geotiff" and os.path.splitext(ap)[1].lower() not in (".tif", ".tiff"):
            errs.append("format=geotiff 时 primary_path 建议为 .tif/.tiff")
    return errs


def register_dataset(entry: Dict[str, Any], overwrite: bool = False) -> str:
    entry = dict(entry)
    if not entry.get("id"):
        entry["id"] = _slug(str(entry.get("title") or entry.get("primary_path") or "dataset"))
    eid = str(entry["id"])
    reg = load_registry()
    if eid in reg and not overwrite:
        raise ValueError(f"id 已存在: {eid}（overwrite=True 可覆盖）")

    entry.setdefault("source", "other")
    entry.setdefault("role", "reference_truth")
    entry.setdefault("format", "other")
    entry.setdefault("coverage_scale", "unknown")
    entry.setdefault("geographic_scope", "")
    entry.setdefault("title", eid)
    entry.setdefault("description", "")
    entry.setdefault("license", "")
    entry.setdefault("tags", [])
    entry.setdefault("year", None)
    entry.setdefault("crs", "")
    entry.setdefault("notes", "")
    entry.setdefault("related_task_hints", [])
    entry.setdefault("pairing_note", "")
    entry.setdefault("aliases", [])
    if not isinstance(entry["tags"], list):
        entry["tags"] = [str(entry["tags"])]
    if not isinstance(entry["related_task_hints"], list):
        entry["related_task_hints"] = [str(entry["related_task_hints"])]
    if not isinstance(entry["aliases"], list):
        entry["aliases"] = [str(entry["aliases"])]
    entry["registered_at"] = entry.get("registered_at") or _now_str()

    errs = validate_entry(entry)
    if errs:
        raise ValueError("; ".join(errs))
    resolved = resolve_stored_primary_path(str(entry.get("primary_path", "")))
    entry["primary_path"] = _canonical_primary_path_for_store(resolved)
    if entry.get("format") == "shapefile":
        entry["shapefile_sidecars"] = shapefile_sidecars(resolved)
    reg[eid] = entry
    save_registry(reg)
    return eid


def get_dataset(dataset_id: str) -> Optional[Dict[str, Any]]:
    return load_registry().get(dataset_id)


def get_primary_path(dataset_id: str) -> Optional[str]:
    row = get_dataset(dataset_id)
    if not row:
        return None
    p = resolve_stored_primary_path(str(row.get("primary_path", "")))
    return p if p and os.path.isfile(p) else None


def remove_dataset(dataset_id: str) -> bool:
    reg = load_registry()
    if dataset_id not in reg:
        return False
    del reg[dataset_id]
    save_registry(reg)
    return True


def list_datasets(
    source: Optional[str] = None,
    format: Optional[str] = None,
    role: Optional[str] = None,
    tag: Optional[str] = None,
    require_file_exists: bool = True,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for eid, row in sorted(load_registry().items(), key=lambda x: x[0]):
        r = dict(row)
        r["id"] = eid
        if source and r.get("source") != source:
            continue
        if format and r.get("format") != format:
            continue
        if role and r.get("role") != role:
            continue
        if tag and tag not in (r.get("tags") or []):
            continue
        pp = r.get("primary_path", "")
        rp = resolve_stored_primary_path(str(pp)) if pp else ""
        if require_file_exists and pp and not (rp and os.path.isfile(rp)):
            continue
        if rp:
            r = dict(r)
            r["_resolved_path"] = rp
        out.append(r)
    return out


def build_catalog_text(
    source: Optional[str] = None,
    require_file_exists: bool = True,
) -> str:
    rows = list_datasets(source=source, require_file_exists=require_file_exists)
    if not rows:
        return "（数据集资产库为空或路径无效。）"
    lines = ["【数据集资产库】"]
    for r in rows:
        lines.append(
            f"- id={r['id']} | source={r.get('source')} | coverage={r.get('coverage_scale')} | "
            f"year={r.get('year')} | format={r.get('format')} | scope={r.get('geographic_scope')!r} | "
            f"path={r.get('_resolved_path') or resolve_stored_primary_path(str(r.get('primary_path', '')))}"
        )
    return "\n".join(lines)


def build_dataset_catalog_for_agent() -> str:
    """
    注入 Copilot：说明全国 SHP 与区域 TIF 如何配对，避免 Agent 误解「全中国」与「浙江一景」。
    """
    rows = list_datasets(require_file_exists=True)
    if not rows:
        return ""

    from agent_context_policy import describe_local_path, sanitize_external_text

    lines = [
        "【数据集资产库 · 已登记且文件存在的条目】",
        "",
        "使用规则（必读）：",
        "1) 每条有稳定 id；执行层在本地解析文件，模型不得编造或回声本地路径。",
        "2) coverage_scale=national（如师姐全国潮滩 SHP）：与某任务 Final TIF 对比时，评价仅在「预测图栅格范围」内统计；",
        "   真值通过栅格化与预测对齐，不是把全国矢量整体当成浙江一张图。",
        "3) coverage_scale=scene 的 TIF：表示单景/局部范围；是否与某 task 重叠需看地理范围或后续补 bounds，不要默认全国可比。",
        "4) 用户问「zhejiang1 与师姐真值差异」时：应选用 advisor、年度合适的 reference_truth shapefile，并说明指标基于该 task 的预测 TIF 覆盖区。",
        "",
    ]
    for r in rows:
        hints = r.get("related_task_hints") or []
        hint_s = "、".join(hints) if hints else "（未标注，可与任意已入库 task 的 Final TIF 配对评价）"
        pair = (r.get("pairing_note") or "").strip()
        lines.append(f"· id={r['id']}")
        lines.append(f"  title={r.get('title')} | source={r.get('source')} | year={r.get('year')} | coverage_scale={r.get('coverage_scale')}")
        lines.append(f"  geographic_scope={r.get('geographic_scope')!r} | format={r.get('format')}")
        _rp = r.get("_resolved_path") or resolve_stored_primary_path(str(r.get("primary_path", "")))
        lines.append(f"  file={describe_local_path(_rp)}")
        lines.append(f"  related_task_hints: {hint_s}")
        if pair:
            lines.append(f"  pairing_note: {pair}")
        lines.append("")
    return sanitize_external_text("\n".join(lines).rstrip())


def register_advisor_china_tidal_flat_year(
    year: int,
    shp_path: str,
    *,
    overwrite: bool = False,
) -> str:
    """登记师姐 `china_tidal_flat_projected_{year}.shp` 一类条目。"""
    eid = f"advisor_china_tidal_flat_{year}"
    return register_dataset(
        {
            "id": eid,
            "source": "advisor",
            "role": "reference_truth",
            "format": "shapefile",
            "coverage_scale": "national",
            "geographic_scope": "中国全域（矢量）；与区域 TIF 对比时有效区为预测图范围",
            "year": year,
            "title": f"师姐·全国潮滩矢量 {year}",
            "primary_path": shp_path,
            "license": "internal",
            "tags": ["tidal_flat", "china", "reference_shp", f"year_{year}"],
            "related_task_hints": [],
            "pairing_note": "与 combine.evaluate_tif_vs_shp 一致：SHP 重投影到预测 TIF 的 CRS，再按 TIF 网格栅格化后算 IoU/F1 等。",
            "aliases": [f"师姐{year}", f"china_tidal_flat_{year}"],
        },
        overwrite=overwrite,
    )


def _cmd_list(args: argparse.Namespace) -> None:
    rows = list_datasets(
        source=args.source,
        format=args.format,
        role=args.role,
        tag=args.tag,
        require_file_exists=not args.include_missing,
    )
    if not rows:
        print("(无匹配记录)")
        return
    for r in rows:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        print("-" * 40)


def _cmd_verify(args: argparse.Namespace) -> None:
    reg = load_registry()
    bad = 0
    for eid, row in sorted(reg.items()):
        row = dict(row)
        row["id"] = eid
        errs = validate_entry(row)
        if errs:
            bad += 1
            print(f"[INVALID] {eid}: {'; '.join(errs)}")
    if bad == 0:
        print(f"共 {len(reg)} 条，校验通过（registry: {registry_path()}）。")
    else:
        print(f"共 {len(reg)} 条，其中 {bad} 条存在问题。")


_DEFAULT_ADVISOR_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "DATA", "sqq_TF_20-25")


def _cmd_seed_advisor(args: argparse.Namespace) -> None:
    """在师姐数据目录下按 china_tidal_flat_projected_{year}.shp 批量登记存在的年份。"""
    base = os.path.abspath(os.path.expanduser(args.base))
    years = [int(y) for y in args.years.split(",") if y.strip()]
    for y in years:
        name = f"china_tidal_flat_projected_{y}.shp"
        shp = os.path.join(base, name)
        if not os.path.isfile(shp):
            print(f"[skip] 不存在: {shp}")
            continue
        eid = register_advisor_china_tidal_flat_year(y, shp, overwrite=args.overwrite)
        print(f"[ok] {eid} -> {shp}")


def main() -> None:
    p = argparse.ArgumentParser(description="YYnet 数据集资产库 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="列出条目")
    pl.add_argument("--source", choices=sorted(_VALID_SOURCES))
    pl.add_argument("--format", choices=sorted(_VALID_FORMATS))
    pl.add_argument("--role", choices=sorted(_VALID_ROLES))
    pl.add_argument("--tag")
    pl.add_argument("--include-missing", action="store_true")
    pl.set_defaults(func=_cmd_list)

    pv = sub.add_parser("verify", help="校验全部条目")
    pv.set_defaults(func=_cmd_verify)

    ps = sub.add_parser("seed-advisor", help="批量登记师姐 china_tidal_flat_projected_*.shp")
    ps.add_argument(
        "--base",
        default=_DEFAULT_ADVISOR_DATA_DIR,
        help=f"师姐数据集目录（默认: {_DEFAULT_ADVISOR_DATA_DIR}）",
    )
    ps.add_argument(
        "--years",
        default="2020,2021,2022,2023,2024,2025",
        help="逗号分隔年份，默认 2020～2025",
    )
    ps.add_argument("--overwrite", action="store_true")
    ps.set_defaults(func=_cmd_seed_advisor)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
