# INFERENCE_AGENT_PROGRESS.md — 本地潮滩推理可信执行闭环 · 完成报告

> 里程碑：`feature/map-capability-aoi-milestone`，本轮只开发「本地潮滩推理的可信执行闭环」。
> 目标：**不重新实现潮滩算法**，把已有推理/后处理代码接入 `plan → validate → confirm → execute → verify → register → map → timeline → grounded reply` 体系。

---

## 1. 原推理调用链（审计结论）

```
侧栏「运行模型推理」按钮 / Copilot dispatch(confirmed=true)
  → st.session_state.pending_task = {task, prob, cnt, mode, force_rerun}
  → rerun → maybe_start_pipeline_thread()（pipeline_thread_started 防重入）
  → threading.Thread(_pipeline_worker_entry) → run_pipeline_sync(ctx, shared, stop_event)
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

关键事实：推理入口 `pre_engine.process_geotiff`；后处理入口 `post_engine.generate_double_constraint_complete`；
权重默认 `best_train_loss_model_resnet50.pth`（CDNet resnet50, img_size=1024, n_class=1, img_chan=3）；
设备 `cuda if torch.cuda.is_available() else cpu`；掩膜 `mask_root/{task}/{fname}_mask.tif`。

---

## 2. 新可信执行链

```
用户选择/指定本地遥感影像（root_dir/{task}）
  → Copilot 生成推理计划（build_inference_plan：输入/波段/权重/设备/输出目录/阈值）
  → validate_inference_plan（逐项检查：输入存在、掩膜目录、输出目录、权重存在且非 URL、设备可用）
  → 用户确认（confirm_inference_plan，单计划单确认，计划号幂等）
  → execute_local_inference
      ├─ pre_engine.load_model(model_path, device)          # 真实权重
      ├─ 逐景 pre_engine.process_geotiff（跳过已存在掩膜）    # 真实推理 → *_mask.tif
      └─ post_engine.generate_double_constraint_complete(keep_final_tif=True)
                                                           # 真实后处理 → Final TIF + Final SHP
  → verify_inference_outputs
      ├─ Final TIF：路径/非空/CRS/transform/有数据/与输入重叠 IoU>0.5/mtime 新于开始/任务名在路径
      └─ Final SHP：.shp/.shx/.dbf/可读/CRS/几何/bbox 与 Final TIF IoU>0.5
  → register_inference_asset（仅校验成功才登记；key = {task}_p{prob:.2f}_c{cnt}__{plan_id[:8]}）
  → 加载地图 / 刷新动态能力 / 更新时间线（INFERENCE_TIMELINE_PHASES 12 阶段）
  → Copilot 返回真实结果（summarize_inference_result_for_chat，带「已验证」标记）
```

状态机：`STATE_INFERENCE_PENDING_PLAN → STATE_INFERENCE_PLAN_CONFIRMED`；工具 `local_tidal_flat_inference` / `confirm_inference`。

---

## 3. 修改文件表

| 文件 | 改动 |
|---|---|
| `TF-agent/inference_agent_loop.py` | **新增**。计划生成/校验/确认/取消、真实执行、输出校验、资产登记、聊天摘要、Agent 上下文。35 个常量/函数 |
| `TF-agent/task_timeline.py` | PHASES 扩展为 12 阶段：`PLAN,VALIDATE,CONFIRM,QUEUED,INFERENCE,POST_PROCESS,EXECUTE,VERIFY,REGISTER,MAP,REPORT` |
| `TF-agent/post_engine.py` | `generate_double_constraint_complete` 增加 `keep_final_tif` 参数：True 时 `os.replace(work→Final)` 保留 Final TIF（默认 False 保持历史行为） |
| `TF-agent/agent.py` | 注册工具 `local_tidal_flat_inference` + `confirm_inference`；pending_action 类型 `propose_inference` / `confirm_inference` |
| `TF-agent/agent_command_bridge.py` | `propose_inference_plan` / `confirm_inference` 包装；`build_pending_task` run_inference 分支；HEAVY_ACTION_LABELS 增 `run_inference` |
| `TF-agent/app.py` | `_inference_worker_entry`；`maybe_start_pipeline_thread` inference 分支；后台管道收尾 inference 处理（重复事件守卫 `_inference_handled`）；侧栏「本地潮滩推理计划」确认/取消区块；计划刷新展示 |
| `tests/unit/test_inference_agent_loop.py` | **新增** 35 个单元测试（FakePreEngine/FakePostEngine，8 个测试类） |
| `docs/dev/INFERENCE_AGENT_DESIGN.md` | 设计文档（审计 18 项 + 流程） |

---

## 4. 真实成功场景（端到端验收）

**输入**：`I:\GEE_data\20\20fujian1\` 福建 2020 两期 Sentinel-2 场景，按 NDWI 水陆边界裁剪同一地理窗口（2048×2048，EPSG:32650）：
- `fujian1_20200617T023549_20200617T024727_T50RQQ.tif`（原 384MB，10535×14049）→ `scene_001.tif`
- `fujian1_20200826T023549_20200826T024916_T50RQQ.tif` → `scene_002.tif`

**模型**：`cdnet_resnet50`（CDNet, backbone=resnet50, output_stride=16, img_size=1024, n_class=1, img_chan=3, chan_num=64, fuzzy_num=16）
**权重**：`e:\Code\GEE\best_train_loss_model_resnet50.pth`（117,327,431 字节，真实权重，torch.load weights_only=True）
**设备**：`cuda`（真实 GPU 推理，非 CPU 模拟）

**真实调用链**：plan → validate → confirm → execute（pre_engine 逐景真实推理 2 景 → post_engine 双重约束合成）→ verify（12 项全部 PASS）→ register → summarize

**输出**：
- 掩膜：`output/inference_acceptance_masks/acceptance_tidal/scene_001_mask.tif`（117,218 B）、`scene_002_mask.tif`（111,480 B）——真实 CDNet 预测，0/255 二值，scene_001 含 2,255,545 个潮滩像元（53%）
- Final TIF：`post_out/inference_acceptance/acceptance_tidal/acceptance_tidal_Final_p0.05_c2.tif`（111,484 B，3468 个有效像元，EPSG:32650）
- Final SHP：`acceptance_tidal_Final_p0.05_c2.shp`（190,360 B，1 个要素，bbox 与 TIF IoU=0.826）
- 资产 ID：**`087331f14966413f8eabc30f604c6dbc`**（已写入 assets_registry.json）

**耗时**：~5.76 秒（处理 4/4 景；2 景推理 + 合成，CUDA）
**验证摘要**（12 项全 PASS）：`final_tif_has_data=3468`、`final_tif_overlaps_input IoU=1.000`、`final_shp_bbox_matches_tif IoU=0.826`、`final_shp_readable=1 features` 等。

---

## 5. 阻断场景（真实运行中修复的坑）

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | `OMP: Error #15` 崩溃 | libiomp5md.dll 重复加载 | 入口 `os.environ.setdefault("KMP_DUPLICATE_LIB_OK","TRUE")`（与 app.py/agent.py 一致） |
| 2 | DataLoader worker 崩溃 | Windows num_workers=4 子进程重导入 `__main__` | 顶层代码移入 `if __name__ == "__main__":` 守卫 |
| 3 | 控制台 GBK 无法打印 emoji（❌✅⚠️） | GBK 控制台编码 | 运行前 `$env:PYTHONIOENCODING="utf-8"` |
| 4 | `verify_inference_outputs` AttributeError | 旧代码 `str(...).get(...)` 误用 | 改为 isinstance 守卫的字典读取 |
| 5 | 摘要头总写「已验证」 | 无条件硬编码 | 按校验结果输出 `（已验证）` / `（校验未完全通过）` |
| 6 | 后处理报「未检测到潮滩像元」 | **单景输入永远无法满足 `count >= 2` 双重约束**（E 最大为 1） | 改用**同窗口两期影像**（20200617+20200826），E>=2 有解 |
| 7 | 后处理缓存复用导致脏数据 | `_NUMERATOR/_DENOMINATOR.tif` 存在则跳过累加 | 真实运行前清理 `post_out/`、`output/inference_acceptance_masks/`、`rasters/` 对应目录 |
| 8 | pytest exit code 1 误判 | `PYTORCH_CUDA_ALLOC_CONF` 弃用警告 | 只看 "N passed" 行 |

---

## 6. 测试结果

**命令 1**（推理闭环 35 项）：
```
D:\anaconda3\envs\gwx\python.exe -m pytest tests/unit/test_inference_agent_loop.py -q --tb=short -p no:cacheprovider
→ 35 passed
```
**命令 2**（全部单元测试 266 项）：
```
D:\anaconda3\envs\gwx\python.exe -m pytest tests/unit -q --tb=short -p no:cacheprovider
→ 266 passed
```
（exit code 1 仅为 PYTORCH_CUDA_ALLOC_CONF 弃用警告，与用例无关。）

测试类：`TestPlanBuild`（计划生成/幂等）、`TestValidate`（10 项输入检查）、`TestConfirmGate`（单计划单确认/取消/防重复）、
`TestExecuteFailure`（load/process/stop/后处理失败路径）、`TestVerifyAndRegister`（12 项校验 + 登记 + 重复防重）、
`TestSummaryAndContext`（摘要真实化 + Agent 上下文）、`TestExistingFlowsUntouched`（原管线不受影响）、`TestBridgeInferenceFlow`（bridge 集成）。

---

## 7. 未完成事项

- 侧栏 UI 中的推理计划展示/确认按钮已接入，但**未做端到端 UI 手工验收**（本轮以脚本驱动验证真实链路）。
- 单景输入无法通过 `count >= 2` 双重约束（算法语义如此）；如需单景成果，需另加"单景直出"模式（不在本轮范围）。
- 掩膜/合成产物未纳入正式 assets_registry（本轮 assets 仅登记 Final SHP；Final TIF 通过 keep_final_tif 保留在 post_out）。
- `_e2e_sandbox/` 脚本（crop/make/run）为沙盒验收工具，**未提交**。

## 8. 下一步建议

1. **UI 手工验收**：在侧栏选择 2 期真实影像 → 触发推理 → 观察计划确认/进度时间线/地图加载/最终摘要。
2. **GEE 数据下载可信执行闭环**：本闭环已具备"计划→校验→确认→执行→校验→登记→加载→时间线→如实汇报"骨架，可复用：
   `download_plan → validate（凭据/范围/波段/配额）→ confirm → execute（真实下载）→ verify（文件/CRS/覆盖率）→ register → map → timeline → grounded reply`。
3. **单景模式**：为单张影像增加 `min_absolute_count=1` 直出路径（如用户确认为单景分析）。
4. **产物登记扩展**：把 Final TIF 一并登记到 assets_registry，便于地图加载直接引用 TIF 而非仅 SHP。
