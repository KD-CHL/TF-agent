import os
import glob
import numpy as np
import rasterio
from rasterio.errors import RasterioError
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks
from skimage.measure import label
import warnings
from tqdm import tqdm

warnings.filterwarnings("ignore")

class LowTideACWIExtractor:
    def __init__(self, workspace_dir: str):
        self.workspace = workspace_dir
        self.output_dir = os.path.join(workspace_dir, "outputs")
        os.makedirs(self.output_dir, exist_ok=True)
        print(f"📁 ACWI 低潮面频率合成工作区已初始化: {self.output_dir}")

    def _remedy_single_band_tif(self, in_path: str, out_path: str = None) -> str:
        """抢救 GIS 空白图：只保留第 1 波段数据，强制 count=1 重写（原 1.py）。"""
        if out_path is None:
            out_path = os.path.join(self.workspace, "step2_fixed.tif")
        with rasterio.open(in_path) as src:
            data = src.read(1)
            profile = src.profile
            profile.update(count=1)
            with rasterio.open(out_path, "w", **profile) as dst:
                dst.write(data, 1)
        return out_path

    def execute_frequency_analysis(self, input_folder: str):
        print("\n🚀 --- 启动低潮面专属流水线: 基于 WTFTFI+ACWI 的非水频率合成 ---")
        tif_files = glob.glob(os.path.join(input_folder, "*.tif"))
        if not tif_files:
            raise ValueError(f"❌ 在 {input_folder} 中没有找到 .tif 文件！")
        
        print(f"📦 找到 {len(tif_files)} 个影像，准备逐景提取陆地掩膜并累加...")
        
        sum_land_mask = None   
        count_valid_obs = None 
        profile = None
        skipped = []

        # 假设 M4 模块下载的波段顺序严格为 ['B8', 'B4', 'B3', 'B2', 'B11']
        # 对应 Rasterio 索引(从1开始): 1=B8, 2=B4, 3=B3, 4=B2, 5=B11
        for file in tqdm(tif_files, desc="📊 逐景 ACWI 计算", unit="景"):
            try:
                with rasterio.open(file) as src:
                    if profile is None:
                        profile = src.profile
                        # 累加器需要用 float32 防止溢出
                        sum_land_mask = np.zeros((src.height, src.width), dtype=np.float32)
                        count_valid_obs = np.zeros((src.height, src.width), dtype=np.float32)

                    b8 = src.read(1).astype(np.float32)  # NIR
                    b4 = src.read(2).astype(np.float32)  # RED
                    b3 = src.read(3).astype(np.float32)  # GREEN
                    b2 = src.read(4).astype(np.float32)  # BLUE
                    b11 = src.read(5).astype(np.float32) # SWIR1

                # ----------------------------------------------------
                # 1. 计算 WTFTFI (浑浊度前置裁判指数)
                # ----------------------------------------------------
                wtftfi_num = 3 * b4 - b11 - b8 - b2 - b3
                wtftfi_den = 5 * b8 * (np.abs(b8 - b4) + np.abs(b8 - b11))
                
                valid_mask = (wtftfi_den != 0) & (~np.isnan(b8))
                wtftfi = np.full(b8.shape, np.nan, dtype=np.float32)
                wtftfi[valid_mask] = wtftfi_num[valid_mask] / wtftfi_den[valid_mask]
                
                wtftfi_valid = np.where((wtftfi > -10) & (wtftfi < 10), wtftfi, np.nan)
                valid_tfi_data = wtftfi_valid[~np.isnan(wtftfi_valid)]
                
                if len(valid_tfi_data) == 0:
                    continue

                # ----------------------------------------------------
                # 2. 寻找 WTFTFI 分界阈值 (判断清水和浑水)
                # ----------------------------------------------------
                tfi_counts, tfi_bins = np.histogram(valid_tfi_data, bins=200, range=(-10, 10))
                tfi_centers = (tfi_bins[:-1] + tfi_bins[1:]) / 2
                tfi_smoothed = gaussian_filter1d(tfi_counts, sigma=2)
                
                max_tfi = np.nanmax(wtftfi_valid)
                if max_tfi < 0:
                    tfi_threshold = 0.0
                else:
                    peaks, _ = find_peaks(tfi_smoothed)
                    gt0_peaks = [p for p in peaks if tfi_centers[p] >= 0]
                    lt0_peaks = [p for p in peaks if tfi_centers[p] < 0]
                    
                    gt0_idx = gt0_peaks[0] if gt0_peaks else np.where(tfi_centers >= 0)[0][0]
                    lt0_idx = lt0_peaks[-1] if lt0_peaks else np.where(tfi_centers < 0)[0][-1]
                    
                    if lt0_idx < gt0_idx:
                        valley_offset = np.argmin(tfi_smoothed[lt0_idx:gt0_idx+1])
                        tfi_threshold = tfi_centers[lt0_idx + valley_offset]
                    else:
                        tfi_threshold = 0.0

                # ----------------------------------------------------
                # 3. 自适应计算 ACWI
                # ----------------------------------------------------
                swir_adaptive = np.where(wtftfi_valid > tfi_threshold, b11, b8)
                
                acwi_den = swir_adaptive + b3
                acwi = np.full(b8.shape, np.nan, dtype=np.float32)
                
                acwi_valid_mask = (acwi_den != 0) & (~np.isnan(swir_adaptive))
                acwi[acwi_valid_mask] = (swir_adaptive[acwi_valid_mask] - b3[acwi_valid_mask]) / acwi_den[acwi_valid_mask]
                acwi = np.where((acwi > -1) & (acwi < 1), acwi, np.nan)
                
                valid_acwi_data = acwi[~np.isnan(acwi)]
                if len(valid_acwi_data) == 0:
                    continue

                # ----------------------------------------------------
                # 4. 直方图导数寻找 ACWI 的水陆分割阈值
                # ----------------------------------------------------
                acwi_counts, acwi_bins = np.histogram(valid_acwi_data, bins=200, range=(-1, 1))
                acwi_centers = (acwi_bins[:-1] + acwi_bins[1:]) / 2
                acwi_smoothed = gaussian_filter1d(acwi_counts, sigma=2) 
                derivative = np.gradient(acwi_smoothed, acwi_centers)
                
                gt_zero_idx = np.where(acwi_centers >= 0)[0]
                acwi_threshold = 0.0 
                
                if len(gt_zero_idx) > 0:
                    peaks, _ = find_peaks(acwi_smoothed[gt_zero_idx])
                    if len(peaks) > 0:
                        first_peak_idx = gt_zero_idx[peaks[0]] 
                        derivative_before_peak = derivative[:first_peak_idx]
                        sign_changes = np.where((derivative_before_peak[:-1] < 0) & (derivative_before_peak[1:] > 0))[0]
                        if len(sign_changes) > 0:
                            acwi_threshold = acwi_centers[sign_changes[-1]]

                # ----------------------------------------------------
                # 5. 生成陆地(非水体)掩膜并累加
                # ----------------------------------------------------
                current_land_mask = np.zeros(acwi.shape, dtype=np.float32)
                current_land_mask[acwi >= acwi_threshold] = 1.0

                sum_land_mask[valid_mask] += current_land_mask[valid_mask]
                count_valid_obs[valid_mask] += 1.0

            except (RasterioError, OSError, ValueError) as e:
                skipped.append((file, repr(e)))
                # 🌟 修复1：在进度条中实时打印坏图警告
                tqdm.write(f"⚠️ 跳过无法读取的影像: {file}\n   {e!r}")

        if sum_land_mask is None:
            raise ValueError("❌ 处理失败！全部影像均无法读取或为全NaN。")

        # 🌟 修复2：将跳过的坏图名单写入本地日志文件
        if skipped:
            log_path = os.path.join(self.output_dir, "step2_skipped_tifs.txt")
            with open(log_path, "w", encoding="utf-8") as lf:
                for path, err in skipped:
                    lf.write(f"{path}\t{err}\n")
            print(f"⚠️ 已跳过 {len(skipped)} 个坏图，详情已写入: {log_path}")

        # ==========================================
        # Step 6: 计算非水体暴露频率 (0 - 100%)
        # ==========================================
        print("\n📈 正在计算季度/年度非水体暴露频率 (Probability)...")
        probability = np.full(sum_land_mask.shape, 0, dtype=np.float32) # 初始化为 0
        valid_pixels = count_valid_obs > 0
        
        # 乘以 100 转为百分比
        probability[valid_pixels] = (sum_land_mask[valid_pixels] / count_valid_obs[valid_pixels]) * 100.0

        # ==========================================
        # Step 7: 连通域去噪与阈值切片
        # ==========================================
        print("🧹 正在执行连通域去噪...")
        
        # 提取频率 > 3 的区域做连通域
        binary_for_denoise = (probability > 3).astype(np.uint8)
        labeled = label(binary_for_denoise, connectivity=2)
        unique, counts = np.unique(labeled, return_counts=True)
        
        # 找出 <=100 像素的小斑块
        small_labels = unique[counts <= 100]
        mask_small_patches = np.isin(labeled, small_labels)
        
        # 应用过滤：频率 <= 5 的归零，小斑块归零
        probability[probability <= 5] = 0
        probability[mask_small_patches] = 0

        # ==========================================
        # Step 8: 师姐的终极优化 (0-100 限制与 UInt8 压缩)
        # ==========================================
        print("🗜️ 正在对齐 0-100 数据尺度，并向下转码为 UInt8 (节省 75% 硬盘空间)...")
        
        # 强制将异常越界值拉回 0-100，并四舍五入
        probability = np.clip(np.round(probability), 0, 100)
        
        # 转为 8 位无符号整数
        probability_uint8 = probability.astype(np.uint8)

        # 更新 TIF 文件的元数据
        profile.update(
            count=1,
            dtype=rasterio.uint8,
            nodata=0,
            compress="lzw",
        )

        out_path = os.path.join(self.output_dir, "step2_final_low_tide_probability.tif")
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(probability_uint8, 1)

        fixed_path = self._remedy_single_band_tif(out_path)
        print(f"✅ 频率图已保存: {out_path}")
        print(f"✅ GIS 抢救版已保存: {fixed_path}")
        return fixed_path

if __name__ == "__main__":
    # 【配置区域】替换为存放你的 5 波段 TIF 的真实路径
    MY_TIF_FOLDER = r"H:\我的云端硬盘\20_5b_zhejiang1" 
    
    extractor = LowTideACWIExtractor(workspace_dir=r"E:\Code\GEE\YYnet\DATA\output2")
    
    try:
        prob_tif_path = extractor.execute_frequency_analysis(input_folder=MY_TIF_FOLDER)
    except Exception as e:
        print(f"\n❌ 运行异常: {e}")