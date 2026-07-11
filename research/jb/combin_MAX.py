from osgeo import gdal
import os
import glob
import time


def build_province_max_mosaic(input_dir, output_tif):
    """
    将指定文件夹下的所有掩码图，按照“最大值合成”逻辑拼接成一张大图。
    逻辑：将 0 视为透明 (NoData)，实现只要有潮滩就保留的效果。
    """
    start_time = time.time()

    # 1. 扫描所有预测结果 (.tif)
    print(f"🔍 正在扫描文件夹: {input_dir}")
    # 递归查找，匹配 _mask.tif (根据你的命名习惯)
    tif_files = glob.glob(os.path.join(input_dir, "**", "*_mask.tif"), recursive=True)

    if not tif_files:
        print("❌ 未找到任何 *_mask.tif 文件，请检查路径！")
        return

    print(f"📦 共找到 {len(tif_files)} 个影像文件，准备合成...")

    # 2. 构建 VRT (虚拟拼接)
    # 关键参数 srcNodata=0: 告诉 GDAL，输入文件里的 0 是透明的！
    # 这样，上下层叠加时，黑色背景不会盖住下面的白色潮滩。
    vrt_path = output_tif.replace(".tif", ".vrt")

    vrt_options = gdal.BuildVRTOptions(
        resampleAlg='nearest',  # 二值图必须用最近邻插值，防止出现非0非255的杂色
        srcNodata=0,  # ✅ 核心：将输入影像的0设为透明 -> 实现最大值叠加
        VRTNodata=0  # 输出的背景也是0
    )

    print("🛠️ 正在构建虚拟索引 (VRT)...")
    ds = gdal.BuildVRT(vrt_path, tif_files, options=vrt_options)

    if ds is None:
        print("❌ VRT 构建失败")
        return

    # 此时 ds 已经是拼接好的逻辑对象了，如果不需存盘，直接用 ds 也可以
    # 但为了方便分发，我们将其转存为实体 TIFF

    # 3. 转换为实体 BigTIFF
    print(f"🚀 正在生成实体 BigTIFF: {output_tif}")
    print("☕ 正在执行压缩和写入，请稍候 (取决于硬盘读写速度)...")

    translate_options = gdal.TranslateOptions(
        format="GTiff",
        creationOptions=[
            "BIGTIFF=YES",  # ✅ 允许文件超过 4GB (全省图很容易超)
            "TILED=YES",  # ✅ 分块存储，打开速度快
            "COMPRESS=LZW",  # ✅ LZW无损压缩 (二值图压缩率极高，文件会很小)
            "NUM_THREADS=ALL_CPUS",  # 多线程压缩
            "COPY_SRC_OVERVIEWS=YES"
        ],
        noData=0,  # 最终文件的 NoData 值设为 0
        callback=gdal.TermProgress_nocb  # 显示进度条
    )

    gdal.Translate(output_tif, ds, options=translate_options)

    # 释放内存，关闭文件
    ds = None

    # 4. (可选) 构建金字塔/快视图，让在 ArcGIS 里缩放不卡顿
    print("\n🏗️ 正在构建金字塔 (Pyramids)...")
    ds_new = gdal.Open(output_tif, 1)  # 1 = Read/Write
    ds_new.BuildOverviews("NEAREST", [2, 4, 8, 16, 32, 64])
    ds_new = None

    end_time = time.time()
    print(f"\n✅ 合成完成！")
    print(f"📂 输出文件: {output_tif}")
    print(f"⏱️ 耗时: {end_time - start_time:.2f} 秒")


if __name__ == "__main__":
    # ================= 配置区域 =================
    # 输入文件夹：你的 V17 代码输出的那个文件夹
    INPUT_FOLDER = r"E:\GEE_data\output_view_zhejiang1"

    # 输出文件：全省潮滩大图路径
    OUTPUT_FILE = r"E:\GEE_data\Zhejiang_Province_Max_Tidal_Flat.tif"
    # ===========================================

    # 确保输出目录存在
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    build_province_max_mosaic(INPUT_FOLDER, OUTPUT_FILE)