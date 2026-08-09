# GEE 数据下载 · 可信执行闭环设计（B1 审计 + 新链路设计）

> 本文档回答 B1 审计的 20 项检查，记录原 GEE 调用链的真实行为，
> 并给出新 `gee_agent_loop.py` 可信闭环的接入点。基于对以下文件的实读：
> `TF-agent/m4_engine.py`、`TF-agent/app.py`、`TF-agent/agent.py`、
> `TF-agent/agent_command_bridge.py`、`TF-agent/dataset_assets.py`、
> `TF-agent/dataset_assets_registry.json`、`TF-agent/capability_registry.py`、
> `TF-agent/task_timeline.py`、`TF-agent/aoi_context.py`、`TF-agent/aoi_map_bridge.py`、
> `research/jb/M4.py`、`gee/jb/scan_task_raw_rasters.py`。

---

## 1. 原 GEE 调用链（真实行为）

```
用户侧栏「GEE 下载」按钮 / Copilot assist_gee_download
        │
        ▼
app.py 侧栏 (ui_m4_*) → pending_action {type:"run_m4", confirmed:true, m4_params:{...}}
        │
        ▼
agent_command_bridge.build_pending_task(run_m4 分支 L744-797)
  · confirmed 校验（_m4_plan_confirmed 或 action.confirmed，否则拒绝）
  · 组装 m4 配置：{roi_path, roi_name, start_date, end_date, export_to,
                  local_out_dir, drive_folder, bands, cloud_limit, min_land_pct,
                  max_land_pct, min_pixel_count, scale, gee_proxy_url, gee_project_id}
        │
        ▼
app.py maybe_start_pipeline_thread → mode="m4" → _pipeline_worker_entry
        │
        ▼
app.py run_m4_download_sync(ctx, shared, stop_event)
  · import m4_engine；ctx["m4"] 直接传入
        │
        ▼
m4_engine.run_m4_download(...)
  ├─ gee_network_context(gee_proxy_url)   # 空 → 清代理直连(VPN)；非空 → 设置显式代理
  ├─ ensure_ee_initialized(project)       # _resolve_ee_project 多来源解析
  ├─ _load_roi_geometry(roi_path, roi_name)
  ├─ COPERNICUS/S2_SR_HARMONIZED + GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED
  ├─ QA60 + CloudScore+ + 0 值掩膜 + 陆地占比过滤 + 按月分块
  ├─ export_to=drive  → ee.batch.Export.image.toDrive(...); task.start()
  └─ export_to=local  → geemap.ee_export_image(...) 同步写本地
```

### 1.1 关键真实行为（审计结论）

| # | 审计项 | 现状（真实代码） | 问题 |
|---|--------|------------------|------|
| 1 | EE 初始化入口 | `ensure_ee_initialized`（m4_engine L83）：全局 `_EE_INIT_KEY` 防重复初始化 | 只有 `(proxy, project)` 键，进程内多次调用安全 |
| 2 | project_id 解析 | `_resolve_ee_project`（L20）：override → env `EE_PROJECT`/`GOOGLE_CLOUD_PROJECT`/`EARTHENGINE_PROJECT` → `~/.config/earthengine/project\|project_id` → credentials 中 project/project_id/cloud_project | ✅ 多来源；但 capability 只查 env `GEE_PROJECT`，不一致 |
| 3 | proxy | `gee_network_context`（L52）：空 url → pop 全部代理 env 直连；非空 → 设 HTTP/HTTPS 代理 | ✅ 空值=直连(VPN)，与测试环境一致 |
| 4 | ROI | `_load_roi_geometry`（L156）：shp/geojson → gpd → 4326 → name 过滤 → dissolve → `geemap.gdf_to_ee`；否则 ee FeatureCollection | ✅ 但必须本地文件，AOI（地图框选 GeoJSON）无对应路径 |
| 5 | 数据集 | `COPERNICUS/S2_SR_HARMONIZED` + `GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED`（linkCollection "cs"） | ✅ 固定两个集合 |
| 6 | S2 collection 版本 | SR_HARMONIZED（L2A 表面反射，除以 10000） | ✅ 固定 |
| 7 | 波段 | **默认 `["B8","B4","B3","B2","B11"]`** | ⚠️ **与推理需求不一致**：pre_engine 模型需 RGB 顺序（tif band1=R=B4, band2=G=B3, band3=B=B2）；默认顺序 B8 开头会导致推理取到 NIR |
| 8 | 云量过滤 | `CLOUDY_PIXEL_PERCENTAGE < cloud_limit` + QA60 bit10/11 + `cs <= 0.4` | ✅ 三层 |
| 9 | 时间范围 | `filterDate(start,end)` + `_date_chunks` 按月 31 天分块 | ✅ |
| 10 | scale | 默认 10，侧栏可选 10/20/30 | ✅ |
| 11 | 导出目标 | `export_to`: drive（toDrive, folder=drive_folder）或 local（geemap.ee_export_image） | ✅ |
| 12 | task.start() | drive 模式 `task.start()` 有调用 | ✅ |
| 13 | task.id 保存 | **未保存**：`id_list` 只存 image 的 `id()`（影像 system:id），不是 EE 批量任务 ID；本地不存任务状态 | ⚠️ **B7 核心缺口** |
| 14 | status 轮询 | **无**：drive 任务提交后即结束，用户去 GEE Tasks 手动看 | ⚠️ **B7 核心缺口** |
| 15 | 下载定位 | local → `os.path.join(local_out_dir, f"{roi_name}_{img_id}.tif")`；drive → 用户手动同步到 `root_dir/drive_folder` | ⚠️ 无本地文件清单，无法 verify |
| 16 | 输出目录 | `local_out_dir`（默认 `./M4_Downloads`）/ `drive_folder`（默认 GEE_Downloads） | ✅ |
| 17 | dataset registry schema | `dataset_assets.py`：source ∈ {advisor,open,other}、role、format、coverage_scale、primary_path（须为现有文件）等 | ✅ GEE 资产可登记，需 primary_path=首个本地 tif |
| 18 | 硬编码路径 | `app.py` 默认 `_default_aoi=r"E:\Data\CHINA_tf_city\china_costal.shp"`；`DEFAULT_CLASH_PROXY=http://127.0.0.1:7892` | ⚠️ 默认路径仅 UI 占位，不得进入 plan |
| 19 | m4_gee_proxy 使用 | bridge `m4_gee_proxy`→`ui_m4_gee_proxy`→ctx m4→`run_m4_download(gee_proxy_url=...)` | ✅ 真实传递 |
| 20 | 失败处理 | 0 影像 → `ValueError("未找到符合条件的影像…")`；初始化失败 → `RuntimeError`（带 hint）；网络/超时 → `_gee_error_hint` | ⚠️ error_message 需**原样**结构化保存，不得改写为成功 |

### 1.2 原链路缺口汇总（新闭环必须补齐）

1. **无 plan/confirm 门闩**：`_m4_plan_confirmed` 是一次性 bool，多个 pending 可能串用；改参数不生成新 plan_id。
2. **drive 任务不可追踪**：task.id 未保存、无轮询、无恢复（页面 rerun 后会再 task.start()）。
3. **无本地文件验证**：GEE COMPLETED ≠ 本地文件可用于推理。
4. **无 dataset 资产登记**：下载结果不进 dataset_assets_registry.json，inference 无法读取 scene_count。
5. **波段与推理需求脱节**：默认 [B8,B4,B3,B2,B11]，而 pre_engine 需要顺序 RGB（B4,B3,B2）。
6. **capability 与真实 project 解析不一致**：`_check_gee_download` 只看 env `GEE_PROJECT`，而 `_resolve_ee_project` 有多来源。

---

## 2. 新 GEE 可信执行闭环设计

### 2.1 时间线阶段（B11，复用现有 framework）

`task_timeline.PHASES` 增加：`GEE_EXPORT`、`WAIT_REMOTE`、`FETCH_OUTPUT`。
全链：`PLAN → VALIDATE → CONFIRM → QUEUED → GEE_EXPORT → WAIT_REMOTE → FETCH_OUTPUT → VERIFY → REGISTER → MAP → REPORT`。

### 2.2 Plan 结构（B3 spec 给定）

```json
{
  "schema": "gee_download_plan_v1",
  "plan_id": "<uuid4.hex>",
  "task_id": "<aoi 或任务名>",
  "tool": "gee_download",
  "aoi_id": "<AOI id>",
  "geometry": {"type": "Polygon", ...},          // internal only，不注入 LLM
  "bbox": [west, south, east, north],
  "dataset": "COPERNICUS/S2_SR_HARMONIZED",
  "date_start": "YYYY-MM-DD",
  "date_end": "YYYY-MM-DD",
  "bands": ["B4", "B3", "B2"],                   // 推理真实需要：顺序 RGB
  "index_bands": ["B8", "B11"],                  // 后处理/指数需要（如 mNDWI）
  "cloud_threshold": 60,
  "scale": 10,
  "gee_project": "<project id>",
  "export_target": "drive | local",
  "output_dir": "<本地目录>",
  "expected_output_type": "raster_dataset",
  "blockers": [],
  "warnings": [],
  "status": "waiting_confirmation"
}
```

> **波段铁律（B3）**：默认 `bands=["B4","B3","B2"]`（顺序保证 tif band1=R、band2=G、band3=B，匹配 pre_engine 的 RGB [1,2,3] 输入）；`index_bands=["B8","B11"]` 用于 mNDWI 等（m4_engine 内部仍需 B8 做 0 值掩膜、B3/B11 算 mNDWI —— 这两项由 m4_engine 内部 select 完成，导出波段只取用户 bands；若用户要指数文件再含 index_bands）。**不偷偷改变已有数据格式**：local 导出用 `geemap.ee_export_image(file_per_band=False)` 保持单文件多波段 GeoTIFF。

### 2.3 Validate（B4）

- EE：`ee` 可导入（不加载到模块顶层）；credential/project 解析（复用 `m4_engine._resolve_ee_project` 语义，不输出凭证）；初始化只发生在 execute，不在 validate 真实连接。
- proxy 格式：空 或 `^(https?://)[^/]+(:\d+)?$`。
- AOI：存在 / valid / geometry 为 Polygon / bbox 合法（经度 -180..180，纬度 -90..90）/ 面积 > 0 / 面积 > 阈值 → warning；**不把巨大 GeoJSON 放入 LLM prompt**（只放 `compact_summary`）。
- 时间：`date_start < date_end`、`%Y-%m-%d` 格式、跨度异常巨大（> 5 年）→ warning。
- 数据：collection 名在白名单（`COPERNICUS/S2_SR_HARMONIZED`）；bands 均在 S2 波段表内；cloud 0..100；scale ∈ {10,20,30}；输出目录可写（写探针）。
- 规模预估：AOI 面积 km² + 日期跨度天数 → 轻量估算可能影像数（如 面积/10000km² × 跨度天/5 天），仅 warning 不入 blocker。

### 2.4 Confirm（B5）

- `_gee_plan_confirmed` 为 set（与 inference 一致），同一 plan_id 只确认一次。
- 未确认绝不 `task.start()`。
- 用户修改 AOI/时间/bands/cloud/scale → 重新 build 得新 plan_id，旧 plan 失效（confirm 校验 plan_id 匹配）。

### 2.5 Execute（B6 + B7）

- 真实调用 `m4_engine.run_m4_download`（复用，不重写）。不 Fake success。
- drive 模式：捕获 `task.id()` 写入任务 ledger；轮询 `ee.batch.Task` 状态（READY/RUNNING/COMPLETED/FAILED/CANCELLED）；error_message 原样保存。
- local 模式：同步导出，直接验证本地文件。
- 最小任务持久化 `TF-agent/data/gee_task_ledger.json`：
  `{task_id, plan_id, gee_task_id, status, created_at, last_checked_at}`。
  rerun 时若存在 gee_task_id → 查状态 → 更新时间线，**不重新 task.start()**。
  进程重启恢复：本地 ledger 低成本 → 支持（读 ledger 恢复 status 并轮询）。

### 2.6 Verify（B8）

区分 `GEE_EXPORT_COMPLETED`（云端）vs `LOCAL_ASSET_READY`（本地文件可用于推理）。
本地 GeoTIFF 检查：存在 / 非空 / rasterio 可开 / CRS / width,height>0 / band count == 计划 bands 数 / 每 band 有 metadata / bbox 与 AOI 合理空间关系 / 非全 NoData / mtime 属于当前任务窗口。

### 2.7 Dataset asset 登记（B9）

`dataset_assets.register_dataset`（source="open"，format="geotiff"，coverage_scale="scene"，role="auxiliary"，primary_path=首个本地 tif）+ 扩展键：
`task_id, plan_id, collection, date_range, bands, cloud_threshold, scale, aoi_summary, gee_task_id, local_files, crs, bbox, scene_count, created_at, status`。
**scene_count 必须进 metadata**（inference 的 `scene_count >= count_threshold` 直接读取）。

### 2.8 与 inference 衔接（B10 + B12）

- 下载完成不自动启动 GPU 推理；只更新 capability：`local_tidal_flat_inference` 在无本地输入时 BLOCKED，下载并登记后 AVAILABLE（evidence 含 scene_count）。
- Copilot 建议「已获得 N 景…可以继续执行潮滩推理」，用户确认后再建 inference plan。
- inference plan 的 `input_asset_id` 可指向 dataset asset；`build_inference_plan` 读取其 `scene_count` 用于 A1 阻断。

### 2.9 文件修改清单

| 文件 | 改动 |
|------|------|
| `TF-agent/gee_agent_loop.py` | **新增**：plan/validate/confirm/execute/verify/register/summarize/context |
| `TF-agent/task_timeline.py` | PHASES 增加 GEE_EXPORT/WAIT_REMOTE/FETCH_OUTPUT |
| `TF-agent/agent_command_bridge.py` | propose_gee/confirm_gee 分支、build_pending_task run_gee_download、HEAVY_ACTION_LABELS、is_heavy |
| `TF-agent/agent.py` | `gee_download_plan` / `confirm_gee_download` 工具 |
| `TF-agent/app.py` | 侧栏 GEE 计划展示 + 确认按钮 + `_gee_worker_entry` 后台线程 + 收尾写回 |
| `TF-agent/capability_registry.py` | gee_download 使用 `_resolve_ee_project` 语义；inference capability 数据就绪状态 |
| `TF-agent/inference_agent_loop.py` | A1 单景阻断（scene_count >= count_threshold） |
| `TF-agent/post_engine.py` | A2 缓存 fingerprint manifest |
| `tests/unit/test_gee_agent_loop.py` | **新增** 20~30 测试 |

### 2.10 已知边界（不实现）

- AutoTune / 数据库 / 多用户 / 批量全国下载 / 多 AOI 并发 / Redis / Celery。
- 不做「一键自动连续执行」：每个重型阶段保持用户确认。
- 不做大规模 UI 重构。
