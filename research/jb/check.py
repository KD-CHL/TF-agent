import geopandas as gpd
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds
import numpy as np

def extract_and_rasterize_single_shp(
    national_shp_path: str,
    output_tif_path: str,
    extract_mode: str = 'attribute', # 'attribute' 或 'bbox'
    resolution_meters: float = 10.0,
    target_epsg: int = 3857
):
    print("🚀 步骤 1: 加载全国潮滩 SHP 数据...")
    # 读入全国数据
    national_gdf = gpd.read_file(national_shp_path)
    
    # 【极客建议】第一次跑的时候，把这行代码解开，看看你的 SHP 里到底有哪些列名！
    # print("查看数据属性列:", national_gdf.columns)
    # print(national_gdf.head(3))

    print(f"✂️ 步骤 2: 使用 {extract_mode} 模式提取目标区域...")
    
    if extract_mode == 'attribute':
        # ---------------------------------------------------------
        # 招式一：属性过滤 (假设你的列名叫 'PROVINCE'，值为 '江苏省')
        # 请根据 print(national_gdf.columns) 的结果修改这里的列名！
        # ---------------------------------------------------------
        column_name = 'PROVINCE' # 改成你实际的列名
        target_value = '江苏省'   # 改成你实际的值
        
        if column_name not in national_gdf.columns:
            raise ValueError(f"❌ 找不到列名 '{column_name}'，请先检查 SHP 属性表！")
            
        target_gdf = national_gdf[national_gdf[column_name] == target_value]

    elif extract_mode == 'bbox':
        # ---------------------------------------------------------
        # 招式二：经纬度框选 (如果 SHP 没有任何省份属性)
        # 这里大致框出了江苏省的经纬度范围 (如果是 WGS84 坐标系)
        # ---------------------------------------------------------
        min_lon, max_lon = 116.3, 122.0 # 江苏大致经度范围
        min_lat, max_lat = 30.7, 35.1   # 江苏大致纬度范围
        
        # 使用 cx 方法进行纯空间切片
        target_gdf = national_gdf.cx[min_lon:max_lon, min_lat:max_lat]
        
    else:
        raise ValueError("extract_mode 必须是 'attribute' 或 'bbox'")

    if target_gdf.empty:
        raise ValueError("❌ 提取结果为空！请检查属性名、属性值或经纬度范围是否正确。")

    print(f"🔄 步骤 3: 转换坐标系至 EPSG:{target_epsg} (为了按米计算分辨率)...")
    if target_gdf.crs.to_epsg() != target_epsg:
        target_gdf = target_gdf.to_crs(epsg=target_epsg)

    print("📏 步骤 4: 计算图像边界与矩阵...")
    minx, miny, maxx, maxy = target_gdf.total_bounds
    width = int(np.ceil((maxx - minx) / resolution_meters))
    height = int(np.ceil((maxy - miny) / resolution_meters))
    transform = from_bounds(minx, miny, maxx, maxy, width, height)

    print(f"🔥 步骤 5: 开始栅格化 (图像大小: {width} x {height})...")
    shapes = ((geom, 1) for geom in target_gdf.geometry)
    rasterized_image = rasterize(
        shapes=shapes,
        out_shape=(height, width),
        transform=transform,
        fill=0,
        all_touched=False,
        dtype=rasterio.uint8
    )

    print("💾 步骤 6: 写入带地理信息的 GeoTIFF 文件...")
    with rasterio.open(
        output_tif_path,
        'w',
        driver='GTiff',
        height=height,
        width=width,
        count=1,
        dtype=rasterio.uint8,
        crs=target_gdf.crs,
        transform=transform,
        compress='lzw'
    ) as out_tif:
        out_tif.write(rasterized_image, 1)
        
    print(f"✅ 搞定！文件已保存至: {output_tif_path}")


# ======= 使用示例 =======
if __name__ == "__main__":
    national_shp = "E:\潮滩数据集\师姐数据集\china_tidal_flat_projected_2020.shp"
    output_tif = "E:\潮滩数据集Jiangsu_Tidal_Flats.tif"
    
    # 强烈建议先用 'attribute' 模式，如果你知道属性列名的话！
    extract_and_rasterize_single_shp(
        national_shp_path=national_shp, 
        output_tif_path=output_tif, 
        extract_mode='bbox', # 如果不知道列名，直接用 bbox 模式切块
        resolution_meters=10.0
    )