import os
import glob
import rasterio
from rasterio.windows import Window
from rasterio.transform import rowcol
from rasterio.mask import mask
import numpy as np
from tqdm import tqdm
import geopandas as gpd


# =======================================================
#  辅助函数：安全相加 (防止 uint8 溢出)
# =======================================================
def cv2_add_safe(a, b):
    """安全相加，防止 uint8 溢出 (255+1=0)"""
    res = a.astype(np.uint16) + b.astype(np.uint16)
    res = np.clip(res, 0, 255).astype(np.uint8)
    return res


# =======================================================
#  核心功能：合成并裁剪
# =======================================================
def generate_spatial_frequency_map_with_clip(input_folder, output_heatmap, shp_path=None):
    print(f"📊 正在执行空间感知频率统计: {input_folder}")

    # --- 阶段 1: 扫描并计算全省范围 ---
    tif_files = glob.glob(os.path.join(input_folder, "**", "*_mask.tif"), recursive=True)
    if not tif_files:
        print("❌ 未找到影像文件")
        return

    print(f"📦 找到 {len(tif_files)} 张影像，正在计算全省总范围...")

    min_x, min_y = float('inf'), float('inf')
    max_x, max_y = float('-inf'), float('-inf')
    ref_meta = None

    for tif in tqdm(tif_files, desc="Scanning Bounds"):
        with rasterio.open(tif) as src:
            if ref_meta is None:
                ref_meta = src.profile.copy()
            left, bottom, right, top = src.bounds
            min_x = min(min_x, left)
            min_y = min(min_y, bottom)
            max_x = max(max_x, right)
            max_y = max(max_y, top)

    res_x = ref_meta['transform'][0]
    res_y = -ref_meta['transform'][4]
    width = int((max_x - min_x) / res_x)
    height = int((max_y - min_y) / res_y)

    print(f"🌍 全省范围计算完毕: {width} x {height}")

    out_trans = rasterio.transform.from_origin(min_x, max_y, res_x, res_y)
    ref_meta.update({
        "height": height,
        "width": width,
        "transform": out_trans,
        "count": 1,
        "dtype": "uint8",
        "compress": "lzw",
        "bigtiff": "YES"
    })

    # --- 阶段 2: 空间拼接与累加 ---
    print("🚀 开始空间累加 (这可能需要一些时间)...")

    # 临时文件路径 (未裁剪的中间结果)
    temp_uncut_path = output_heatmap.replace(".tif", "_uncut.tif")

    with rasterio.open(temp_uncut_path, "w+", **ref_meta) as dst:
        for tif in tqdm(tif_files, desc="Accumulating"):
            with rasterio.open(tif) as src:
                src_data = src.read(1)
                binary_data = (src_data > 128).astype(np.uint8)

                if not np.any(binary_data): continue

                src_bounds = src.bounds
                row_start, col_start = rowcol(out_trans, src_bounds.left, src_bounds.top)

                h, w = binary_data.shape
                # 边界保护：防止计算出的窗口超出大图范围
                # (有时候浮点精度会导致超出1个像素)
                write_h = min(h, height - row_start)
                write_w = min(w, width - col_start)

                if write_h <= 0 or write_w <= 0: continue

                window = Window(col_start, row_start, write_w, write_h)

                # 读取大图对应位置
                current_val = dst.read(1, window=window)

                # 截取小图对应位置 (防止尺寸不匹配)
                binary_patch = binary_data[:write_h, :write_w]

                # 累加
                new_val = cv2_add_safe(current_val, binary_patch)
                dst.write(new_val, 1, window=window)

    print(f"✅ 初步合成完成，保存在临时文件: {temp_uncut_path}")

    # --- 阶段 3: 按最大水面(岸线)裁剪 ---
    if shp_path and os.path.exists(shp_path):
        print(f"✂️ 开始应用岸线裁剪: {os.path.basename(shp_path)}")
        try:
            # 读取矢量
            gdf = gpd.read_file(shp_path)

            with rasterio.open(temp_uncut_path) as src:
                # 检查坐标系
                if src.crs != gdf.crs:
                    print(f"   🔄 转换矢量坐标系: {gdf.crs} -> {src.crs}")
                    gdf = gdf.to_crs(src.crs)

                geoms = gdf.geometry.values

                # 执行裁剪
                # crop=True: 会把大图周围多余的空白切掉，让图变小一点，只保留有岸线的地方
                # nodata=0: 裁剪外的区域设为0
                out_image, out_transform = mask(src, geoms, crop=True, nodata=0)
                out_meta = src.meta.copy()

            # 更新元数据
            out_meta.update({
                "driver": "GTiff",
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": out_transform
            })

            # 保存最终结果
            with rasterio.open(output_heatmap, "w", **out_meta) as dest:
                dest.write(out_image)

            print(f"✅ 裁剪完成！最终干净结果: {output_heatmap}")

            # 删除临时文件 (可选，如果不想删就注释掉下面这行)
            os.remove(temp_uncut_path)
            print("🗑️ 已删除中间临时文件。")

        except Exception as e:
            print(f"❌ 裁剪失败: {e}")
            print(f"⚠️ 保留未裁剪版本: {temp_uncut_path}")
    else:
        print("⚠️ 未提供有效的 SHP 路径或文件不存在，跳过裁剪步骤。")
        # 如果不裁剪，直接重命名临时文件为最终文件
        if os.path.exists(temp_uncut_path):
            os.rename(temp_uncut_path, output_heatmap)
            print(f"✅ 已保存未裁剪结果: {output_heatmap}")


if __name__ == "__main__":
    # ================= 配置区域 =================
    # 输入：你的预测结果文件夹
    input_dir = r"E:\GEE_data\output_view_zhejiang3"

    # 输出：最终合成并裁剪好的全省大图
    output_map = r"E:\GEE_data\Zhejiang3_Final_Clipped.tif"

    # 岸线数据：师姐给的最大水面 Shapefile (.shp)
    # 这一步非常关键，请确保路径正确
    shp_path = r"E:\Code\服务器备份\GEE\jb\water-line\max_water_extent23.shp"
    # ===========================================

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_map), exist_ok=True)

    generate_spatial_frequency_map_with_clip(input_dir, output_map, shp_path)