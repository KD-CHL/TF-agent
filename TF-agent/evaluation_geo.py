"""
评价用地理工具：按任务 AOI 矢量裁剪师姐（参考）潮滩 SHP，避免跨任务重叠区进入指标。

默认 AOI 路径可通过环境变量 CSTF_TASK_AOI_SHP 覆盖。
侧栏任务名常为带年份前缀形式（如 20zhejiang1），AOI 属性表 name 列多为小写省名+序号（如 zhejiang1）。
本模块会自动去掉前两位年份再与 name 等列匹配；优先使用 name 列，避免用 object 等易重复字段。
"""

from __future__ import annotations

import os
import re
from typing import Callable, List, Optional

import geopandas as gpd

DEFAULT_TASK_AOI_SHP = os.environ.get(
    "CSTF_TASK_AOI_SHP",
    r"E:\Data\CHINA_tf_city\china_costal.shp",
)


def _log(logger: Optional[Callable], msg: str) -> None:
    if logger:
        logger(msg)


def task_name_match_variants(task_name: str) -> List[str]:
    """
    生成用于匹配 AOI 表（如 name=zhejiang1）的字符串变体。
    含：原样、大小写、去掉前两位年份后的尾部（20zhejiang1 → zhejiang1）。
    """
    tn = str(task_name).strip()
    if not tn:
        return []
    out: List[str] = []
    for x in {tn, tn.lower(), tn.upper()}:
        if x and x not in out:
            out.append(x)
    # 20zhejiang1 → zhejiang1（与 china_costal 属性表 name 列一致）
    m = re.match(r"^(\d{2})(.+)$", tn, re.I)
    if m:
        tail = m.group(2).strip()
        for x in {tail, tail.lower(), tail.upper()}:
            if x and x not in out:
                out.append(x)
    # 若任务名本身无年份前缀，也保留去数字前缀的尾部（兼容 2020zhejiang1 等少见命名）
    tail2 = re.sub(r"^\d{2,4}", "", tn, count=1, flags=re.I).strip()
    if tail2 and tail2.lower() != tn.lower():
        for x in {tail2, tail2.lower(), tail2.upper()}:
            if x and x not in out:
                out.append(x)
    return out


_SKIP_FALLBACK_COLS = frozenset(
    {
        "object",
        "OBJECT",
        "OBJECTID",
        "FID",
        "fid",
        "Shape",
        "shape",
        "geometry",
    }
)


def filter_aoi_for_task(aoi: gpd.GeoDataFrame, task_name: str) -> gpd.GeoDataFrame:
    """在 AOI 表中筛选与当前任务对应的行；name=zhejiang1 与任务 20zhejiang1 自动对齐。"""
    variants = task_name_match_variants(task_name)
    if not variants:
        return aoi.iloc[0:0].copy()

    # name / NAME 优先：与全国省片 china_costal 属性表一致
    preferred = [
        "name",
        "NAME",
        "task",
        "TASK",
        "task_id",
        "TASK_ID",
        "tasknode",
        "TaskNode",
        "city",
        "CITY",
        "node",
        "NODE",
        "区划",
        "任务",
        "region",
        "REGION",
    ]
    for col in preferred:
        if col not in aoi.columns:
            continue
        s = aoi[col].astype(str).str.strip()
        for v in variants:
            if not v:
                continue
            m = s.str.casefold() == v.casefold()
            if m.any():
                return aoi[m].copy()
        for v in variants:
            if len(v) < 2:
                continue
            pat = r"(?<![A-Za-z0-9_])" + re.escape(v) + r"(?![A-Za-z0-9_])"
            m = s.str.contains(pat, case=False, na=False, regex=True)
            if m.any():
                hit = aoi[m].copy()
                if len(hit) <= len(aoi):
                    return hit

    for col in aoi.columns:
        if col in _SKIP_FALLBACK_COLS:
            continue
        try:
            s = aoi[col].astype(str).str.strip()
        except Exception:
            continue
        for v in variants:
            if len(v) < 2:
                continue
            pat = r"(?<![A-Za-z0-9_])" + re.escape(v) + r"(?![A-Za-z0-9_])"
            m = s.str.contains(pat, case=False, na=False, regex=True)
            if m.any() and int(m.sum()) < len(aoi):
                return aoi[m].copy()
    raise ValueError(
        f"在任务 AOI 文件中未找到与任务名 «{task_name}» 匹配的记录（已尝试去掉前两位年份后与各列比对，"
        f"如 20zhejiang1 → zhejiang1）。可用列: {list(aoi.columns)}。"
    )


def clip_truth_to_task_aoi(
    truth_gdf: gpd.GeoDataFrame,
    aoi_shp_path: str,
    task_name: str,
    logger: Optional[Callable] = None,
) -> gpd.GeoDataFrame:
    """
    用任务 AOI 多边形裁剪师姐真值（不改变 truth_gdf 的 CRS）。
    aoi_shp_path 为空或文件不存在时原样返回 truth_gdf。
    """
    if not aoi_shp_path or not str(aoi_shp_path).strip():
        return truth_gdf
    path = os.path.normpath(os.path.expanduser(str(aoi_shp_path).strip()))
    if not os.path.isfile(path):
        _log(logger, f"  ⚠️ 任务 AOI 文件不存在，跳过裁剪: {path}")
        return truth_gdf

    aoi = gpd.read_file(path)
    if aoi.crs is None:
        raise ValueError(f"任务 AOI 缺少 CRS: {path}")
    if truth_gdf.crs is not None and aoi.crs != truth_gdf.crs:
        aoi = aoi.to_crs(truth_gdf.crs)

    sub = filter_aoi_for_task(aoi, task_name)
    if sub.empty:
        raise ValueError(f"任务 {task_name} 在 AOI 中筛选结果为空")

    # gpd.clip 对多行 AOI 使用全部几何的并集作为裁剪范围
    clipped = gpd.clip(truth_gdf, sub)

    _log(
        logger,
        f"  → 已按任务 AOI 裁剪师姐真值: {os.path.basename(path)} | "
        f"任务={task_name} | 要素 {len(truth_gdf)} → {len(clipped)}",
    )
    if clipped.empty:
        raise ValueError(
            "师姐真值与任务 AOI 求交后为空，请检查 AOI 是否与师姐数据空间相交、"
            "或任务名字段是否与侧栏任务一致。"
        )
    return clipped
