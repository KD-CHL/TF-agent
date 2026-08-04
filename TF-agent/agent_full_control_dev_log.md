# 🚀 Agent 全参数对话控制自愈升级日志

## 一、重构完成情况

| 项目 | 状态 |
|------|------|
| 对话参数覆盖率 | **100%** — 涵盖 M1–M5、E1、M4、AutoTune、路径、工作台 Tab、地图跳转 |
| 双轨网桥 JSON 协议 | ✅ `agent_command_bridge.py` |
| Agent 工具升级 | ✅ `dispatch_system_command` + 兼容工具 JSON 化 |
| Streamlit 侧栏 `ui_*` 绑定 | ✅ 推理/M5/E1/M4/路径/任务 Tab |
| 差量合流（null/缺省不覆盖） | ✅ |
| Legacy `COMMAND_*` 兼容 | ✅ |
| 单元测试 | ✅ **17/17 通过** (`test_agent_command_flow.py`) |
| 是否引入原有手动 Bug | **否** — 侧栏仍走原按钮/schema；Agent 仅写 `ui_*` + `pending_*` |

---

## 二、协议规范 (JSON Payload Spec)

Agent 工具返回块（正文末尾原样附带）：

```
[SYSTEM_COMMAND_JSON]
{ ... }
[/SYSTEM_COMMAND_JSON]
```

### 顶层结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `map` | object \| null | `lat`, `lon`, `zoom` → `st.session_state.map_center` / `map_zoom` |
| `sidebar_states` | object \| null | 差量更新侧栏；**缺省或 null 不覆盖** |
| `pending_action` | object \| null | 入队后台任务（与侧栏按钮 schema 一致） |

> **P0 加固（2025-02 基线固化）**：`run_pipeline` / `run_m4` / `run_autotune`
> 属重型工具，需 `pending_action.confirmed=true` 显式确认；未确认仅记录
> `_pending_heavy_confirm` 供 UI 弹确认，不写 `pending_task` / `pending_autotune`。

### `sidebar_states` 字段 → `st.session_state`

| JSON 键 | Session 键 | 说明 |
|---------|------------|------|
| `workflow_tab` | `ui_workflow` | `潮滩推理` / `GEE 数据下载`（支持别名；旧字段 `workspace_tab` 仍兼容） |
| `run_mode` | `ui_run_mode` + `ui_inference_mode` | `dl` / `index` |
| `selected_task` | `ui_selected_task` | 目标任务名 |
| `prob_th` / `min_cnt` | `ui_prob_th` / `ui_min_cnt` | 0.01–0.50 / 1–10 |
| `m5_enabled` | `ui_m5_enabled` | bool |
| `e1_enabled` / `e1_reference` | `ui_e1_enabled` / `ui_e1_reference` | E1 开关与参考产品 |
| `root_dir` 等路径 | `ui_root_dir`, `ui_mask_root`, … | 路径类字符串（仅文件系统路径 normpath，URL/代理地址不处理） |
| `m4_cloud` 等 | `ui_m4_cloud_limit`, … | M4 下载参数 |

### `pending_action.type`

| type | 写入 | 说明 |
|------|------|------|
| `run_pipeline` | `pending_task`（需 `confirmed=true`） | DL / 指数法推理 |
| `run_m4` | `pending_task` (mode=m4)（需 `confirmed=true`） | GEE 下载 |
| `run_autotune` | `pending_autotune`（需 `confirmed=true`） | 自适应调参 |

### 口语 → JSON 映射（System Prompt 已注入）

- **例 A**：「深度学习跑 24zhejiang，概率 5% 频次 2」→ `run_mode=dl`, `prob_th=0.05`, `min_cnt=2`, `pending_action=run_pipeline(confirmed=true)`
- **例 B**：「打开 E1，参考师姐 2020」→ `e1_enabled=true`, `e1_reference=师姐_2020`
- **例 C**：「开 M5，指数法跑一下」→ `m5_enabled=true`, `run_mode=index`, `pending_action=run_pipeline(confirmed=true)`
- **例 D**：「切下载页，云量 20，启动 M4」→ `workflow_tab=GEE数据下载`, `m4_cloud=20`, `pending_action=run_m4(confirmed=true)`

---

## 三、测试通过实录

```
python test_agent_command_flow.py
Ran 17 tests in 0.002s — OK
```

| 测试类 | 验证点 |
|--------|--------|
| `TestParseSystemCommand` | JSON 块 / Legacy 管道 / 无关文本 |
| `TestDeltaMerge` | **未提及参数保持原值**、null 跳过、地图+侧栏并发、Tab 别名 |
| `TestPendingActions` | pipeline / M4 / AutoTune schema 与侧栏一致 |
| `TestCoercion` | 概率 clamp、中文 bool |
| `TestScenarioExamples` | 口语场景 A–D 端到端 dict 模拟 |

---

## 四、改动文件清单

| 文件 | 职责 |
|------|------|
| `agent_command_bridge.py` | 解析、差量合流、`pending_task` 构建 |
| `agent.py` | `dispatch_system_command`、JSON 工具、System Prompt |
| `app.py` | `init_ui_session_defaults`、`ui_*` 侧栏键、`process_agent_reply` |
| `test_agent_command_flow.py` | 无 Streamlit 回归测试 |

---

## 五、使用说明

1. 重启 Streamlit 后，Copilot 可通过自然语言调整侧栏并触发流程。
2. **手动侧栏操作优先级不变**：Agent 只更新 JSON 中显式给出的字段。
3. 若模型仍输出旧版 `COMMAND_UPDATE_MAP|…`，bridge 自动兼容。

---

*生成时间：2026-06-25*
