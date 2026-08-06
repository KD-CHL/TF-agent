# 里程碑进展：地图控制闭环 · 动态能力状态 · 统一任务时间线 · AOI 双向交互 · PDF 报告

更新时间：2026-08-06
基线分支：`feature/map-capability-aoi-milestone`
设计文档：`docs/dev/MAP_CAPABILITY_AOI_DESIGN.md`（§14 为 E 阶段 PDF 最小接入设计）

## 总览

| 阶段 | 主题 | Commit | 状态 |
|------|------|--------|------|
| A | Cesium 地图 + Copilot 闭环 | `d2d5ab2` | ✅ 完成 |
| B | 能力注册表（capability_registry） | `1be33b4` | ✅ 完成 |
| C | 统一任务时间线（task_timeline） | `b0b1a96` | ✅ 完成 |
| D | AOI 双向交互（aoi_context） | `5b01c8e` | ✅ 完成 |
| E | PDF 报告最小接入（reportlab） | `14002a3` | ✅ 完成 |
| E+ | 同门 PDF 成果报告集成（matplotlib+rasterio，双引擎） | 未提交（本轮） | ✅ 完成 |

约束遵守：不 push、不改 M5/E1 算法、`git add .` 禁用、排除 `Untitled` / `analyze_shp_minimal.py` / `analyze_tidal_datasets.py` / `scan_tidal_data.py`。

---

## 阶段 A — Cesium 地图控制 + Copilot 闭环（commit `d2d5ab2`）

- **设计**：§1–3（Viewer 生命周期、`CSTF_MAP_V1` 消息协议、相机预设、Agent 命令调用链）。
- **实现**：`TF-agent/app.py`（Cesium iframe 嵌入 + 消息桥接）；`TF-agent/map_commands.py`（Agent 地图命令 → iframe 消息）；`TF-agent/globe_server.py` / `globe_engine.py`（127.0.0.1:8765 地图服务）。
- **测试**：单元（相机预设、消息协议、命令解析）+ E2E。
- **验证**：浏览器 8501 实测地图加载、相机跳转、图层叠加。
- **风险**：iframe 每次 rerun 重建 → 采用缓存签名复用；已按 §1.2 生命周期规则固化。
- **结论**：闭环可用；`session_state.map_center` 为相机状态唯一驱动。

## 阶段 B — 动态能力状态（commit `1be33b4`）

- **设计**：§4（数据结构、状态判定、检查分层 cheap/expensive、安全、消费方）。
- **实现**：`TF-agent/capability_registry.py`（能力注册表 + 缓存 + 过期）；app.py 侧栏「能力状态」expander；Copilot 白名单摘要（evidence 布尔化，密钥不落缓存）。
- **测试**：注册/查询/过期/缓存命中单元测试；E2E 覆盖。
- **验证**：能力面板实时反映推理/下载/分析能力可用性。
- **风险**：昂贵检查阻塞 rerun → cheap/expensive 分层 + 缓存；已固化。
- **结论**：B 阶段完成，无敏感信息外泄。

## 阶段 C — 统一任务时间线（commit `b0b1a96`）

- **设计**：§5（事件结构、阶段语义 M5/E1 映射、存储与恢复、UI）。
- **实现**：`TF-agent/task_timeline.py`（`TimelineEvent` 顶层 `task_id` + `details`；`QUEUED→RUNNING→SUCCEEDED/FAILED` 事件流；`timeline_ledger.json` 磁盘恢复）；app.py 时间线 expander（事件按时间倒序、状态图标、进度百分比）。
- **测试**：事件入栈/恢复/去重/顺序单元测试；E2E。
- **验证**：重启进程后时间线从 ledger 恢复，标注「历史记录（进程重启后恢复），非实时状态」。
- **风险**：事件与 `pipeline_shared` 状态双源 → 单向同步（时间线只读消费），不混合。
- **结论**：C 阶段完成，M5/E1 行为零改动。

## 阶段 D — AOI 双向交互（commit `5b01c8e`）

- **设计**：§6–8（AOI 结构、Cesium 侧工具、Python 侧 `validate_aoi`、Copilot 上下文注入、地图回声）。
- **实现**：`TF-agent/aoi_context.py`（AOI 校验/归一化/序列化）；Cesium 侧画笔与回声；Copilot 上下文携带 AOI。
- **测试**：AOI 校验边界（多边形闭合/经度纬度范围/重叠）单元测试；E2E 双向消息。
- **验证**：地图框选 → 侧栏 AOI 面板 + Copilot 上下文同步；Copilot 指令 → 地图回声。
- **风险**：非法 AOI（自交/越界）→ `validate_aoi` 明确报错不吞异常；已固化。
- **结论**：D 阶段完成。

## 阶段 E — PDF 报告最小接入（commit `14002a3`）

- **设计**：§14（reportlab 纯 Python 方案；中文字体探测；真实数据禁止编造；`generate_task_report` 适配器接口；去重；无 token/无绝对路径；「生成 PDF 报告」按钮入口）。
- **实现**：`TF-agent/report_generator.py`（`generate_task_report` → `ReportResult`，时间线 + 能力 + 资产 + 任务结果）；app.py `_build_pdf_report` + 时间线 expander「📄 生成 PDF 报告」按钮。
- **测试**：report_generator 单测；E2E 覆盖。
- **验证**：`report_20fujian1_e0bfbadcb1db.pdf`（53KB）真实生成；同 task 配置去重返回已有路径。
- **风险**：中文字体缺失 → 探测 `C:/Windows/Fonts/msyh.ttc`，缺失则清晰降级；截图失败 → warning 不阻断。
- **结论**：E 阶段完成，与 A–D 全绿基线无回归。

---

## 阶段 E+ — 同门 PDF 成果报告集成（本轮，未提交）

**需求**：集成同门 `E:\Code\pdf`（`report_engine.py`）成果报告功能，并作适当优化；保持既有 reportlab 任务报告引擎不动（双引擎并存）。

### 设计

- **双引擎并存**：`report_generator.py`（reportlab，任务报告）不动；新增 `asset_report_engine.py`（集成 `E:\Code\pdf\report_engine.py`，matplotlib + rasterio + geopandas，成果资产报告）。
- **适配器接口**：`generate_asset_report(task, asset_key=None, output_dir=None, registry_path=None, ref_shp=None, progress_callback=None) -> AssetReportResult`（`success/task_id/report_path/sections/warnings/error`）。
- **数据来源**：`assets_registry.json` 中已入库 Final TIF（`final_*.tif` + 文件存在性过滤，按 `created_at` 倒序）；参考真值经 `dataset_assets.list_datasets(role="reference_truth", format="shapefile")` 按 `_task_year` 匹配（如 `24fujian2 → advisor_china_tidal_flat_2024`）。
- **报告内容**：栅格统计（rasterio 逐块、纬度余弦面积近似）+ TIF 预览图（红 [220,62,52] / 深底 [28,34,42]）+ 与参考 SHP 的 IoU/P/R/F1 对比。
- **入口**：时间线 expander「🗺️ 生成成果报告」按钮（与「📄 生成 PDF 报告」并列双列）；任务来源：时间线事件顶层 `task_id` → `details.task` 回退 → 侧栏 `selected_task` 兜底。

### 优化（相对同门原始实现）

1. **永不抛异常**：`generate_asset_report` 全程 try/except，异常归一化为 `AssetReportResult(error=...)`。
2. **可选依赖降级**：`_HAS_RASTERIO/_HAS_MATPLOTLIB/_HAS_GEOPANDAS` 守卫，缺失时返回可读错误而非 ImportError 崩溃。
3. **路径消毒**：`_safe_filename` 剥离路径穿越（`..`/分隔符）、`:`→`_`，输出固定于 `data/reports/`。
4. **结果去重**：`task_id|asset_key|tif_mtime` MD5 哈希；已存在且新 → 复用已有文件（浏览器实测「已存在同资产报告，返回已有文件」）。
5. **进度回调**：`progress_callback(pct, msg)` 供 UI 接入。
6. **无绝对路径泄漏**：报告内路径转相对；错误消息不包含完整本地路径。
7. **UI 消息持久化**：修复 `st.fragment(run_every=2.5)` 定时局部 rerun 清除 ephemeral `st.success/st.warning` 的问题 —— 改用 `session_state` + `st.markdown` 持久渲染结果与下载按钮，用户生成后可稳定看到结果与「⬇️ 下载成果报告」按钮。

### 实现

- 新增 `TF-agent/asset_report_engine.py`（~600 行，端口 + 加固）。
- 修改 `TF-agent/app.py`：`_build_asset_report()` + 时间线双列按钮 + 持久消息渲染。
- 新增 `tests/unit/test_asset_report_engine.py`（13 个用例）。

### 测试

- **新增单测 13/13 通过**：资产过滤、成功路径、进度回调、去重、无资产失败、依赖缺失失败、结果结构、无绝对路径泄漏、safe_filename、`compute_raster_stats`（EPSG:4326 面积估算、40×30 夹具潮滩像元==200）、`render_tif_preview`（背景和==104、红通道 220）。
- **全量回归 231 passed**（218 基线 + 13 新增）。
- **E2E 13/13 PASS**、**压力 5/5 PASS**。

### 验证（真实数据）

- `generate_asset_report('24fujian2')`：success=True、7 个 section、146KB、13.8s、warnings=[]；参考真值匹配 `advisor_china_tidal_flat_2024`，结果与 2024 参考真值一致。
- 合成夹具数据：7 页、139KB、2020 参考真值。
- `20fujian1`（推理失败、输出目录空）：返回「任务 20fujian1 无已入库 Final TIF 资产（assets_registry.json）」，不崩溃。

### 浏览器验收（localhost:8501，Streamlit 运行中）

| 场景 | 结果 |
|------|------|
| 时间线 expander 内「🗺️ 生成成果报告」按钮可见 | ✅ |
| 失败路径：点击 → 显示「⚠️ 成果报告生成失败：任务 20fujian1 无已入库 Final TIF 资产」+ 说明 caption | ✅（持久显示） |
| 成功路径：临时注入真实 TIF 资产 → 点击 → 「✅ 成果报告已生成」+ 报告路径 + 「⬇️ 下载成果报告」按钮 | ✅（9s 后仍显示，fragment rerun 不清除） |
| 去重：同资产二次生成 → 复用已有文件（「已存在同资产报告，返回已有文件」） | ✅ |
| 消息持久化：fragment 每 2.5s 局部 rerun 后结果与下载按钮仍可见 | ✅ |
| 任务解析：时间线顶层 `task_id`（20fujian1）正确读取（修复自 `details.task` 空回退问题） | ✅ |
| 测试后 registry 恢复原状（124 keys，无注入残留） | ✅ |

### 修复

- `_build_asset_report` 任务来源：原读 `details.task`（实际恒为空）→ 改为顶层 `task_id` 优先、`details.task` 回退、`selected_task` 兜底。
- ephemeral 消息被 `st.fragment(run_every=2.5)` 定时 rerun 清除 → session_state + markdown 持久化。

### 风险与结论

- 风险：报告基于 registry 资产，推理失败任务无资产 → 明确报错（预期行为）；大 TIF 预览渲染耗时（真实 TIF 约 10–25s）→ 进度回调 + 去重已缓解。
- 结论：E+ 集成完成、优化落地、双引擎并存、无回归；待提交（不 push）。

---

## 最终验证汇总（本轮收尾）

- 单元：**231 passed**（40.5s）。
- E2E：**13/13 PASS**；压力：**5/5 PASS**。
- 真实数据资产报告：`24fujian2` 7 页 146KB，匹配 2024 参考真值。
- 浏览器：成功/失败/去重/持久化四场景全过（见上表）。
- Git 待提交：`TF-agent/asset_report_engine.py`（新）、`TF-agent/app.py`（改）、`tests/unit/test_asset_report_engine.py`（新）；运行时产物（`data/timeline_ledger.json`、`data/reports/*.pdf`）已被 gitignore 排除。
