# E1 Agent 闭环进展

更新时间：2026-07-19

## Git 基线（保留既有修改，不提交）

- 分支 `main` @ `93c44a59832f726386615f7bd6d10d074828dcdd`
- 已有未提交：M5 闭环 / 相机 / `m5_agent_loop` / `agent_task_framework` / acceptance 等
- 本轮只补齐 E1 闭环缺口；不提交 Git

## 阶段 0 — 审计结论

| 组件 | 角色 |
|------|------|
| **真正入口** | `TF-agent/e1_engine.run_e1_after_synthesis` → `research/jb/E1.py` · `E1_DataCleanerAndDiagnostic.run_pixel_comparison` |
| **evaluation_geo** | 仅 AOI 过滤/裁剪；**不是** E1 主指标引擎 |
| **combine.py** | TIF vs SHP 混淆矩阵（AutoTune）；**不在** E1 Agent 路径 |
| **生产 UI** | 侧栏 `ui_e1_*` + 流水线 `_run_e1_phase` |
| **脚手架（已有）** | `e1_agent_loop.py`、bridge `propose_e1/run_e1`、`run_e1_sync`、`test_e1_agent_loop.py` |
| **缺口（本轮已补）** | 线程分发、Agent 工具、finalize 真实回复/地图 |

### 真实支持的指标

- 成对：`jaccard_iou`、面积 km²、像元计数、`causal_analysis` / 分歧图
- 多产品：`disagreement_pixel_ratio`、agreement/disagreement TIF
- 产物：`E1_PIXEL_REPORT_{roi}.json` + `.md`

## 实施计划（≤10 行）

1. 先跑现有 `test_e1_agent_loop`（TDD 基线）。
2. 增补短测：工具 JSON 含 `propose_e1` / `run_e1`。
3. `maybe_start_pipeline_thread`：`mode==e1` → `_e1_worker_entry`。
4. `finalize_background_pipeline`：E1 摘要写对话 + 加载热力图。
5. `agent.py`：`prepare_e1_consistency_check` / `confirm_and_run_e1`。
6. 只跑 `test_e1_agent_loop` + `test_m5_agent_loop`。
7. 不改 GEE / 推理 / `m5_engine` 核心 / PDF / 布局 CSS；不启 Streamlit。

## 阶段状态

- [x] 0 审计
- [x] 1 单元测试（TDD）：先红（缺工具）再绿
- [x] 2 最小接线：`mode==e1` 线程 + finalize 摘要/地图 + Agent 工具/提示
- [x] 3 短测通过：`test_e1_agent_loop` + `test_m5_agent_loop` 全部 OK（未提交 Git）

## 闭环路径（已接通）

```text
prepare_e1_consistency_check / propose_e1
  → 预检（当期 SHP + data_root + reference）
  → 用户确认
  → run_e1 → maybe_start → run_e1_sync → e1_engine
  → verify → register_e1_asset → 地图热力 → Copilot 真实摘要
```
