import argparse
import os
import sys
from pathlib import Path
from typing import Optional

import rasterio
from rasterio.features import rasterize
import geopandas as gpd
import numpy as np
from sklearn.metrics import confusion_matrix

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TF_AGENT = _REPO_ROOT / "TF-agent"
if _TF_AGENT.is_dir() and str(_TF_AGENT) not in sys.path:
    sys.path.insert(0, str(_TF_AGENT))


def _resolve_true_shp(true_shp: Optional[str], reference_id: Optional[str]) -> str:
    if reference_id:
        try:
            from dataset_assets import get_primary_path
        except ImportError:
            raise RuntimeError("无法导入 YYnet.dataset_assets，请确认路径。") from None
        p = get_primary_path(reference_id)
        if not p:
            raise FileNotFoundError(f"数据集资产库中未找到 id={reference_id!r} 或文件不存在。")
        return p
    if true_shp:
        return os.path.normpath(os.path.abspath(os.path.expanduser(true_shp)))
    raise ValueError("请指定 --true-shp 或 --reference-id")

def evaluate_tif_vs_shp(
    pred_tif_path: str,
    true_shp_path: str,
    nodata_val=None,
    task_aoi_shp_path: Optional[str] = None,
    task_name: Optional[str] = None,
):
    """
    将预测的 TIF 栅格与真实的 SHP 矢量进行精度对比

    参数:
        pred_tif_path (str): 模型预测结果的 tif 文件路径 (假设 1 为潮滩, 0 为背景)
        true_shp_path (str): 师姐的全国潮滩真值 shp 文件路径
        nodata_val (int): 预测图中无效值/背景值的设定，对比时会忽略这些区域
        task_aoi_shp_path: 任务分区 AOI（如 china_costal.shp），存在则先按 task_name 裁剪师姐真值再栅格化
        task_name: 与 AOI 属性表中任务名一致，如 20zhejiang2
    """
    print("⏳ [1/4] 正在加载模型预测结果 (TIF)...")
    with rasterio.open(pred_tif_path) as src:
        pred_array = src.read(1)  # 读取第一波段
        transform = src.transform
        crs_raster = src.crs
        out_shape = pred_array.shape
        print(f"  -> TIF 尺寸: {out_shape}, 坐标系: {crs_raster}")

    print("⏳ [2/4] 正在加载并对齐真值数据 (SHP)...")
    true_gdf = gpd.read_file(true_shp_path)

    # 🌟 防御性编程：强制坐标系对齐 (极其重要！)
    if true_gdf.crs != crs_raster:
        print(f"  -> 发现坐标系不一致，正在将 SHP 从 {true_gdf.crs} 投影至 {crs_raster}...")
        true_gdf = true_gdf.to_crs(crs_raster)

    if task_aoi_shp_path and task_name:
        try:
            _app = Path(__file__).resolve().parent.parent.parent / "TF-agent"
            if str(_app) not in sys.path:
                sys.path.insert(0, str(_app))
            from evaluation_geo import clip_truth_to_task_aoi

            true_gdf = clip_truth_to_task_aoi(true_gdf, task_aoi_shp_path, task_name, logger=print)
        except Exception as e:
            print(f"  ⚠️ 任务 AOI 裁剪失败，使用未裁剪真值: {e}")

    print("⏳ [3/4] 正在将矢量真值栅格化 (Rasterize)...")
    # 提取几何对象，并将其值设定为 1 (代表真实潮滩)
    geom_value = ((geom, 1) for geom in true_gdf.geometry)
    
    # 将 SHP 烧录成与 TIF 完全一样的像素矩阵
    true_array = rasterize(
        shapes=geom_value,
        out_shape=out_shape,
        transform=transform,
        fill=0,            # 矢量范围外填充 0 (背景)
        dtype=np.uint8
    )

    print("⏳ [4/4] 正在计算评价指标 (Metrics)...")
    
    # 展平二维数组为一维，方便计算
    pred_flat = pred_array.flatten()
    true_flat = true_array.flatten()

    # 🌟 核心修复点：将预测结果中的 255 (纯白潮滩) 强制映射为 1
    # 这样就能和师姐矢量转换出来的栅格标签 (1) 完美匹配
    pred_flat = np.where(pred_flat == 255, 1, pred_flat)

    # 处理 Nodata 区域
    if nodata_val is not None:
        valid_mask = (pred_flat != nodata_val)
        pred_valid = pred_flat[valid_mask]
        true_valid = true_flat[valid_mask]
    else:
        pred_valid = pred_flat
        true_valid = true_flat

    # 计算混淆矩阵 (TN, FP, FN, TP)
    # 此时预测图和真值图的潮滩标签都统一变成了 1
    tn, fp, fn, tp = confusion_matrix(true_valid, pred_valid, labels=[0, 1]).ravel()

    # 防御性计算 (分母为 0 保护)
    iou = tp / (tp + fp + fn) if (tp + fp + fn) > 0 else 0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
    oa = (tp + tn) / (tp + tn + fp + fn) if len(pred_valid) > 0 else 0

    print("\n" + "="*40)
    print("🎯 潮滩分割精度报告")
    print("="*40)
    print(f"总体精度 (Overall Accuracy) : {oa * 100:.2f} %")
    print(f"交并比 (IoU)                : {iou * 100:.2f} %")
    print(f"精确率 (Precision)          : {precision * 100:.2f} %")
    print(f"召回率 (Recall)             : {recall * 100:.2f} %")
    print(f"F1-Score                    : {f1_score * 100:.2f} %")
    print("-" * 40)
    print(f"True Positives (TP): {tp} 像素 (正确预测的潮滩)")
    print(f"False Positives(FP): {fp} 像素 (误报的潮滩，错分)")
    print(f"False Negatives(FN): {fn} 像素 (漏报的潮滩，漏分)")
    print("="*40)

    return iou, precision, recall

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="预测 TIF vs 真值 SHP 精度评价（真值路径可直接写或用 YYnet 资产 id）")
    ap.add_argument("--pred", default=None, help="预测结果 GeoTIFF 路径")
    ap.add_argument("--true-shp", default=None, help="真值主 .shp 路径")
    ap.add_argument(
        "--reference-id",
        default=None,
        help="YYnet dataset_assets_registry 中的 id，例如 advisor_china_tidal_flat_2020",
    )
    ap.add_argument("--nodata", type=int, default=None, help="预测图中需忽略的 nodata 像元值")
    ap.add_argument(
        "--task-aoi-shp",
        default=None,
        help=r"任务分区 AOI，如 E:\Data\CHINA_tf_city\china_costal.shp",
    )
    ap.add_argument(
        "--task-name",
        default=None,
        help="与 AOI 属性一致的任务名（如 20zhejiang2）；缺省则从预测文件名推断",
    )
    args = ap.parse_args()

    pred = args.pred or r"E:\Data\843output\20zhejiang3\20zhejiang3_Final_p0.05_c3.tif"
    true_shp = _resolve_true_shp(args.true_shp, args.reference_id)
    _tn = args.task_name
    if not _tn and pred:
        import re as _re

        _m = _re.match(r"^(\d{2}[a-zA-Z0-9]+?)_Final", os.path.basename(pred))
        if _m:
            _tn = _m.group(1)

    evaluate_tif_vs_shp(
        pred,
        true_shp,
        nodata_val=args.nodata,
        task_aoi_shp_path=args.task_aoi_shp,
        task_name=_tn,
    )