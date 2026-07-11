# 🚨 潮滩系统：全方位细粒度测试与交互体验诊断报告

**生成时间**：2026-06-29  
**工作区**：`e:\Code\GEE`  
**测试入口**：`python run_ux_stress_tests.py` + `python run_e2e_diagnostics.py`  
**共享 UX 模块**：`cstf_ux.py`

---

## 📊 一、测试与自愈成果

| 指标 | 结果 |
|------|------|
| **系统整体测试状态** | **全部通过**（UX 压力 5/5 + E2E 回归 13/13） |
| **体验升级脚本数量** | **6 个**（见下表） |
| **引入的防御性地学机制** | 空几何交集拦截器、拓扑自愈（make_valid/buffer(0)）、分母防零护盾（safe_div/safe_pct_change）、差集运算 try-except 降级 |
| **重构的交互设计** | 跨平台路径归一化、缺夹自创目录、专家处方式报错（💔🩺💊）、控制台分隔线与中文温和提示 |

### 体验升级涉及文件

| 文件 | 升级内容 |
|------|----------|
| `cstf_ux.py` | **新建** 共享 UX/地学防御工具库 |
| `jb/M5.py` | 空重叠安全退出、路径清洗、拓扑修复、CLI 防爆屏 |
| `YYnet-main/m5_engine.py` | 与 M5 对齐的防御逻辑 + 路径归一化 |
| `jb/E1.py` | 空重叠 IoU=0 跳过、拓扑自愈日志、目录自动创建、分母护盾 |
| `YYnet-main/app.py` | 资产账本路径 normpath、空 task 友好报错 |
| `jb/combine.py` | 优先导入 YYnet-main（避免旧模块污染） |

---

## ⚠️ 二、系统未开发/缺失功能客观审计

> **未进行越权开发**，仅记录对体验的影响。

### 1. PDF 一键报告生成

- **所属模块**：报告生成
- **缺失表现**：无 PDF 导出代码（仅有 E1 JSON/MD、M5 JSON）
- **体验影响**：用户需手动转换报告格式，无法「一键交付 PDF」

### 2. 独立 M3.py

- **所属模块**：M3 空间融合
- **缺失表现**：逻辑在 `M1.1.py` / `index_engine.py`，无独立入口
- **体验影响**：文档引用 M3 时用户找不到对应脚本，CLI 帮助不完整

### 3. M4 GEE 下载离线 E2E

- **所属模块**：M4
- **缺失表现**：依赖 GEE 账号/VPN，无法在沙盒全自动压测
- **体验影响**：GEE 失败时用户仍可能看到原始 Traceback（M4 尚未接入 `cstf_ux` 专家报错）

### 4. pre_engine GPU 推理 UX

- **所属模块**：深度学习推理
- **缺失表现**：`pre_engine.py` 仍使用硬编码路径与 emoji print，未接入统一 UX 层
- **体验影响**：Windows GBK 控制台偶发 emoji 编码问题；路径手抖仍可能失败

### 5. assets_registry.json Schema 校验

- **所属模块**：资产账本
- **缺失表现**：损坏 JSON 静默回退 `{}`
- **体验影响**：缓存命中异常时用户只看到「无缓存」，难以定位账本损坏

---

## 🐛 三、体验级与极限 Bug 诊断修复实录

### Bug #1: 路径拼写容错低导致 FileNotFoundError

- **受影响的文件**：`jb/M5.py`、`YYnet-main/m5_engine.py`、`jb/E1.py`、`app.py`
- **体验缺陷描述**：路径末尾多 `\`、混用 `/`、带引号时，系统直接找不到文件。
- **病因分析**：未使用 `normpath` / `abspath` 清洗用户输入。
- **代码重构对比**：
  ```python
  # 修复前
  gdf = gpd.read_file(baseline_shp)

  # 修复后（cstf_ux.normalize_path）
  baseline_shp = ux.normalize_path(baseline_shp, must_exist=True)
  gdf = gpd.read_file(baseline_shp)
  ```
- **验证状态**：✅ UX 测试「M5 路径尾斜杠容错」通过。

---

### Bug #2: 极端无重叠区域导致 M5 仍做差集/除零风险

- **受影响的文件**：`jb/M5.py`、`YYnet-main/m5_engine.py`
- **极限测试表现**：杭州湾基线 vs 125°E 远离要素，旧版仍 overlay 并可能产生误导指标。
- **病因分析**：缺少 union 级空间交集前置判断。
- **代码重构对比**：
  ```python
  if not ux.geometries_have_overlap(union_base, union_curr):
      ux.warn(ux.zero_overlap_message_m5(), log)
      return self._zero_overlap_report(...)  # change_rate=0.00%
  change_rate = ux.safe_pct_change(area_curr, area_base, default=0.0)
  ```
- **验证状态**：✅ UX「M5 零重叠安全退出」+ E2E「M5 边界: 空交集」通过。  
  控制台输出含：**「检测到两期空间要素无任何重叠区域，自动将变化率计为 0.00%…」**

---

### Bug #3: E1 无重叠仍跑昂贵栅格化

- **受影响的文件**：`jb/E1.py`
- **极限测试表现**：目标 SHP 与参考层无交集时仍分块栅格化，耗时长且 IoU 含义不明。
- **病因分析**：缺少矢量级 `_gdf_spatial_overlap` 前置路由。
- **代码重构对比**：
  ```python
  if not self._gdf_spatial_overlap(target_gdf, ref_gdf):
      ux.warn(ux.zero_overlap_message_e1(pair_name), print)
      metrics = {..., "jaccard_iou": 0.0, "skipped_raster_compare": True}
      # 安全跳过，不中断主流程
  ```
- **验证状态**：✅ UX「E1 零重叠 IoU=0 跳过」通过。

---

### Bug #4: 自相交脏多边形导致拓扑异常

- **受影响的文件**：`jb/E1.py`（`normalize_vector` / `load_dataset`）
- **体验缺陷描述**：bowtie 多边形导入后 overlay/rasterize 可能失败，用户面对 Shapely 英文报错。
- **病因分析**：未统一调用拓扑修复。
- **代码重构对比**：
  ```python
  gdf = ux.repair_geometries(gdf, logger=print)
  # 控制台：「拓扑自愈：发现 N 个无效几何，正在 make_valid / buffer(0) 修复…」
  ```
- **验证状态**：✅ UX「E1 脏拓扑自愈」+ E2E「E1 边界: 脏多边形」通过。

---

### Bug #5: 分母为零潜在 ZeroDivisionError（M5 紧凑度/面积变化率）

- **受影响的文件**：`jb/M5.py`（旧版第 66、72 行）
- **病因分析**：基线面积或紧凑度为 0 时直接除法。
- **代码重构对比**：
  ```python
  change_rate = ux.safe_pct_change(area_curr, area_base, default=0.0)
  compactness_base = ux.safe_div(4 * np.pi * area_base, union_base.length ** 2, default=0.0)
  ```
- **验证状态**：✅ 已通过 `_stats_to_metrics` / M5 全链路隐式验证。

---

### Bug #6: CLI 直接暴露 Python Traceback

- **受影响的文件**：`jb/M5.py` `__main__`
- **体验缺陷描述**：文件不存在时用户看到整屏 Traceback。
- **代码重构对比**：
  ```python
  if __name__ == "__main__":
      sys.exit(ux.run_cli_main(_main, "M5 异常检测"))
  # 输出 💔 / 🩺 / 💊 三段式处方；CSTF_DEBUG=1 才显示堆栈
  ```
- **验证状态**：✅ 手动验证 FileNotFoundError 路径。

---

### Bug #7: 输出目录不存在导致崩溃

- **受影响的文件**：`jb/M5.py`、`jb/E1.py`
- **修复**：统一 `ux.ensure_dir()`，静默创建 `outputs_m5_advanced`、`E1_unified` 等层级。
- **验证状态**：✅ 删除目录后重跑测试，自动重建。

---

### Bug #8: combine.py 污染 sys.path 导致 post_engine 加载旧版

- **受影响的文件**：`jb/combine.py`（上轮 E2E 已修，本轮保留）
- **修复**：优先插入 `YYnet-main` 再 `YYnet`。
- **验证状态**：✅ E2E 13/13 回归通过。

---

## ⚠️ 四、边界情况验证矩阵

| 场景 | 模块 | 预期 | 实测 |
|------|------|------|------|
| 两期矢量完全不相交 | M5 | 变化率 0.00%，安全 JSON | ✅ |
| 两期矢量完全不相交 | E1 | IoU=0，跳过栅格化 | ✅ |
| WGS84 vs UTM 混用 | M5 | 自动投影 | ✅ |
| 自相交 bowtie | E1 | make_valid 修复 | ✅ |
| 路径尾 `\` 与 `/` 混用 | M5 | normalize_path 矫正 | ✅ |
| union/像元 union=0 | E1 | safe_div → IoU=0 | ✅ |
| 未选 Streamlit 任务 | app.py | 中文错误提示，不 crash | ✅ |

---

## 🚀 五、本地复现

```powershell
conda activate gwx
cd e:\Code\GEE

# 生成沙盒（若尚未生成）
python generate_comprehensive_test_data.py

# UX 极限压力测试（5 项）
python run_ux_stress_tests.py

# 全模块回归（13 项）
python run_e2e_diagnostics.py
```

期望：**5/5 + 13/13 全部通过**。

---

## 📁 六、后续建议（非本次实施）

1. 将 `cstf_ux` 接入 `pre_engine.py`、`M4.py` CLI 入口，统一专家报错体验。
2. 合并 `YYnet` 与 `YYnet-main` 双份引擎，消除路径污染根因。
3. PDF 报告若立项，单独开需求，不在本轮越权开发。

---

*本报告遵循「缺失功能只记录、已有代码可深度优化」红线生成。*
