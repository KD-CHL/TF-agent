import os
import numpy as np
import rasterio
from rasterio.enums import Resampling
import cv2


RAW_DIR = r"G:\我的云端硬盘\GEE_DAILY_EXPORT"
MASK_DIR = r"G:\我的云端硬盘\GEE_PREDICT"
OUT_DIR = r"G:\我的云端硬盘\GEE_OVERLAY"

os.makedirs(OUT_DIR, exist_ok=True)

MASK_COLOR = (0, 0, 255)   # 红色
ALPHA = 0.4                # 掩码透明度


# ===========================
# 自适应亮度增强（关键）
# ===========================
def stretch_image(img, lower=2, upper=98):
    """
    对每个通道做 2%-98% 分位数线性拉伸，使图像变亮，接近GEE显示效果
    img: H×W×3 float32 (0-1)
    """
    result = np.zeros_like(img)
    for c in range(3):
        p_low = np.percentile(img[:, :, c], lower)
        p_high = np.percentile(img[:, :, c], upper)
        band = img[:, :, c]

        # 拉伸
        band = (band - p_low) / (p_high - p_low + 1e-6)
        band = np.clip(band, 0, 1)
        result[:, :, c] = band

    return (result * 255).astype(np.uint8)


def overlay_mask(raw_img, mask_img, color=(0,0,255), alpha=0.4):
    color_layer = np.zeros_like(raw_img)
    color_layer[mask_img == 255] = color
    return cv2.addWeighted(raw_img, 1, color_layer, alpha, 0)


def main():
    raw_files = [f for f in os.listdir(RAW_DIR) if f.endswith(".tif")]

    for raw_file in raw_files:
        raw_path = os.path.join(RAW_DIR, raw_file)
        mask_path = os.path.join(MASK_DIR, raw_file.replace(".tif", "_mask.tif"))

        if not os.path.exists(mask_path):
            print(f"[跳过] 未找到掩码文件：{mask_path}")
            continue

        print(f"[处理] {raw_file} ...")

        # ---- 读取原始影像 ----
        with rasterio.open(raw_path) as src_raw:
            raw = src_raw.read([1, 2, 3])  # B G R
            raw = np.moveaxis(raw, 0, -1)  # 转 H×W×3

        # 归一化（防止极值导致拉伸失败）
        raw = raw.astype(np.float32)
        raw /= (np.percentile(raw, 99.5) + 1e-6)
        raw = np.clip(raw, 0, 1)

        raw_vis = stretch_image(raw, lower=1, upper=99)

        # # 多段拉伸（先亮再增强对比）
        # raw_vis = gamma_correction(raw_vis, gamma=0.35)

        # 再做一次线性拉伸确保不偏灰
        raw_vis = cv2.normalize(raw_vis, None, 0, 255, cv2.NORM_MINMAX)

        # ---- 读取掩码 ----
        with rasterio.open(mask_path) as src_mask:
            mask = src_mask.read(1)

        # 尺寸不一致 → 重采样掩码
        if mask.shape != raw_vis.shape[:2]:
            with rasterio.open(mask_path) as src_mask:
                mask = src_mask.read(
                    1,
                    out_shape=(raw_vis.shape[0], raw_vis.shape[1]),
                    resampling=Resampling.nearest
                )

        # ---- 叠加 ----
        overlay_img = overlay_mask(raw_vis, mask, color=MASK_COLOR, alpha=ALPHA)

        # ---- 保存 PNG ----
        out_png = os.path.join(OUT_DIR, raw_file.replace(".tif", "_overlay.png"))
        cv2.imwrite(out_png, overlay_img)
        print("  ✔ PNG 输出：", out_png)

        # ---- 保存 GeoTIFF（保持地理信息） ----
        out_tif = os.path.join(OUT_DIR, raw_file.replace(".tif", "_overlay.tif"))
        with rasterio.open(raw_path) as src_raw:
            profile = src_raw.profile
            profile.update(count=3, dtype="uint8")

            with rasterio.open(out_tif, "w", **profile) as dst:
                dst.write(overlay_img[:, :, 0], 1)
                dst.write(overlay_img[:, :, 1], 2)
                dst.write(overlay_img[:, :, 2], 3)

        print("  ✔ GeoTIFF 输出：", out_tif, "\n")


if __name__ == "__main__":
    main()
