import geopandas as gpd
import rasterio
import numpy as np
from shapely.geometry import box
from sklearn.metrics import confusion_matrix

def evaluate_points_with_tif(tif_path: str, shp_path: str, label_column: str):
    """
    使用点矢量数据验证单张 TIF 栅格的精度
    """
    print("⏳ [1/5] 正在加载模型预测结果 (TIF) 并获取空间边界...")
    with rasterio.open(tif_path) as src:
        crs_tif = src.crs
        bounds = src.bounds
        print(f"  -> TIF 坐标系: {crs_tif}")
        print(f"  -> TIF 边界 (Bounds): {bounds}")
        
        print("⏳ [2/5] 正在加载验证点数据 (SHP) 并对齐坐标系...")
        gdf = gpd.read_file(shp_path)
        if gdf.crs != crs_tif:
            print(f"  -> 坐标系不一致，正在将 SHP 投影至 {crs_tif}...")
            gdf = gdf.to_crs(crs_tif)

        print("⏳ [3/5] 正在进行空间过滤，提取落入当前 TIF 范围内的验证点...")
        tif_bbox = box(*bounds)
        gdf_clip = gdf[gdf.geometry.intersects(tif_bbox)]
        
        total_points = len(gdf_clip)
        if total_points == 0:
            print("❌ 错误：在这张 TIF 的空间范围内，没有找到任何师姐的验证点！")
            return
        print(f"  -> 成功筛选出 {total_points} 个落入该区域的验证点。")

        print("⏳ [4/5] 正在从 TIF 中抽取对应坐标的像元值 (Point Sampling)...")
        coords = [(geom.x, geom.y) for geom in gdf_clip.geometry]
        sampled_values = list(src.sample(coords))
        pred_values = [val[0] for val in sampled_values]

    print("⏳ [5/5] 正在计算评价指标 (Metrics)...")
    
    # 🌟 1. 处理预测值：将 TIF 中的 255 (纯白潮滩) 强制映射为 1，背景 0 保持不变
    pred_array = np.array([1 if p == 255 else 0 for p in pred_values])
    
    # 🌟 2. 处理真实值：解析师姐的属性表 (防御性编程：转小写并去除空格)
    true_labels = gdf_clip[label_column].values
    true_array = np.array([
        1 if str(label).strip().lower() == 'flat' else 0 
        for label in true_labels
    ])

    # 计算混淆矩阵
    tn, fp, fn, tp = confusion_matrix(true_array, pred_array, labels=[0, 1]).ravel()

    # 指标计算 (分母为 0 保护)
    iou = tp / (tp + fp + fn) if (tp + fp + fn) > 0 else 0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
    oa = (tp + tn) / total_points if total_points > 0 else 0

    print("\n" + "="*40)
    print(f"🎯 区域潮滩分割精度报告")
    print("="*40)
    print(f"有效验证点总数              : {total_points} 个")
    print("-" * 40)
    print(f"总体精度 (Overall Accuracy) : {oa * 100:.2f} %")
    print(f"交并比 (IoU)                : {iou * 100:.2f} %")
    print(f"精确率 (Precision)          : {precision * 100:.2f} %")
    print(f"召回率 (Recall)             : {recall * 100:.2f} %")
    print(f"F1-Score                    : {f1_score * 100:.2f} %")
    print("-" * 40)
    print(f"True Positives (TP): {tp} 个 (正确预测的潮滩点)")
    print(f"False Positives(FP): {fp} 个 (误把 inland/water 预测成了潮滩)")
    print(f"False Negatives(FN): {fn} 个 (漏报了原本是 flat 的潮滩点)")
    print("="*40)

if __name__ == "__main__":
    # 替换为你实际的 SHP 和 TIF 路径
    SHP_FILE = r"E:\Code\GEE\jb\边缘验证点\edge_point_all2020.shp"
    TIF_FILE = r"E:\Data\20\843output\20liaoning1\20tianjin_Final_p0.05_c1.tif"
    
    # 已经为你设置好了正确的列名
    TARGET_COLUMN = "class" 
    
    evaluate_points_with_tif(TIF_FILE, SHP_FILE, TARGET_COLUMN)