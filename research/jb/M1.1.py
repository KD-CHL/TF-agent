import os
import numpy as np
import rasterio
from rasterio.features import shapes
import geopandas as gpd
import warnings
from tqdm import tqdm

warnings.filterwarnings("ignore")

class FinalTidalFlatExtractor:
    def __init__(self, workspace_dir: str):
        self.workspace = workspace_dir
        self.output_dir = os.path.join(workspace_dir, "outputs")
        os.makedirs(self.output_dir, exist_ok=True)
        print(f"📁 最终潮滩合成工作区已初始化: {self.output_dir}")

    def generate_tidal_flat(self, m1_shp_path: str, m2_tif_path: str):
        print("\n🚀 --- 启动最终阶段: 空间交集提取潮滩 ---")
        
        # ==========================================
        # 1. 加载 M1 最大海面矢量 (高潮线)
        # ==========================================
        print(f"🌊 正在加载 M1 最大水面矢量: {os.path.basename(m1_shp_path)}")
        gdf_m1 = gpd.read_file(m1_shp_path)
        if gdf_m1.empty:
            raise ValueError("❌ M1 矢量文件为空！")

        # ==========================================
        # 2. 读取 M2 暴露概率栅格并矢量化
        # ==========================================
        print(f"🏜️ 正在读取 M2 暴露概率栅格: {os.path.basename(m2_tif_path)}")
        with rasterio.open(m2_tif_path) as src:
            prob_data = src.read(1)
            transform = src.transform
            crs_m2 = src.crs

        print("🗺️ 正在将 M2 概率图 (概率 > 0) 转化为面矢量...")
        # 提取只要概率 > 0 的区域（即所有非纯水体的区域）
        mask = prob_data > 0
        
        # 栅格转矢量
        results = (
            {'properties': {'prob_val': v}, 'geometry': s}
            for i, (s, v) in enumerate(tqdm(
                list(shapes(prob_data, mask=mask, transform=transform)), 
                desc="矢量化进度"
            ))
        )
        gdf_m2 = gpd.GeoDataFrame.from_features(list(results), crs=crs_m2)
        
        if gdf_m2.empty:
            raise ValueError("❌ M2 栅格中没有找到任何概率 > 0 的区域！")

        # ==========================================
        # 3. 坐标系对齐 (极其重要的 GIS 防御性编程)
        # ==========================================
        if gdf_m1.crs != gdf_m2.crs:
            print(f"🔄 检测到坐标系不一致，正在将 M2 ({gdf_m2.crs}) 投影至 M1 ({gdf_m1.crs})...")
            gdf_m2 = gdf_m2.to_crs(gdf_m1.crs)

        # ==========================================
        # 4. 终极空间运算：求交集 (Intersection)
        # ==========================================
        print("🔗 正在执行高能空间运算：M1 (最大海面) ∩ M2 (暴露陆地) = 潮滩 ...")
        # 这一步可能会耗时一两分钟，取决于多边形的复杂程度
        tidal_flat_gdf = gpd.overlay(gdf_m1, gdf_m2, how='intersection')

        if tidal_flat_gdf.empty:
            print("⚠️ 警告：交集为空！请检查 M1 和 M2 的地理范围是否重叠。")
            return None

        # (可选) 融合同一概率的碎小多边形，让最终的 Shapefile 更干净
        print("🧹 正在优化与清理最终潮滩多边形...")
        tidal_flat_gdf = tidal_flat_gdf.dissolve()

        # ==========================================
        # 5. 导出最终成果
        # ==========================================
        out_path = os.path.join(self.output_dir, "Final_Intertidal_Flat.shp")
        tidal_flat_gdf.to_file(out_path)
        print(f"🎉🎉🎉 大功告成！潮滩最终边界已提取并保存至: {out_path}")
        return out_path

if __name__ == "__main__":
    # 【配置区域】填入你之前跑出来的两个文件的绝对路径
    M1_SHAPEFILE = r"E:\Code\GEE\YYnet\DATA\outputs\step3_tidal_ocean_extent.shp" 
    M2_PROB_TIF = r"E:\Code\GEE\YYnet\DATA\output2\step2_fixed.tif"
    
    extractor = FinalTidalFlatExtractor(workspace_dir=r"E:\Code\GEE\YYnet\DATA\final_result")
    
    try:
        final_shp = extractor.generate_tidal_flat(M1_SHAPEFILE, M2_PROB_TIF)
        print("\n✅ 现在，把这个 Final_Intertidal_Flat.shp 拖进 ArcGIS，搭配卫星底图，这就是你这篇论文/周报的最终核心成果图！")
    except Exception as e:
        print(f"\n❌ 运行异常: {e}")