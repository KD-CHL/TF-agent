# 🚨 潮滩解译与分析系统：全功能自愈性诊断与审计日志

**生成时间**：2026-06-29  
**工作区**：`e:\Code\GEE`（主产品 `YYnet-main`，研究脚本 `jb`）  
**测试沙盒**：`e:\Code\GEE\_e2e_sandbox`（由 `generate_comprehensive_test_data.py` 生成）  
**自动化入口**：`python generate_comprehensive_test_data.py` → `python run_e2e_diagnostics.py`

---

## 📊 一、测试与自愈成果总览

| 指标 | 结果 |
|------|------|
| **系统整体测试状态** | **全部通过**（13/13） |
| **自动扫描并运行的已有脚本/模块** | **13 项**（见下表） |
| **发现并自动修复的 Bug 总数** | **4 个** |
| **越权开发缺失功能** | **0**（严格遵守红线，仅记录） |

### 本次测试覆盖的模块

| # | 测试项 | 对应模块/文件 | 结果 |
|---|--------|---------------|------|
| 1 | 资产账本 JSON 读写与路径拦截 | `app.py` / `assets_registry.json` | ✅ |
| 2 | M5 jb 三维度告警 | `jb/M5.py` | ✅ |
| 3 | M5 engine 自动基线匹配 | `YYnet-main/m5_engine.py` | ✅ |
| 4 | M5 边界：CRS 自动对齐 | `jb/M5.py` | ✅ |
| 5 | M5 边界：空几何相交 | `jb/M5.py` | ✅ |
| 6 | E1 像元级 IoU 对比 | `jb/E1.py` | ✅ |
| 7 | E1 engine 封装层 | `YYnet-main/e1_engine.py` | ✅ |
| 8 | E1 边界：脏多边形拓扑 | `jb/E1.py` `normalize_vector` | ✅ |
| 9 | evaluation_geo AOI 裁剪 | `YYnet-main/evaluation_geo.py` | ✅ |
| 10 | jb/combine TIF vs SHP | `jb/combine.py` | ✅ |
| 11 | post_engine 时空合成 | `YYnet-main/post_engine.py` | ✅ |
| 12 | globe_server 健康检查 | `YYnet-main/globe_server.py` | ✅ |
| 13 | app.py 空 task 防御 | `YYnet-main/app.py` | ✅ |

### 已实现功能清单（扫描结论）

| 模块 | 位置 | 状态 |
|------|------|------|
| M1 海面提取 | `jb/M1_1.1.py` + `index_engine._m1_pipeline` | ✅ 已实现 |
| M2 ACWI 频率 | `jb/M1_1.2.py` + `index_engine._m2_pipeline` | ✅ 已实现 |
| M3 空间融合 | `jb/M1.1.py` + `index_engine._fuse_and_rasterize` | ✅ 逻辑存在，**无独立 M3.py** |
| M4 GEE 下载 | `jb/M4.py` + `m4_engine.py` | ✅ 已实现（需 GEE 凭证/VPN，未在本次沙盒 E2E 中跑通） |
| M5 时空异常 | `jb/M5.py` + `m5_engine.py` | ✅ 已实现 |
| E1 多源一致性 | `jb/E1.py` + `e1_engine.py` | ✅ 已实现 |
| 深度学习推理 | `pre_engine.py` + `post_engine.py` | ✅ 已实现 |
| 指数法推理 | `index_engine.py` | ✅ 已实现 |
| AutoTune | `auto_tune.py` | ✅ 已实现（需真实 Mask，未纳入沙盒 E2E） |
| 资产账本 | `assets_registry.json` + `app.py` | ✅ 已实现 |
| 数据集资产库 | `dataset_assets.py` | ✅ 已实现 |
| 远程演示网关 | `cstf_gateway.py` + ngrok 脚本 | ✅ 已实现 |
| 智能体 | `agent.py` | ✅ 已实现（需 API Key，未纳入沙盒 E2E） |

---

## ⚠️ 二、未实现/缺失功能审计清单

> 以下功能**仅客观记录，未进行越权开发**。

### 1. **PDF 一键报告生成**

- **所属模块**：报告生成
- **现状评估**：全工作区 Python 代码中**未发现** `reportlab` / `fpdf` / `weasyprint` / `PdfPages` 等 PDF 输出逻辑。
- **对系统的影响**：E1 输出 JSON + Markdown，M5 输出 JSON；无法直接交付 PDF 格式报告，需人工转换或后续单独开发。

### 2. **独立 M3.py 模块**

- **所属模块**：M3 空间融合
- **现状评估**：融合逻辑分散在 `jb/M1.1.py` 与 `index_engine.py`，无命名为 M3 的独立脚本。
- **对系统的影响**：文档/流程若引用「M3」需映射到上述文件，否则新人易误解为缺失。

### 3. **assets_registry.json 模式校验**

- **所属模块**：资产账本管理
- **现状评估**：`load_asset_registry()` 读取失败时静默返回 `{}`；README 中 schema 校验仍为 TODO。
- **对系统的影响**：损坏或手改的 JSON 可能导致缓存命中异常，难以第一时间发现。

### 4. **jb/combine.py、jb/check.py 未集成进 YYnet-main**

- **所属模块**：精度评价 / 真值裁剪
- **现状评估**：仅存在于 `jb/`，Streamlit 主应用使用 `auto_tune` + `evaluation_geo`，未直接调用 `combine.py`。
- **对系统的影响**：命令行评价与 UI 评价路径不一致，维护两套逻辑。

### 5. **M4 GEE 下载端到端沙盒测试**

- **所属模块**：M4 前置数据
- **现状评估**：依赖 Earth Engine 账号、VPN、真实 ROI；无法在离线沙盒中完整 E2E。
- **对系统的影响**：M4 代码存在但未在本次自动化中验证 GEE 侧连通性。

### 6. **pre_engine 深度学习推理端到端**

- **所属模块**：M1 前置（CDNet 推理）
- **现状评估**：需 GPU + 真实 `.pth` 权重 + 大尺寸 TIF；沙盒仅模拟小图，未跑完整 CDNet forward。
- **对系统的影响**：推理链路在 UI 中可用，但 CI 无法在无 GPU 环境验证。

### 7. **E1.py `__main__` 演示块**

- **所属模块**：E1
- **现状评估**：`target_path=None` 直接运行会只做开源产品互比，需手动填路径。
- **对系统的影响**：独立运行脚本时易误以为「无输出/报错」。

---

## 🐛 三、已有缺陷诊断与修复详情

### Bug #1: 未选目标任务时推理线程崩溃（`actual_task=None`）

- **受影响的文件/模块**：`YYnet-main/app.py` → `run_pipeline_sync` / `run_index_pipeline_sync`
- **原始 Traceback 报错**：
  ```text
  TypeError: join() argument must be str, bytes, or os.PathLike object, not 'NoneType'
  File "app.py", line 743, in run_pipeline_sync
    input_dir = os.path.join(root_dir, actual_task)
  ```
- **病因诊断 (Root Cause Analysis)**：侧栏「未发现可用任务」时 `selected_task=None`，但「运行模型推理」仍可触发，`pending_task` 将 `task=None` 传入后台线程，`os.path.join` 无法拼接 `None`。
- **代码修复对比 (Code Diff)**：
  ```python
  # 修复前：直接进入 join
  input_dir = os.path.join(root_dir, actual_task)

  # 修复后：增加防御
  if not actual_task or not root_dir:
      push_status("error", "❌ 未选择有效目标任务，或原始影像目录未配置。请在侧栏选择任务后再运行推理。")
      return False
  input_dir = os.path.join(root_dir, actual_task)
  ```
- **验证状态**：✅ 已修复，E2E 测试「app.py 空 task 防御」通过。

---

### Bug #2: post_engine 误将输出 SHP 当作岸线裁剪栅格读取

- **受影响的文件/模块**：`YYnet-main/post_engine.py` → `generate_double_constraint_complete`
- **原始 Traceback 报错**（测试复现时）：
  ```text
  rasterio.errors.RasterioIOError: ...Final_p0.05_c2.shp: TIFFReadDirectory:Failed to read directory
  File "post_engine.py", line 252, in generate_double_constraint_complete
    gdf = gpd.read_file(shp_path)
  ```
- **病因诊断**：当 `shp_path` 与 `output_path` 相同时，阶段 3 在 SHP 尚未生成或格式不对时尝试 `gpd.read_file` 作为岸线裁剪掩膜；读失败时整个合成返回 `False`。另：旧版 `YYnet/post_engine.py` 无此防护。
- **代码修复对比**：
  ```python
  # 修复前
  if shp_path and os.path.exists(shp_path):
      gdf = gpd.read_file(shp_path)

  # 修复后
  if shp_path and os.path.exists(shp_path) and os.path.normpath(shp_path) != os.path.normpath(final_shp_path):
      try:
          gdf = gpd.read_file(shp_path)
          ...
      except Exception as e:
          logger(f"   ⚠️ 岸线裁剪矢量读取失败，跳过裁剪: {e}")
          clip_mask = None
  ```
- **验证状态**：✅ 已修复，沙盒 `post_engine 时空合成` 通过。

---

### Bug #3: E1 未对自相交脏多边形做拓扑修复

- **受影响的文件/模块**：`jb/E1.py` → `normalize_vector`
- **原始现象**：导入自相交 bowtie 多边形时，后续 `unary_union` / `rasterize` 可能抛出 `TopologicalError` 或产生空几何。
- **病因诊断**：清洗流水线只过滤 `is_empty`，未调用 `make_valid` / `buffer(0)`。
- **代码修复对比**：
  ```python
  # 修复后（节选）
  from shapely.validation import make_valid
  gdf["geometry"] = gdf.geometry.apply(
      lambda g: make_valid(g) if g is not None and not g.is_valid else g
  )
  ```
- **验证状态**：✅ 已修复，E2E「E1 边界: 脏多边形」通过。

---

### Bug #4: combine.py 将旧版 YYnet 插入 sys.path 首位，污染 post_engine 导入

- **受影响的文件/模块**：`jb/combine.py`；间接影响 `post_engine` 测试及任何依赖 `evaluation_geo` 的 jb 脚本
- **原始现象**：E2E 全量运行时在 `combine` 测试之后 `post_engine` 返回 `False`；单独运行 `post_engine` 则成功。根因：`import post_engine` 加载了 **`YYnet/post_engine.py`（旧版）** 而非 **`YYnet-main/post_engine.py`（新版）**。
- **代码修复对比**：
  ```python
  # combine.py 修复前
  sys.path.insert(0, str(_YYNET))  # 仅 YYnet

  # 修复后：优先 YYnet-main
  for _pkg in (_YYNET_MAIN, _YYNET):
      if _pkg.is_dir() and str(_pkg) not in sys.path:
          sys.path.insert(0, str(_pkg))
  ```
- **验证状态**：✅ 已修复，13/13 E2E 全通过。

---

## ⚠️ 四、边界情况验证（Edge Cases）

| 边界情况 | 测试方式 | 预期行为 | 实测结果 |
|----------|----------|----------|----------|
| **空几何体相交** | M5：`baseline_shp`（杭州湾） vs `disjoint_shp`（125°E 远离） | 不崩溃，完成告警 JSON | ✅ 通过 |
| **坐标系未对齐** | M5：WGS84 基线 vs UTM 当期 | 自动 `to_crs` 到 UTM 51N | ✅ 通过 |
| **Shapely 拓扑无效** | E1：bowtie 自相交 SHP → `normalize_vector` | `make_valid` 修复后可入库 | ✅ 通过 |
| **NoData / 背景 TIF** | 沙盒 `test_nodata.tif`（大面积 0 值） | 生成成功，供后续 ETL 使用 | ✅ 已生成（未单独断言过滤逻辑） |
| **空 task 推理** | `app.py` 静态检查 + 运行时 guard | 友好错误提示，不 crash | ✅ 通过 |
| **资产账本中间产物拦截** | 检查 registry 不含 `_NUMERATOR` / `_DENOMINATOR` | 不注册中间 TIF | ✅ 通过 |

---

## 📁 五、测试资产说明（沙盒）

运行 `generate_comprehensive_test_data.py` 后生成：

- `assets_registry.json`：模拟 `24zhejiang1` 的 DL + Index 成果登记
- `rasters/test2020_final.tif`、`test2024_final.tif`：二值潮滩栅格（值 1=潮滩）
- `output/20zhejiang1/`、`output/24zhejiang1/`：两期 Final SHP
- `data_root/师姐数据集/`、`FCS30/`、`DCTF_18N/`：E1 多源对比用矢量
- `vectors/dirty_bowtie.shp`、`disjoint_far.shp`：拓扑与空交集边界用例
- `input/24zhejiang1/*_mask.tif`：post_engine 合成用成对 Mask

---

## 🚀 六、复现与后续建议

### 本地复现 E2E

```powershell
conda activate gwx
cd e:\Code\GEE
python generate_comprehensive_test_data.py
python run_e2e_diagnostics.py
```

期望输出：`=== 完成: 13/13 通过 ===`，结果写入 `_e2e_sandbox/e2e_results.json`。

### 建议（非本次实施）

1. 统一 `YYnet` 与 `YYnet-main`，避免双份 `post_engine.py` 漂移。
2. 为 `assets_registry.json` 增加 JSON Schema 校验。
3. 若需 PDF 报告，单独立项，不在本次自愈范围内开发。
4. M4 / pre_engine 在有 VPN + GEE + GPU 的环境中做人工冒烟测试。

---

*本日志由自动化诊断流程生成，遵循「缺失功能只记录、不填补」红线约束。*
