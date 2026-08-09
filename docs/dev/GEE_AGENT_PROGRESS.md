# GEE_AGENT_PROGRESS.md — GEE 数据下载可信执行闭环 · 完成报告

> 里程碑：`feature/map-capability-aoi-milestone`，本轮第二个任务：**开发 GEE 数据下载的 Copilot 可信执行闭环**。
> 目标：**不重新实现 GEE 影像筛选逻辑**，把已有 `m4_engine` 下载代码接入
> `plan → validate → confirm → execute → verify → register → map → timeline → grounded reply` 体系，
> 并在下载完成后**不自动启动 GPU 推理**（推理仍走独立确认门闩）。

---

## 1. 原 GEE 调用链（审计结论）

```
侧栏「M4 海洋专题下载」按钮（历史直连路径）
  → 侧栏填 AOI 范围/日期/波段/阈值
  → m4_engine.run_m4_download(roi_path, roi_name, start_date, end_date, ...)
      ├─ gee_network_context(gee_proxy_url)        # 临时设置/移除代理环境变量
      ├─ ensure_ee_initialized(project=...)        # 无项目时 ee.Initialize() 直接失败
      ├─ _load_roi_geometry(roi_path, roi_name)    # 读 ROI 矢量
      ├─ 云端筛选：QA60 云掩膜 + Cloud Score+ + 水陆占比 + 像素阈值
      ├─ 按月分批查询 image id 列表（_date_chunks / _filter_chunk_ids）
      ├─ drive 模式：ee.batch.Export.image.toDrive(...).start()   # 任务异步，无人跟踪状态
      └─ local 模式：geemap.ee_export_image(...)                  # 逐景真实下载
```

关键事实：
- 入口 `m4_engine.run_m4_download`；真实下载依赖 `ee` / `geemap` 与 GEE 凭据。
- **无项目 ID 时新版 GEE API 直接报 `no project found`**（`ensure_ee_initialized` 已带提示）。
- drive 模式 `task.start()` 后**无人记录任务 id / 轮询状态**——历史工作流无法回答「下载到哪一步、是否成功」。
- 下载结果**不校验、不登记**，无法被后续推理工作流引用。

---

## 2. 新可信执行链

```
地图 AOI / 指令携带 AOI
  → build_gee_download_plan（gee_download_plan_v1：
      task/aoi/日期/集合/波段/云量/水陆占比/scale/export_to/代理/项目 + blockers+warnings+steps）
  → validate_gee_download_plan
      ├─ 凭证文件存在（不读取内容）
      ├─ project 多源解析（override → EE_PROJECT/GOOGLE_CLOUD_PROJECT/EARTHENGINE_PROJECT
      │    → ~/.config/earthengine/project|project_id → credentials.json 的 project 键）
      ├─ proxy 格式合法；AOI 合法（bbox 范围/面积）；日期合法；波段合法；输出目录可写
      └─ 真实 ee.Initialize（B4：初始化检查，失败 → blocker，不进入确认）
  → 用户确认（confirm_gee_download_plan：单确认门闩，plan_id 幂等，重复确认拒绝）
  → execute_gee_download
      ├─ _materialize_aoi_for_m4：AOI Polygon → 临时 .geojson（复用 m4_engine 加载）
      ├─ m4_engine.run_m4_download(..., on_task_started=记录账本)
      │    drive：每个 task.start() 后 ledger 登记 gee_task_id/description → 轮询 ee.data.getTaskStatus
      │    local：geemap.ee_export_image 逐景真实下载
      └─ ToolResult 只填真实数据（scene_count / local_tifs / gee_task_ids / export_state）
  → verify_gee_outputs（B8 清单）
      ├─ 本地 tif：存在/非空/可打开/CRS/尺寸/波段数==len(bands)/非全 NoData/mtime 新于开始
      └─ drive 无本地文件：export_state==COMPLETED 也不视为闭环完成（asset_ready=False）
  → register_gee_dataset_asset（仅验证通过才登记；B9：scene_count 必须进 metadata）
  → 加载地图 / 刷新动态能力 / 更新时间线（PHASES 增 GEE_EXPORT/WAIT_REMOTE/FETCH_OUTPUT）
  → Copilot 返回真实结果（summarize_gee_result_for_chat，明示「推理不会自动启动」）
  → 如需推理：单独发起推理任务（build_inference_plan 读取 dataset scene_count 做 A1 阻断）
```

状态机：`_gee_pending_plan → _gee_plan_confirmed(set)`；工具 `gee_download_plan` / `confirm_gee_download`。
时间线阶段：`PLAN,VALIDATE,CONFIRM,QUEUED,GEE_EXPORT,WAIT_REMOTE,FETCH_OUTPUT,EXECUTE,VERIFY,REGISTER,MAP,REPORT`。

---

## 3. 修改文件表

| 文件 | 改动 |
|---|---|
| `TF-agent/gee_agent_loop.py` | **新增**。计划生成/校验/确认/取消、真实执行（复用 m4_engine）、任务账本 ledger、输出校验、数据集资产登记、聊天摘要、Agent 上下文。~40 个常量/函数 |
| `TF-agent/m4_engine.py` | `run_m4_download` 增加 `on_task_started` 回调：drive 模式每个 `task.start()` 后调用（供账本记录任务 id/description） |
| `TF-agent/agent_command_bridge.py` | `propose_gee_plan` / `confirm_gee_plan`；`build_pending_task` 增 `run_gee_download` 分支（确认门闩 + plan_id 匹配）；`apply_system_command` 增 `propose_gee*` / `confirm_gee` 分支；`is_heavy` 含 `run_gee_download`；`ApplyResult` 增 `gee_plan`/`gee_plan_text`；侧栏 legacy M4 默认 5 波段不覆盖显式 bands（`_LEGACY_M4_BANDS`） |
| `TF-agent/app.py` | `_gee_worker_entry`（execute→verify→register）；`maybe_start_pipeline_thread` GEE 分支；后台管道收尾 GEE 处理（`_gee_handled` 守卫）；侧栏「GEE 影像下载计划」确认/取消区块；flush 展示计划与执行 toast |
| `TF-agent/task_timeline.py` | PHASES 增 `GEE_EXPORT, WAIT_REMOTE, FETCH_OUTPUT`（位于 EXECUTE 之前） |
| `TF-agent/capability_registry.py` | `_check_gee_download` 重写：多源 project 解析（env → project 文件 → credentials 键）；无项目 → UNAVAILABLE；无 gee 模块 → BLOCKED；否则 CONDITIONAL + evidence |
| `TF-agent/agent.py` | 注册工具 `gee_download_plan` / `confirm_gee_download`；系统提示口语速查更新（下载/GEE/下影像 → gee_download_plan，确认后执行，完成后不会自动启动推理） |
| `TF-agent/inference_agent_loop.py` | `build_inference_plan` 读取已登记 GEE 数据集的 `scene_count`：`sc < count_threshold` 时提前阻断（A1 复用）；plan 增 `input_asset_scene_count` |
| `tests/unit/test_gee_agent_loop.py` | **新增** 39 个单元测试（9 个测试类 + B12 集成 4 项） |

---

## 4. 真实 GEE 验收（端到端，2026-06 实测）

**环境**：Clash 代理 `http://127.0.0.1:7890`；GEE Cloud Project `ctfseg-481406`；凭据 `~/.config/earthengine/credentials`（refresh_token，含 earthengine/cloud-platform/drive/devstorage 权限）。

**输入**：福建泉州湾附近 AOI `bbox=(118.6,24.8,118.7,24.9)`（112 km²）；日期 `2024-10-01 ~ 2024-11-30`；集合 `COPERNICUS/S2_SR_HARMONIZED`；波段默认 `["B4","B3","B2"]`（RGB）；scale 10 m；云量上限 60；导出 `local`。

**真实调用链**：AOI → plan（ready=True）→ validate（真实 `ee.Initialize` 成功）→ confirm（重复确认被拒）→ execute（**真实下载 7 景**）→ verify（**每景 8 项全 PASS**）→ register → inference plan（仅构建，未启动 GPU）。

**输出**：
- 本地 GeoTIFF 7 景（如 `b14_acceptance_20241014T023549_20241014T024851_T50RPN.tif`，约 5.8~6.3 MB/景，1025×1121，**EPSG:32650**，3 波段 B4/B3/B2，valid_pixels 4000+）
- 数据集资产：`gee_b14_acceptance_b64f3004`（临时 registry），`scene_count=7`，bands `["B4","B3","B2"]`，`format=geotiff`，`role=auxiliary`，CRS/bbox/plan_id/task_id/local_files 齐全
- 推理计划：`ready=True`，`input_asset_scene_count=7`，`tool=local_tidal_flat_inference`——**到 WAITING_CONFIRMATION 即停，未做任何 GPU 推理**
- 摘要如实：`scene_count：7 景 · 本地资产校验：7 通过（LOCAL_ASSET_READY）· 推理不会自动启动`

> 临时脚本与临时 registry 均为验收产物，**未提交**。

---

## 5. 数据集资产登记（dataset asset）

- 登记入口 `register_gee_dataset_asset(plan, result, verification)`：**仅当 verification.ok==True 且 result.success==True 才登记**；同 plan_id 幂等（不重复登记）。
- `dataset_id = f"gee_{task_id}_{plan_id[:8]}"`；metadata 含：`asset_id/task_id/plan_id/source=open/provider=Earth Engine/collection/date_range/bands/index_bands/cloud_limit/scale/format=geotiff/role=auxiliary/coverage_scale=scene/primary_path/local_files/crs/bbox/aoi_summary/aoi_bbox/gee_task_id/export_to/drive_folder/scene_count/status=verified/created_at/registered_at`。
- **`scene_count` 来自真实 metrics**（local 模式 = 本地 tif 数与 id_list 取大；drive 模式 = 提交任务数），测试断言 `entry["scene_count"] == metrics["scene_count"]`。

---

## 6. 与推理工作流的衔接（不自动启动 GPU）

- `summarize_gee_result_for_chat` 明示：**「推理不会自动启动——如需潮滩推理，请回复『对 XX 做潮滩推理』以生成推理计划，确认后再执行。」**
- 推理侧 `build_inference_plan`：当 `input_asset_id` 指向已登记 GEE 数据集时，读取 `scene_count`；`sc < count_threshold`（如 cnt=2 而只有 1 景）→ 提前 blocker（A1 阻断复用），不加载模型、不探测设备、不创建输出。
- UI 侧：GEE 下载完成只登记资产 + 汇报，**不会**触发 `run_inference` 的 pending_task；用户需再发起推理任务并单独确认。

---

## 7. 测试结果

**命令 1**（GEE 闭环 39 项）：
```
D:\anaconda3\envs\gwx\python.exe -m pytest tests/unit/test_gee_agent_loop.py -q --tb=short -p no:cacheprovider
→ 39 passed
```
**命令 2**（全部单元测试 307 项，含原 268 + GEE 39）：
```
D:\anaconda3\envs\gwx\python.exe -m pytest tests/unit -q --tb=short -p no:cacheprovider
→ 307 passed
```
（exit code 1 仅为 PYTORCH_CUDA_ALLOC_CONF 弃用警告，与用例无关。）

测试类：`TestPlanBuild`（默认波段 RGB/缺 AOI/非法 AOI/坏日期/代理校验/非法波段/scale/导出目标/目录自动创建）、
`TestValidate`（ee 不可导入/项目缺失/项目可解析/无凭据时不泄漏敏感键）、
`TestConfirmGate`（未确认阻断/重复确认拒绝/plan_id 不匹配/参数变更换新 plan_id/桥接确认链路）、
`TestExecute`（账本防重启/任务轮询/失败原样透传/真实写文件/plan_id 隔离）、
`TestVerifyAndRegister`（scene_count 入 metadata/缺文件不登记/无 CRS 失败/全 NoData 失败/景数匹配/失败不登记）、
`TestSummarizeAndCapability`（摘要只报真实数据/失败如实/能力多源解析/推理 scene_count 阻断）、
`TestB12Integration`（时间线含 GEE 阶段/能力多源/agent 工具注册/桥接 propose→confirm 全链路）、
`TestLedgerPersistence`（账本往返持久化）。

---

## 8. 未完成事项 / 注意点

- **drive 模式未端到端验收**：本机未配置 Google Drive 绑定测试；`task.start()` → 账本 → `ee.data.getTaskStatus` 轮询链路已实现并有单测覆盖，但真实 Drive 导出需用户在 GEE Code Editor Tasks 观察。
- **UI 手工验收未做**：侧栏「GEE 影像下载计划」确认/取消按钮已接线，未在 Streamlit 上手工点击验收（本轮以脚本驱动真实链路）。
- **代理/凭据是硬前提**：无代理时 `oauth2.googleapis.com` 不可达；无 Cloud Project 时新版 GEE API 拒绝初始化（能力表显示 UNAVAILABLE，计划生成会列出 blocker）。
- **临时 registry 不入库**：B14 验收使用的 registry 为临时文件；正式运行由 app 侧 `DATASET_ASSETS_REGISTRY_PATH` 决定。
- 原始 `m4_engine.run_m4_download` 直连路径（侧栏 M4 下载）保留未动，本轮仅在其上增加 `on_task_started` 钩子。
