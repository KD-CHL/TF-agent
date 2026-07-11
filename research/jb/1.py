import os
import rasterio
from rasterio.features import shapes
import geopandas as gpd
from shapely.geometry import shape
import warnings
from tqdm import tqdm

warnings.filterwarnings("ignore")

def convert_cstfseg_raster_to_vector(tif_path: str, out_shp_path: str, tidal_flat_value: int = 1):
    """
    将 CSTFSeg 的栅格结果转换为 ESRI Shapefile
    :param tif_path: 输入的 CSTFSeg 预测图 TIF 路径
    :param out_shp_path: 输出的 Shapefile 路径
    :param tidal_flat_value: 模型预测结果中，代表“潮滩”的像素值（通常为 1 或 255，请根据实际情况配置）
    """
    print(f"\n🔄 正在读取栅格数据: {os.path.basename(tif_path)}")
    
    if not os.path.exists(tif_path):
        raise FileNotFoundError(f"❌ 找不到输入的 TIF 文件: {tif_path}")

    with rasterio.open(tif_path) as src:
        image = src.read(1)        # 读取第1波段 (单通道分类图)
        transform = src.transform  # 获取地理仿射变换矩阵
        crs = src.crs              # 获取原始坐标系 (如 WGS84 或 UTM)
        nodata = src.nodata

    print(f"📊 正在解析像素矩阵，提取目标标签值 [{tidal_flat_value}] 的潮滩斑块...")
    
    # 建立布尔掩膜：只提取像素值等于目标潮滩标签的区域，排查无效值
    mask = (image == tidal_flat_value)
    if nodata is not None:
        mask = mask & (image != nodata)

    # 利用 rasterio 核心算子 shapes 提取像素边界的几何形状
    # shapes 返回的是一个生成器，每个元素为 (geometry_dict, value)
    shape_generator = shapes(image, mask=mask, transform=transform)
    
    records = []
    # 转换为列表以配置进度条
    shape_list = list(shape_generator)
    
    if not shape_list:
        print(f"⚠️ 警告：在该 TIF 影像中未检测到任何像素值为 [{tidal_flat_value}] 的潮滩区域！")
        return None

    for geom, value in tqdm(shape_list, desc="🎨 几何拓扑矢量化", unit="面"):
        # 将 GeoJSON 字典结构转换为 Shapely 可操作的 Geometry 对象
        poly_geom = shape(geom)
        records.append({
            "geometry": poly_geom,
            "class_val": int(value)
        })

    print(f"🗺️ 正在组装矢量图层并注入地理坐标系 ({crs if crs else '未定义'})...")
    gdf = gpd.GeoDataFrame(records, crs=crs)

    print("🧹 正在执行空间融合 (Dissolve)，消除原始像素网格网线痕迹...")
    # 按照 class_val 进行融合，把原本成百上千个微小的像素方块面，融合成干净的连续多边形
    gdf_dissolved = gdf.dissolve(by="class_val").reset_index()

    # 自动创建不存在的输出目录
    out_dir = os.path.dirname(out_shp_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    print(f"💾 正在写入 ESRI Shapefile 磁盘固化存储...")
    # 强制以 UTF-8 编码保存，防止路径或属性表出现乱码
    gdf_dissolved.to_file(out_shp_path, encoding='utf-8')
    
    print(f"🎉 转换成功！矢量成果已保存至: {out_shp_path}")
    return out_shp_path

if __name__ == "__main__":
    print("==========================================================")
    print("        CSTFSeg 栅格转矢量单机测试流启动")
    print("==========================================================")
    
    # 🛠️ 【本地化独立测试配置控制台】
    # 1. 填入你的 CSTFSeg 模型推理出来的历史 TIF 路径
    INPUT_CSTF_TIF = r"E:\Data\843output\24zhejiang1\24zhejiang1_Final_p0.13_c2.tif"
    
    # 2. 填入你期望转换出来的 SHP 矢量保存路径
    OUTPUT_VECTOR_SHP = r"E:\Data\843output\24zhejiang1\History_Flat_2022_Vector1.shp"
    
    # 3. 核心标签值指定：请确认你的 CSTFSeg 模型中，潮滩的分类标签代码是多少？
    # 常见情况：二值化分类为 1；或者灰度图展现为 255。请在此处进行对齐。
    TARGET_LABEL_VALUE = 255
    
    # 执行一键转换
    try:
        converted_path = convert_cstfseg_raster_to_vector(
            tif_path=INPUT_CSTF_TIF,
            out_shp_path=OUTPUT_VECTOR_SHP,
            tidal_flat_value=TARGET_LABEL_VALUE
        )
        print("\n💡 提示：转换完成后，你现在可以把生成的 SHP 丢进 M5 模块")
        print("   与师姐指数方法得到的最新 SHP 矢量进行全历史周期的同维时空对比了！")
    except Exception as e:
        print(f"\n❌ 运行中断，错误原因: {e}")