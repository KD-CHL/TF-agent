# INFERENCE_AGENT_DESIGN.md — 本地潮滩推理的可信执行闭环

> 范围：只做「本地潮滩推理」的可信执行闭环。不开发 GEE 下载，不扩展 M5/E1，不优化 PDF。
> 目标：不重新实现潮滩算法，把已有推理/后处理代码接入 `plan → validate → confirm → execute → verify → register → map → timeline → grounded reply` 体系。

---

## 1. 审计结论（真实调用链）

```
侧栏「运行模型推理」按钮 / Copilot dispatch(confirmed=true)
  → st.session_state.pending_task = {task, prob, cnt, mode, force_rerun}
  → rerun → maybe_start_pipeline_thread()（L3969，pipeline_thread_started 防重入）
  → threading.Thread(_pipeline_worker_entry) → run_pipeline_sync(ctx, shared, stop_event)（L1065）
      ├─ pre_engine.load_model(model_path, device)          # CDNet resnet50, weights_only=True
      ├─ 对 input_dir 下每个 *.tif（排除 _mask/Final）:
      │    pre_engine.process_geotiff(...)                  # 1024 patch / 512 overlap, RGB bands[1,2,3]
      │    → mask_root/{task}/{fname}_mask.tif
      ├─ post_engine.generate_double_constraint_complete(
      │      source_folder, mask_folder, output_path, shp_path,
      │      prob_threshold, min_absolute_count)            # → Final TIF + Final SHP（同 stem）
      ├─ register_asset(task, prob, cnt, final_shp)         # assets_registry.json
      └─ _run_m5_phase / _run_e1_phase（成功后可选联动）
```

回答审计问题（18 项）的关键事实：

| # | 问题 | 结论 |
|---|---|---|
| 1 | 推理入口 | `pre_engine.process_geotiff`（经 `run_pipeline_sync`） |
| 2 | 后处理入口 | `post_engine.generate_double_constraint_complete` |
| 3 | 按钮启动 | `pending_task` + `maybe_start_pipeline_thread` 单线程防重入 |
| 4 | 输入目录 | `root_dir/{task}/`，任务名 = root_dir 子目录（task_options） |
| 5 | 影像格式 | `*.tif`（排除 `_mask`/`Final` 前缀） |
| 6 | 波段 | RGB 3 波段，固定取 bands `[1,2,3]` |
| 7 | 波段顺序 | 代码固定（不探测，按 RGB 顺序） |
| 8 | 权重来源 | 侧栏 `ui_model_path`（默认硬编码 `E:\Code\GEE\best_train_loss_model_resnet50.pth`） |
| 9 | 设备 | `cuda if torch.cuda.is_available() else cpu`（自动） |
| 10 | 掩膜输出 | `mask_root/{task}/{fname}_mask.tif` |
| 11 | 后处理产物 | `_NUMERATOR.tif`/`_DENOMINATOR.tif` 缓存 + Final TIF + Final SHP |
| 12 | Final TIF | `final_root/{task}/{task}_Final_p{prob:.2f}_c{cnt}.tif` |
| 13 | Final SHP | 是（同 stem 矢量化为 `.shp` + 侧文件） |
| 14 | 已登记资产 | 是（`assets_registry.json`，`{task}_p{prob:.2f}_c{cnt}` 键） |
| 15 | 有无计划/校验/ToolResult | 只有确认门闩；无 plan / validate / verify / ToolResult |
| 16 | 硬编码盘符 | 有（默认值存于 `agent_command_bridge.init_ui_session_defaults`） |
| 17 | rerun 重复启动 | 不会（`pipeline_thread_started` 标志防重入） |
| 18 | 失败误报 | 无结构化 VERIFY：后处理返回 True 但 Final 文件无效（如全 NoData）仍会登记 |

---

## 2. 新增模块 `TF-agent/inference_agent_loop.py`

**原则**：输入/权重路径只来自侧栏合法值或注册资产，禁止 LLM 拼接路径；全部真实调用现有 `pre_engine` / `post_engine`；不在本模块重实现算法。

### 2.1 计划结构（§三）

```python
plan = {
  "schema": "local_tidal_flat_inference_plan_v1",
  "plan_id": "<uuid>",              # 参数变化 → 新 plan_id；旧计划失效
  "task_id": "24zhejiang1",
  "tool": "local_tidal_flat_inference",
  "input_asset_id": "...",          # 来自 dataset_assets 登记 或 "ui_selected"
  "input_path": "I:\\GEE_data\\20\\24zhejiang1",
  "input_type": "local_raster",
  "bands": [1, 2, 3],
  "model_id": "cdnet_resnet50",
  "weight_id": "best_train_loss_model_resnet50",
  "weight_path": "E:\\Code\\GEE\\best_train_loss_model_resnet50.pth",
  "device_policy": "auto",          # auto | cuda_required
  "device": "cuda",                 # validate 后写入实际设备
  "prob_threshold": 0.05,
  "count_threshold": 2,
  "postprocess": True,
  "output_dir": "E:\\Data\\843output\\24zhejiang1",
  "mask_dir": "E:\\Data\\843mask\\24zhejiang1",
  "expected_outputs": ["prediction_tif", "final_tif", "final_shp"],
  "ready": False,
  "blockers": [],
  "warnings": [],
  "steps": [...],
  "status": "waiting_confirmation",
  "created_at": "...",
}
```

### 2.2 计划构建

```python
def build_inference_plan(*, task_id, root_dir, final_root, mask_root,
                         model_path, prob_threshold, count_threshold,
                         input_asset_id=None, weight_id=None,
                         device_policy="auto") -> dict
```
- `input_path = root_dir/task_id`（task_id 必须存在于 root_dir 子目录列表，否则 blocker）；
- `weight_path`：只接受 `weight_id` 解析自**模型配置注册表** 或 侧栏 `model_path` 直接值；拒绝 URL、拒绝任意拼接路径；
- 参数范围：`prob ∈ [0.01, 0.50]`、`cnt ∈ [1, 10]`（与侧栏 slider 一致），越界 → blocker。

### 2.3 执行前验证（§四）

```python
def validate_inference_plan(plan) -> (ok: bool, blockers: List[str], device: str)
```
- 输入：路径存在、目录非空、含受支持 `.tif`、rasterio 可打开首个 tif、CRS 可读、尺寸合法、波段数 ≥ 3、输出目录可写；
- 权重：路径存在、非 URL、`os.path.isfile`、`torch.load(..., weights_only=True)` 可安全加载并 `strict=True` 匹配 CDNet state_dict（用 `load_state_dict` 试配，失败 → blocker）；仅记录校验，不保留权重驻留；
- 设备：`torch.cuda.is_available()`；`device_policy="auto"` → cuda/cpu 回退并记录真实设备；`cuda_required` 且无 CUDA → blocker；绝不自动声称在用 GPU；
- 参数：真实范围校验；
- 存在 blocker → 不进入执行；不启动线程；不登记；不伪报完成。

### 2.4 确认机制（§五）

复用 `agent_task_framework.require_confirm` 语义 + 统一时间线阶段：

```
PLAN → VALIDATE → CONFIRM → QUEUED → INFERENCE → POST_PROCESS → VERIFY → REGISTER → MAP → REPORT
```

- 时间线 `PHASES` 元组追加 `INFERENCE`、`POST_PROCESS`（`task_timeline.py`，向后兼容 M5/E1 的 `EXECUTE`）；
- 状态：`waiting_confirmation` 计划记录于 `session_state["_inference_pending_plan"]`；
- 同一 `plan_id` 只确认一次：确认后写入 `_inference_plan_confirmed={plan_id}`；
- 重复确认 / rerun：`pipeline_thread_started` + `plan_id` 幂等键，不重复启动线程；
- 取消：`_inference_pending_plan` 清除即失效，不可恢复；
- 与 M5/E1 待确认计划完全隔离（各自的 state 键）；
- 多个计划并存时，确认必须携带明确 `plan_id`；
- 参数修改 → 新 `plan_id` → 旧计划作废；
- UI「确认执行」按钮与 Copilot `confirm_inference(plan_id)` 走**同一** `confirm_inference_plan(state, plan_id)`。

### 2.5 真实执行（§六）

```python
def execute_local_inference(plan, *, stop_event=None, push_log=print,
                            push_progress=None,
                            pre_engine_mod=None, post_engine_mod=None) -> ToolResult
```
- `pre_engine_mod` / `post_engine_mod` 可注入（单元测试用 fake adapter）；默认导入真实模块；
- 流程 = 审计链路的直接复刻：
  1. `load_model(weight_path, device)`
  2. 逐 tif `process_geotiff(...)` → `mask_dir`
  3. `generate_double_constraint_complete(...)` → Final TIF + Final SHP
- **不得重写**模型构建/预处理/推理/后处理逻辑；
- 异常/stop → `ToolResult{success=False, error=...}`，不抛未捕获。

### 2.6 ToolResult（§六）

```python
{
  "success": bool, "task_id": str, "plan_id": str,
  "tool": "local_tidal_flat_inference", "status": "completed|failed",
  "inputs": {"input_asset_id","input_path","model_id","weight_id","device"},
  "parameters": {"prob_threshold","count_threshold"},
  "outputs": {"prediction_tif","final_tif","final_shp"},
  "metrics": {"elapsed_seconds","processed_tiles","tif_count"},
  "warnings": [], "error": None,
}
```
只填真实数据；虚构精度/面积/图斑数/GPU 利用率/耗时/瓦片数 → 一律禁止（metrics 中取不到的字段留 0/None）。

### 2.7 结果验证（§七）

```python
def verify_inference_outputs(plan, result, started_at) -> VerifyResult
```
- Final TIF：存在、非空、rasterio 可开、CRS 存在、transform 有效、w/h>0、非全 NoData、范围与输入合理关系、mtime ≥ 任务启动时间、属于当前 task_id；
- Final SHP：`.shp/.shx/.dbf` 存在、geopandas 可读、CRS 存在、几何合法、非空、bbox 与 Final TIF 基本一致；SHP 为**必要输出**（现有后处理生成它），缺失 → 验证失败，不登记、不回复完成；保留日志与失败阶段。
- 验证失败 → status=failed，不登记正式预测成果。

### 2.8 资产登记（§八）

```python
def register_inference_asset(plan, result, verification) -> asset_id | None
```
写入 `assets_registry.json`（键 `{task}_p{prob:.2f}_c{cnt}` 保持不变以兼容现有 find_asset），条目扩展派生字段：

```python
{
  "task", "prob_threshold", "min_count", "file_path",
  "method": "dl",
  "asset_id": "<uuid>",
  "plan_id": plan["plan_id"],
  "asset_type": "tidal_flat_prediction",
  "source_asset_id": plan.get("input_asset_id"),
  "input_path": rel_path(plan["input_path"]),
  "model_id", "weight_id",
  "code_commit": "<git rev-parse HEAD>",
  "device": plan["device"],
  "parameters": {"prob_threshold","count_threshold"},
  "final_tif": rel_path(result.outputs["final_tif"]),
  "final_shp": rel_path(result.outputs["final_shp"]),
  "created_at", "elapsed_seconds", "status": "verified",
}
```
规则：验证成功才登记；失败不登记；不覆盖已有成果（`asset_id` 唯一，新登记使用新键 `{task}_p{prob:.2f}_c{cnt}__{plan_id[:8]}`，避免覆盖）；同 `plan_id` 不重复登记；面向用户的回复不泄露无关绝对路径（用 basename/相对名）。

### 2.9 地图与后续任务联动（§九）

- 成功后：将 Final SHP 交给现有 `asset_override`/`_globe_rev` 加载路径 → 现有 Cesium Viewer；不重建 iframe、不改变 AOI、默认不重置相机；
- 地图失败 → 仅 warning，不否定推理成功；
- 刷新 capability registry：`deep_learning_inference` 结果字段更新；E1/M5/PDF 可用性按现有能力检查重算（`capability_registry.refresh` 风格，TTL 内可复用）。

### 2.10 时间线（§十）

阶段映射到统一时间线：
- PLAN「推理计划已生成」→ VALIDATE「输入与模型检查」→ CONFIRM「等待用户确认」→ QUEUED「已入队」→ INFERENCE「影像推理」→ POST_PROCESS「后处理」→ VERIFY「结果验证」→ REGISTER「成果登记」→ MAP「地图加载」→ REPORT「Copilot 回复」；
- 失败保留真实阶段（如「模型加载失败」「输入波段不完整」「后处理失败」「Final TIF 无效」），不笼统显示「任务失败」。

---

## 3. 集成点（小改动）

| 文件 | 改动 |
|---|---|
| `TF-agent/inference_agent_loop.py` | 新增（2.1~2.10 全部逻辑） |
| `TF-agent/task_timeline.py` | `PHASES` 追加 `INFERENCE`、`POST_PROCESS`（仅元组扩展，兼容） |
| `TF-agent/agent.py` | 新工具 `local_tidal_flat_inference(task_id, prob_th, cnt, run_now)` + `confirm_inference(plan_id)`；不暴露路径参数 |
| `TF-agent/agent_command_bridge.py` | `propose_inference_plan(state, action)` / `confirm_inference_plan(state, plan_id)` / `build_pending_task` 增加 `run_inference` 分支；`HEAVY_ACTION_LABELS["run_inference"]` |
| `TF-agent/app.py` | 侧栏确认按钮复用 `confirm_inference_plan`；时间线阶段接入；成功后能力刷新 + 地图加载（小改） |
| `TF-agent/agent_task_framework.py` | 不改（复用 require_confirm / VerifyResult / format_plan_markdown） |

---

## 4. 测试范围（§十一）

新增 `tests/unit/test_inference_agent_loop.py`，15~25 个高价值用例，覆盖用户 20 项清单：
1 合法输入生成计划 / 2 缺输入阻断 / 3 输入不可读阻断 / 4 波段不足阻断 / 5 缺权重阻断 / 6 非法权重路径阻断 / 7 CUDA 策略 / 8 未确认不执行 / 9 重复确认不重复执行 / 10 rerun 不重复执行 / 11 推理异常失败 / 12 后处理异常失败 / 13 Final TIF 缺失验证失败 / 14 Final TIF 无效验证失败 / 15 成功登记 / 16 失败不登记 / 17 地图失败仅 warning / 18 Copilot 只展示真实结果 / 19 能力状态刷新 / 20 M5/E1/PDF 不受影响。

- 编排逻辑：注入 fake `pre_engine_mod` / `post_engine_mod` adapter；
- 真实小样本：单独真实运行脚本（合法小影像 + 真实权重，GPU/CPU 自动），见 §5。

## 5. 真实验收（§十二）

- 成功：构造 ~1024×1024 或更小的合法 RGB GeoTIFF（reproject 至合法 CRS，如 UTM）→ 真实 `build_inference_plan` → confirm → 真实 `execute_local_inference`（真实权重 `best_train_loss_model_resnet50.pth`）→ `verify` → `register` → map → 回复；
- 失败：缺权重 / 波段不足 → VALIDATE 阻断、不启动线程、不登记、不伪报完成；
- 只跑小样本，不跑大区域/全量数据。

## 6. 验收命令

```
D:\anaconda3\envs\gwx\python.exe -m pytest tests/unit/test_inference_agent_loop.py -q --tb=short -p no:cacheprovider
D:\anaconda3\envs\gwx\python.exe -m pytest tests/unit -q --tb=short -p no:cacheprovider
```
（不再运行重复压力测试与完整浏览器回归。）

## 7. 提交

```
git add TF-agent/inference_agent_loop.py TF-agent/task_timeline.py TF-agent/agent.py \
        TF-agent/agent_command_bridge.py TF-agent/app.py \
        tests/unit/test_inference_agent_loop.py docs/dev/INFERENCE_AGENT_DESIGN.md \
        docs/dev/INFERENCE_AGENT_PROGRESS.md
git commit -m "feat(agent): add trusted local inference workflow"
```
不使用 `git add .`，不 push。
