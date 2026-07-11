import os
import ee
import geemap
import geopandas as gpd
from tqdm import tqdm  # 🌟 引入强力进度条库

# 初始化 GEE 环境
try:
    ee.Initialize()
except Exception as e:
    ee.Authenticate()
    ee.Initialize()

class M4_GEE_Downloader:
    def __init__(self, export_to="drive", local_out_dir="./M4_Downloads"):
        """
        初始化下载器
        :param export_to: 'drive' (导出到谷歌云盘, 推荐) 或 'local' (直接下载到本地电脑)
        :param local_out_dir: 如果选择 local，本地存放的文件夹路径
        """
        self.export_to = export_to.lower()
        self.local_out_dir = local_out_dir
        if self.export_to == 'local':
            os.makedirs(self.local_out_dir, exist_ok=True)
        print("🌍 M4 GEE 云端获取引擎已就绪！")

    def execute_download(self, 
                         roi_path: str, 
                         roi_name: str,
                         start_date: str, 
                         end_date: str,
                         bands: list = ['B8', 'B4', 'B3', 'B2', 'B11'],
                         cloud_limit: int = 60,
                         min_land_pct: float = 5.0,
                         max_land_pct: float = 95.0,
                         min_pixel_count: int = 1000,
                         drive_folder: str = "GEE_Downloads"):
        """
        执行高度自定义的数据筛选与下载任务
        """
        print(f"\n🚀 开始处理任务: [{roi_name}] | 时间: {start_date} 至 {end_date}")
        
        # 1. 加载 ROI
        if roi_path.endswith('.shp') or roi_path.endswith('.geojson'):
            print("📍 解析本地矢量边界...")
            gdf = gpd.read_file(roi_path).to_crs("EPSG:4326")
            roi_geom = ee.Geometry.Polygon(list(gdf.geometry[0].exterior.coords))
        else:
            print("📍 解析 GEE 云端 Asset 边界...")
            roi_geom = ee.FeatureCollection(roi_path).filter(ee.Filter.eq('name', roi_name)).geometry()

        # 2. 定义 S2 去云函数 (QA60)
        def maskS2clouds_QA60(image):
            qa = image.select('QA60')
            cloudBitMask = 1 << 10
            cirrusBitMask = 1 << 11
            mask = qa.bitwiseAnd(cloudBitMask).eq(0).And(qa.bitwiseAnd(cirrusBitMask).eq(0))
            return image.updateMask(mask).divide(10000).copyProperties(image, ["system:time_start"])

        # 3. 定义掩膜 0 值函数
        def maskZero(image):
            return image.updateMask(image.select('B8').gt(0))

        # 4. 定义 Cloud Score+ 去云函数
        def apply_CloudScore_Plus(image):
            mask = image.select('cs').lte(0.4)
            return image.updateMask(mask.Not())

        # 5. 定义指标计算与统计函数
        def calculate_indices_and_stats(image):
            # 计算 mNDWI
            mndwi = image.normalizedDifference(['B3', 'B11']).rename('mNDWI')
            mndwi = mndwi.updateMask(mndwi.gt(-1).And(mndwi.lt(1)))
            image = image.addBands(mndwi)

            # 计算总像素数
            pixels = image.select('B3').reduceRegion(
                reducer=ee.Reducer.count(),
                geometry=roi_geom,
                scale=10,
                maxPixels=1e13
            ).get('B3')
            image = image.set('pixel_count', ee.Number(pixels))

            # 计算陆地像素数 (mNDWI <= 0)
            land = image.updateMask(image.select('mNDWI').lte(0))
            land_pixels = land.select('mNDWI').reduceRegion(
                reducer=ee.Reducer.count(),
                geometry=roi_geom,
                scale=10,
                maxPixels=1e13
            ).get('mNDWI')
            
            # 计算陆地占比
            land_percent = ee.Number(land_pixels).divide(ee.Number(pixels).max(1)).multiply(100)
            return image.set('land_percent', land_percent)

        # ==========================================
        # 核心查询与过滤流水线
        # ==========================================
        print("☁️ 正在远程调用 GEE 服务器进行过滤筛选 (此步骤由云端并行计算，请稍候)...")
        
        # 基础集合
        s2_sr = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        s2_cs = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")

        # 初筛
        data_col = (s2_sr.filterBounds(roi_geom)
                         .filterDate(start_date, end_date)
                         .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_limit))
                         .map(maskS2clouds_QA60)
                         .map(maskZero)
                         .map(lambda img: img.clip(roi_geom)))

        # Link Cloud Score+
        cs_linked_col = data_col.linkCollection(s2_cs, 'cs').map(apply_CloudScore_Plus)

        # 指数计算与占比过滤
        final_col = (cs_linked_col.map(calculate_indices_and_stats)
                                  .filter(ee.Filter.gt('pixel_count', min_pixel_count))
                                  .filter(ee.Filter.lt('land_percent', max_land_pct))
                                  .filter(ee.Filter.gt('land_percent', min_land_pct)))

        # 获取最终符合条件的影像数量
        try:
            image_count = final_col.size().getInfo()
            print(f"🎯 过滤完成！共筛选出 {image_count} 景高质量影像。")
        except Exception as e:
            print(f"❌ 获取影像数量失败，请检查网络代理。详情: {e}")
            return

        if image_count == 0:
            print("⚠️ 未找到符合条件的影像，请放宽过滤条件。")
            return

        # ==========================================
        # 🌟 进度条优化核心：一口气拉取全部影像 ID 列表
        # ==========================================
        print("📋 正在获取云端影像清单以配置进度条...")
        id_list = final_col.aggregate_array('system:index').getInfo()

        # ==========================================
        # 导出机制 1: 提交到云盘 (Drive)
        # ==========================================
        if self.export_to == 'drive':
            print(f"📤 正在批量向 Google Drive 提交 {image_count} 个任务...")
            
            # 使用 tqdm 包裹 ID 列表，实时展现任务提交进度
            for img_id in tqdm(id_list, desc="🚀 提交 Drive 任务", unit="个"):
                # 根据 ID 精确过滤出该景影像
                img = final_col.filter(ee.Filter.eq('system:index', img_id)).first()
                
                task = ee.batch.Export.image.toDrive(
                    image=img.select(bands),
                    description=f"{roi_name}_{img_id}",
                    folder=drive_folder,
                    region=roi_geom.getInfo()['coordinates'],
                    scale=10,
                    maxPixels=1e13,
                    fileFormat='GeoTIFF'
                )
                task.start()
            print(f"\n✅ 全部 {image_count} 个任务已成功推送到 GEE 官方后台调度中心！")
            print("💡 提示：此时云端已开始异步生成，你可以去 GEE 网页版的 Tasks 面板查看实时切片进度。")
            
        # ==========================================
        # 导出机制 2: 直接下载到本地 (Local)
        # ==========================================
        elif self.export_to == 'local':
            print(f"📥 正在直接下载 {image_count} 景影像至本地硬盘 (已支持断点续传)...")
            
            # 弃用不透明的内置批量函数，改用单景可监测循环，实现颗粒度极细的进度条
            for img_id in tqdm(id_list, desc="📥 下载本地影像", unit="景"):
                out_tif = os.path.join(self.local_out_dir, f"{roi_name}_{img_id}.tif")
                
                # 🛠️ 断点续传防护：如果本地已经下载好了，直接秒跳过，防止重新等
                if os.path.exists(out_tif):
                    continue
                
                img = final_col.filter(ee.Filter.eq('system:index', img_id)).first()
                try:
                    geemap.ee_export_image(
                        img.select(bands), 
                        filename=out_tif, 
                        scale=10, 
                        region=roi_geom, 
                        file_per_band=False
                    )
                except Exception as e:
                    tqdm.write(f"⚠️ 影像 [{img_id}] 下载失败，已自动跳过。错误: {e}")
                    
            print(f"✅ 本地无损下载流执行完毕！目标路径: {self.local_out_dir}")

# ==========================================
# 🎯 M4 自定义任务执行入口
# ==========================================
if __name__ == "__main__":
    # 配置引擎: 选择 'drive' (云端离线异步) 或 'local' (本地直接下载)
    m4 = M4_GEE_Downloader(export_to="drive")
    
    m4.execute_download(
        roi_path=r"E:\Data\CHINA_tf_city\china_costal.shp", 
        roi_name="zhejiang2",
        start_date="2020-01-01",
        end_date="2020-01-15",
        bands=['B8', 'B4', 'B3', 'B2', 'B11'],
        cloud_limit=60,         
        min_pixel_count=1000,   
        min_land_pct=5.0,       
        max_land_pct=95.0,      
        drive_folder="test"
    )