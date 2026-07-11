# -*- coding: utf-8 -*-
"""
CSTF 地理空间模块：路径归一化、拓扑自愈、空交集拦截、专家级报错文案。
供 research/jb/* 与 TF-agent/* 共用。
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Optional, Tuple, Union

PathLike = Union[str, Path]

_MIN_AREA_M2 = 1e-6


def normalize_path(path: Optional[PathLike], *, must_exist: bool = False) -> Optional[str]:
    """清洗路径：去首尾空格/引号，normpath + abspath，兼容 \\ 与 /。"""
    if path is None:
        return None
    raw = str(path).strip().strip('"').strip("'").strip()
    if not raw:
        return None
    cleaned = os.path.normpath(os.path.abspath(os.path.expanduser(raw)))
    if must_exist and not os.path.exists(cleaned):
        raise FileNotFoundError(f"路径不存在: {cleaned}")
    return cleaned


def ensure_dir(path: Optional[PathLike], logger: Callable[[str], Any] = print) -> str:
    """缺夹自创：静默创建输出目录（含多级）。"""
    p = normalize_path(path) or ""
    if not p:
        raise ValueError("无法创建目录：路径为空")
    os.makedirs(p, exist_ok=True)
    return p


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """分母零值护盾。"""
    if denominator is None or abs(float(denominator)) < _MIN_AREA_M2:
        return float(default)
    return float(numerator) / float(denominator)


def safe_pct_change(current: float, baseline: float, default: float = 0.0) -> float:
    return safe_div(current - baseline, baseline, default=default) * 100.0


def repair_geometries(gdf, logger: Callable[[str], Any] = print):
    """无效拓扑自愈：is_valid 检查 + make_valid / buffer(0)。"""
    import geopandas as gpd

    if gdf is None or gdf.empty:
        return gdf
    gdf = gdf.copy()
    invalid = (~gdf.geometry.is_valid) & gdf.geometry.notna()
    n_bad = int(invalid.sum())
    if n_bad > 0:
        logger(f"  ├─ 拓扑自愈：发现 {n_bad} 个无效几何，正在 make_valid / buffer(0) 修复…")
        try:
            from shapely.validation import make_valid

            gdf.loc[invalid, "geometry"] = gdf.loc[invalid, "geometry"].apply(make_valid)
        except ImportError:
            gdf.loc[invalid, "geometry"] = gdf.loc[invalid, "geometry"].buffer(0)
        logger("  └─ 拓扑修复完成，已继续后续空间运算。")
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    return gdf


def geometries_have_overlap(geom_a, geom_b, min_area_m2: float = _MIN_AREA_M2) -> bool:
    """判断两期 union 是否存在有效空间交集。"""
    if geom_a is None or geom_b is None or geom_a.is_empty or geom_b.is_empty:
        return False
    try:
        inter = geom_a.intersection(geom_b)
        return not inter.is_empty and inter.area >= min_area_m2
    except Exception:
        return False


def banner(title: str, logger: Callable[[str], Any] = print, char: str = "═", width: int = 52) -> None:
    logger("")
    logger(char * width)
    logger(f"  {title}")
    logger(char * width)


def warn(msg: str, logger: Callable[[str], Any] = print) -> None:
    logger(f"⚠️  {msg}")


def success(msg: str, logger: Callable[[str], Any] = print) -> None:
    logger(f"✅  {msg}")


def expert_error(
    what: str,
    cause: str,
    prescription: str,
    logger: Callable[[str], Any] = print,
    exc: Optional[BaseException] = None,
) -> None:
    """专家处方式报错（不直接 dump Traceback 给用户）。"""
    logger("")
    logger("─" * 52)
    logger(f"💔 发生了什么：{what}")
    logger(f"🩺 诊断病因：{cause}")
    logger(f"💊 解决方案：{prescription}")
    if exc is not None and os.environ.get("CSTF_DEBUG"):
        logger("─" * 52)
        logger(traceback.format_exc())
    logger("─" * 52)


def run_cli_main(main_fn: Callable[[], None], module_name: str = "模块") -> int:
    """CLI 入口防爆屏包装。"""
    try:
        main_fn()
        return 0
    except FileNotFoundError as e:
        expert_error(
            f"{module_name} 找不到所需文件。",
            str(e),
            "请检查路径是否正确、文件是否已被移动；路径可含 \\ 或 /，系统会自动归一化。",
            exc=e,
        )
        return 1
    except Exception as e:
        expert_error(
            f"{module_name} 运行未完成。",
            str(e),
            "请核对输入数据坐标系、几何是否有效；设置环境变量 CSTF_DEBUG=1 可查看详细堆栈。",
            exc=e,
        )
        return 1


def zero_overlap_message_m5() -> str:
    return (
        "⚠️ 检测到两期空间要素无任何重叠区域，自动将变化率计为 0.00%，"
        "生成空白变化图层并安全退出。"
    )


def zero_overlap_message_e1(pair: str = "") -> str:
    suffix = f"（{pair}）" if pair else ""
    return (
        f"⚠️ 检测到对比要素与参考层{suffix}无任何像元级重叠，"
        "IoU 自动记为 0.0000，跳过分歧图导出并安全继续。"
    )
