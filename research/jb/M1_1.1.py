import os
import glob
import numpy as np
import rasterio
from rasterio.errors import RasterioError
from rasterio.features import shapes
import geopandas as gpd
from shapely.geometry import shape
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks
from skimage.measure import label
import warnings
from tqdm import tqdm

warnings.filterwarnings("ignore")

class LocalTidalExtractor:
    def __init__(self, workspace_dir: str):
        """初始化测试工作区"""
        self.workspace = workspace_dir
        self.output_dir = os.path.join(workspace_dir, "outputs")
        os.makedirs(self.output_dir, exist_ok=True)
        print(f"📁 工作区已初始化: {self.output_dir}")

    # ==========================================
    # Step 1: 读取本地影像，计算并合成最大 mNDWI
    # ==========================================
    def step1_max_mndwi(self, input_folder: str, b3_idx: int = 3, b11_idx: int = 5):
        """
        读取文件夹下所有 TIF，计算 mNDWI 并取最大值。
        假设你从 GEE 下载的波段顺序是 ['B8','B4','B3','B2','B11']，那么 B3 是第 3 个，B11 是第 5 个。
        """
        print("\n🚀 --- 执行 Step 1: 计算 mNDWI 最大值 ---")
        tif_files = glob.glob(os.path.join(input_folder, "*.tif"))
        if not tif_files:
            raise ValueError(f"❌ 在 {input_folder} 中没有找到 .tif 文件！")
        
        print(f"📦 找到 {len(tif_files)} 个影像，准备合成...")
        
        max_mndwi_array = None
        profile = None
        skipped = []

        for file in tqdm(tif_files, desc="Step1 合成mNDWI", unit="景"):
            try:
                with rasterio.open(file) as src:
                    if profile is None:
                        profile = src.profile
                        profile.update(count=1, dtype=rasterio.float32, nodata=np.nan)

                    b3 = src.read(b3_idx).astype(np.float32)
                    b11 = src.read(b11_idx).astype(np.float32)

                # 🌟 修复：千万不能用 >0 过滤！纯水体的 B11 经常是负数或 0。
                    # 只需确保分母不为 0，并且过滤掉原生的 NaN (NoData) 即可
                    denominator = b3 + b11
                    valid_mask = (denominator != 0) & (~np.isnan(b3)) & (~np.isnan(b11))
                    
                    mndwi = np.full(b3.shape, np.nan, dtype=np.float32)
                    # 计算 mNDWI
                    mndwi[valid_mask] = (b3[valid_mask] - b11[valid_mask]) / denominator[valid_mask]
                    
                    # 严谨复刻 GEE 的范围控制: mNDWI > -1 and mNDWI < 1
                    # 小于 -1 或 大于 1 的异常值直接设为 NaN 剔除
                    mndwi = np.where((mndwi > -1) & (mndwi < 1), mndwi, np.nan)

                if max_mndwi_array is None:
                    max_mndwi_array = mndwi
                else:
                    max_mndwi_array = np.fmax(max_mndwi_array, mndwi)
            except (RasterioError, OSError, ValueError) as e:
                skipped.append((file, repr(e)))
                tqdm.write(f"⚠️ 跳过无法读取的影像: {file}\n   {e!r}")

        if max_mndwi_array is None:
            raise ValueError(
                "❌ 没有成功读取任何一景影像（可能全部损坏或路径无效）。"
                f" 跳过记录: {skipped}"
            )

        if skipped:
            log_path = os.path.join(self.output_dir, "step1_skipped_tifs.txt")
            with open(log_path, "w", encoding="utf-8") as lf:
                for path, err in skipped:
                    lf.write(f"{path}\t{err}\n")
            print(f"⚠️ 已跳过 {len(skipped)} 个坏图，详情已写入: {log_path}")

        # 导出 Step 1 结果
        out_path = os.path.join(self.output_dir, "step1_mndwi_max.tif")
        with rasterio.open(out_path, 'w', **profile) as dst:
            dst.write(max_mndwi_array, 1)
            
        print(f"✅ Step 1 完成！最大 mNDWI 灰度图已保存至: {out_path}")
        return out_path

    # ==========================================
    # Step 2: 利用直方图导数进行智能阈值二值化
    # ==========================================
    def step2_binary_water(self, mndwi_tif: str):
        print("\n🚀 --- 执行 Step 2: 自适应阈值二值化 ---")
        
        with rasterio.open(mndwi_tif) as src:
            mndwi_data = src.read(1)
            profile = src.profile
            
        valid_data = mndwi_data[~np.isnan(mndwi_data)]
        
        # 1. 构建直方图
        counts, bin_edges = np.histogram(valid_data, bins=200, range=(-1, 1))
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
        
        # 2. 高斯平滑与求导 (复现师姐的寻峰逻辑)
        smoothed_counts = gaussian_filter1d(counts, sigma=2) 
        derivative = np.gradient(smoothed_counts, bin_centers)
        
        # 寻找 mNDWI > 0 区域的最高峰 (水体峰)
        gt_zero_idx = np.where(bin_centers > 0)[0]
        threshold_mndwi = 0.0 # 默认阈值
        
        if len(gt_zero_idx) > 0:
            peaks, _ = find_peaks(smoothed_counts[gt_zero_idx])
            if len(peaks) > 0:
                first_peak_idx = gt_zero_idx[peaks[0]] 
                # 往回找导数由正变负的谷底
                derivative_before_peak = derivative[:first_peak_idx]
                sign_changes = np.where((derivative_before_peak[:-1] < 0) & (derivative_before_peak[1:] > 0))[0]
                
                if len(sign_changes) > 0:
                    threshold_mndwi = bin_centers[sign_changes[-1]]
        
        print(f"📊 算法计算得出自适应水体阈值: {threshold_mndwi:.4f}")
        
        # 3. 二值化: 大于阈值的认为是水 (1)，小于等于的是陆地 (0)
        water_mask = np.full(mndwi_data.shape, 0, dtype=np.uint8)
        water_mask[mndwi_data > threshold_mndwi] = 1
        
        # 导出 Step 2 结果
        profile.update(dtype=rasterio.uint8, nodata=None)
        out_path = os.path.join(self.output_dir, "step2_water_mask.tif")
        with rasterio.open(out_path, 'w', **profile) as dst:
            dst.write(water_mask, 1)
            
        print(f"✅ Step 2 完成！水陆二值图已保存至: {out_path}")
        return out_path

    # ==========================================
    # Step 3: 通过本地点资产(SHP)精确提取海洋面
    # ==========================================
    def step3_extract_ocean_by_points(self, mask_tif: str, points_shp: str):
        print("\n🚀 --- 执行 Step 3: 根据散点 Shapefile 提取真实海洋面 ---")
        
        # 1. 读取你本地的散点资产 SHP
        print(f"📍 正在读取海洋种子点资产: {points_shp}")
        seed_points = gpd.read_file(points_shp)
        
        with rasterio.open(mask_tif) as src:
            water_mask = src.read(1)
            transform = src.transform
            crs = src.crs

        # 确保点的坐标系和栅格图严格一致
        if seed_points.crs != crs:
            seed_points = seed_points.to_crs(crs)

        # 2. 栅格转矢量：将所有水体 (值为 1) 转化为多边形
        print("🗺️ 正在将全流水体转化为矢量多边形 (耗时操作，请稍候)...")
        results = (
            {'properties': {'class': v}, 'geometry': s}
            for i, (s, v) in enumerate(shapes(water_mask, mask=(water_mask==1), transform=transform))
        )
        gdf_water = gpd.GeoDataFrame.from_features(list(results), crs=crs)

        if gdf_water.empty:
            print("❌ 警告：未提取到任何水体矢量！")
            return None

        # 3. 核心空间相交 (等效于 GEE 的 filterBounds)
        print("🔗 正在执行空间相交：利用点资产精准捕获被桥梁隔断的海区...")
        # sjoin (Spatial Join) 会找出所有“包含”这些点的水体多边形
        selected_ocean = gpd.sjoin(gdf_water, seed_points, how="inner", predicate="intersects")

        if selected_ocean.empty:
            print("⚠️ 警告：你的点全都没有落在水体上！请检查点的位置是否正确。")
            final_vector = gdf_water # 兜底，返回全部水体
        else:
            # 去重：因为一个大片海洋里可能有好几个点，会导致同一个多边形被选出多次
            final_vector = selected_ocean.drop_duplicates(subset='geometry').copy()
            
            # (可选) 如果你希望所有海区融合成一个整体，可以解开下面这行的注释
            # final_vector = final_vector.dissolve()

        # 清理多余的属性列，只保留几何图形
        final_vector = final_vector[['geometry']]

        # 导出 Step 3 结果
        out_path = os.path.join(self.output_dir, "step3_tidal_ocean_extent.shp")
        final_vector.to_file(out_path)
        
        print(f"✅ Step 3 完成！基于点资产提取的纯净海洋矢量已保存至: {out_path}")
        return out_path


# ==========================================
# 🎯 Main 函数：一步一步严格调用测试
# ==========================================
if __name__ == "__main__":
    # 【配置区域】你需要修改这里为你本地存放影像的真实文件夹路径
    # 假设你把 GEE 下载的浙江地区的 TIF 放到了这个目录
    MY_TIF_FOLDER = r"H:\我的云端硬盘\20_5b_zhejiang1" 
    MY_POINTS_SHP = r"E:\Code\GEE\jb\point\points_export.shp"

    # 初始化工具
    extractor = LocalTidalExtractor(workspace_dir=r"E:\Code\GEE\YYnet\DATA")
    
    try:
        with tqdm(total=3, desc="M1 全流程", unit="步") as p_main:
            mndwi_tif_path = extractor.step1_max_mndwi(
                input_folder=MY_TIF_FOLDER, b3_idx=3, b11_idx=5
            )
            p_main.update(1)
            water_mask_path = extractor.step2_binary_water(mndwi_tif_path)
            p_main.update(1)
            final_shp_path = extractor.step3_extract_ocean_by_points(
            mask_tif=water_mask_path, 
            points_shp=MY_POINTS_SHP
        )
            p_main.update(1)
        print("\n🎉 全部测试圆满完成！你可以去 outputs 文件夹里用 QGIS/ArcGIS 查看每一步的成果了。")
        
    except Exception as e:
        print(f"\n❌ 测试过程中断，错误原因: {e}")