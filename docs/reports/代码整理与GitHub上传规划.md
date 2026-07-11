# 潮滩系统代码整理与 GitHub 上传规划

**整理日期**：2026 年 7 月  
**当前工作区**：`e:\Code\GEE`  
**目标**：分清有用/无用代码，重命名为可读性更强的结构，准备上传 GitHub

---

## 一、先说结论：你现在有什么

当前 `e:\Code\GEE` **不是一个干净的项目根目录**，而是一个「开发工作台」，里面混着：

| 类型 | 代表 | 是否上传 GitHub |
|------|------|----------------|
| **核心产品** | `YYnet-main/` | ✅ 必须上传（主体） |
| **研究原型** | `jb/` | ⚠️ 可选上传（建议精简后作为 `research/`） |
| **测试基础设施** | 根目录 4 个测试脚本 + `cstf_ux.py` | ✅ 建议上传（移到 `tests/`） |
| **第三方整包** | `Llama-Factory/` | ❌ 不上传（478 个文件，用文档说明依赖即可） |
| **大模型权重/配置** | `Qwen2.5-7B-Instruct/`、`qwen_rs_agent_lora/`、`Qwen_Agent_Merged/` | ❌ 不上传（放 HuggingFace / 本地） |
| **测试产出物** | `_e2e_sandbox/` 输出 | ❌ 不上传（可本地重新生成） |
| **IDE 配置** | `.idea/` | ❌ 不上传 |
| **密钥/日志** | `.env`、`jb/data.py` 内 API Key、`agent_debug.log` | ❌ 绝不上传 |
| **工作汇报文档** | `导师汇报_*.md`、诊断日志 | ⚠️ 可放 `docs/`，不必放产品根目录 |

**重要发现**：
- 磁盘上**没有** `YYnet/` 文件夹，只有 `YYnet-main/`；但 README、部分脚本仍写 `YYnet/`，需要统一。
- `YYnet-main/` 内已有独立 `.git`，远程为 `GGboywx/TF_Agent`；与外层 `GEE` 目录关系混乱，上传前需决定「只保留一个 Git 根」。

---

## 二、有用 vs 无用：逐项分类

### 2.1 核心产品 `YYnet-main/`（23 个 .py）— **全部有用**

这是 Streamlit 主应用 + 各引擎，**改革重点在这里**。

#### A. 应用层（必须保留）

| 现文件名 | 作用 | 建议新名 | 优先级 |
|----------|------|----------|--------|
| `app.py` | Streamlit 主界面（地图、推理、聊天） | `streamlit_app.py` 或保留 `app.py` | 高 |
| `sidebar_ui.py` | 侧栏 UI 组件 | 保留 | — |
| `agent.py` | LLM 智能体 | 保留或 `llm_agent.py` | 中 |
| `agent_command_bridge.py` | 智能体 ↔ UI 指令桥接 | 保留（名字已经清晰） | — |
| `cstf_gateway.py` | 单端口网关（Streamlit + 三维地球） | `gateway_server.py` | 中 |

#### B. 推理与后处理（必须保留）

| 现文件名 | 作用 | 建议新名 | 说明 |
|----------|------|----------|------|
| `pre_engine.py` | 深度学习单景推理（CDNet） | `inference_engine.py` | 与 post 成对 |
| `post_engine.py` | 时空掩膜融合 → Final 成果 | `fusion_engine.py` | 核心后处理 |
| `index_engine.py` | 指数法提取（mNDWI + ACWI） | `index_extraction_engine.py` | 与 DL 推理并列 |
| `auto_tune.py` | 概率/计数阈值网格搜索 | `threshold_tuning.py` | 名字更直观 |

#### C. 分析引擎（必须保留）

| 现文件名 | 作用 | 建议新名 | 说明 |
|----------|------|----------|------|
| `e1_engine.py` | 多源一致性诊断（封装 `jb/E1.py`） | `consistency_engine.py` | E1 = 多源 IoU |
| `m5_engine.py` | 时空异常告警（封装 `jb/M5.py`） | `change_alert_engine.py` | M5 = 变化检测 |
| `m4_engine.py` | GEE Sentinel-2 下载导出 | `gee_download_engine.py` | 已替代旧版 |
| `evaluation_geo.py` | 地理精度评价（AOI 裁剪真值） | `geo_evaluation.py` | 保留也可 |
| `dataset_assets.py` | 数据集资产登记 CLI | `dataset_registry.py` | 与 assets_registry 区分 |

#### D. 可视化（必须保留）

| 现文件名 | 作用 | 建议新名 |
|----------|------|----------|
| `globe_engine.py` | Cesium 三维地球 HTML 生成 | `cesium_globe.py` |
| `globe_server.py` | 地球 HTTP 服务 | 保留 |

#### E. 深度学习模型（必须保留）

| 现文件名 | 作用 | 建议新名 | 说明 |
|----------|------|----------|------|
| `YYnet.py` | CDNet 主模型定义 | `cdnet_model.py` | **避免与项目同名** |
| `backbone.py` | 网络主干 | `model_backbone.py` | 与 modules 放 `models/` |
| `modules.py` | 网络模块 | `model_modules.py` | 同上 |

#### F. 可选 / 辅助（有用但非主链路）

| 现文件名 | 作用 | 建议 | 说明 |
|----------|------|------|------|
| `train_agent.py` | 智能体 LoRA 训练 | 移到 `scripts/train_agent.py` | 开发用，非运行时必需 |
| `api_server.py` | 本地 OpenAI 兼容 API | 移到 `scripts/local_llm_api.py` | 可选部署 |
| `gee_engine.py` | 早期 GEE 导出脚本 | **可删除或归档** | 已被 `m4_engine.py` 取代 |
| `test_agent_command_flow.py` | 智能体指令单元测试 | 移到 `tests/test_agent_commands.py` | 测试文件不应放根目录 |

#### G. 配置与注册表（必须保留，注意内容）

| 文件 | 建议 |
|------|------|
| `requirements.txt` | 保留 |
| `.streamlit/config.toml` | 保留 |
| `assets_registry.json` | 保留样例，大文件可 `.gitignore` 后提供 `assets_registry.example.json` |
| `dataset_assets_registry.json` | 同上 |
| `.env` | **不上传**；提供 `.env.example` |
| `README.md` | 保留并更新目录结构 |
| `REMOTE_DEMO.md` | 移到 `docs/remote_demo.md` |
| `scripts/*.ps1` | 保留在 `scripts/` |

#### H. 应删除或排除的产物（无用）

| 路径 | 原因 |
|------|------|
| `agent_debug.log` | 运行日志 |
| `_analysis_vec_tmp/` | 临时分析目录 |
| `DATA/sqq_TF_20-25/` 仅 .prj/.cpg | 无实际 shp，残缺数据 |
| `system_*_diagnostics_log.md`（副本） | 与根目录重复，放 `docs/` 即可 |

---

### 2.2 研究脚本 `jb/`（14 个 .py）— **部分有用、部分可归档**

`jb/` 是早期 M1–M5、E1 原型；其中 **M4/M5/E1 已被 `YYnet-main` 的 engine 封装**。若上传 GitHub，建议作为 `research/` 子目录，并标明「CLI 原型，产品侧请用 engine」。

#### 有用（建议保留并改名）

| 现文件名 | 作用 | 建议新名 | 与产品的关系 |
|----------|------|----------|--------------|
| `E1.py` | 多源像元级一致性诊断 | `multi_source_consistency.py` | `e1_engine.py` 的源码 |
| `M5.py` | 时空异常三维度告警 | `spatial_change_alert.py` | `m5_engine.py` 的源码 |
| `M4.py` | GEE 数据下载 | `gee_sentinel_export.py` | `m4_engine.py` 的源码 |
| `M1_1.1.py` | 海面提取（ACWI） | `sea_surface_extraction.py` | `index_engine` M1 链路 |
| `M1_1.2.py` | 低潮 ACWI 频率 | `acwi_low_tide_extraction.py` | `index_engine` M2 链路 |
| `M1.1.py` | M1∩M2 空间融合 | `tidal_flat_fusion.py` | 对应「M3」逻辑 |
| `combine.py` | TIF vs SHP 精度评价 | `accuracy_evaluation.py` | 与 `evaluation_geo` 相关 |
| `check.py` | SHP 栅格化裁剪 | `rasterize_clip.py` | 工具脚本 |
| `point.py` | 点状验证 | `point_validation.py` | 验证工具 |
| `1.py` | CSTFSeg 栅格转矢量 | `raster_to_vector.py` | **现名完全不可读** |
| `combin_Ratio.py` | 比率合成 | `ratio_combination.py` | 拼写也建议修正 |
| `combin_MAX.py` | GDAL 最大值镶嵌 | `max_mosaic.py` | 工具脚本 |

#### 低价值 / 建议不上传或归档

| 现文件名 | 原因 | 建议 |
|----------|------|------|
| `data.py` | LLM 数据蒸馏脚本，**内含硬编码 API Key** | 删除密钥后改名 `llm_distillation.py`，或移出仓库 |
| `add_view.py` | 硬编码 `G:\` 路径的可视化草稿 | 归档到 `research/archive/` 或删除 |
| `jb/Qwen2.5-7B-Instruct/` | 与根目录重复的大模型元数据 | **删除**（重复） |
| `jb/e1_workspace/outputs_e1/*` | 测试报告产出 | 不上传，放 `.gitignore` |
| `jb/point/`、`边缘验证点/`、`water-line/` | 本地验证数据 | 不上传（或放 `data/samples/` 小样例） |

---

### 2.3 根目录散落文件

#### 有用（建议迁入仓库的 `tests/` + `src/`）

| 文件 | 作用 | 建议位置 |
|------|------|----------|
| `cstf_ux.py` | 共享地学防御工具库 | `src/cstf/utils/geo_ux.py` |
| `generate_comprehensive_test_data.py` | E2E 沙盒数据生成 | `tests/fixtures/generate_sandbox_data.py` |
| `run_e2e_diagnostics.py` | 13 项 E2E 测试 | `tests/e2e/run_diagnostics.py` |
| `run_ux_stress_tests.py` | 5 项 UX 压力测试 | `tests/stress/run_ux_stress.py` |

#### 有用但非产品核心（可选）

| 文件 | 作用 | 建议 |
|------|------|------|
| `scan_tidal_data.py` | 扫描 `E:\潮滩数据集` | 移到 `scripts/`，路径改环境变量 |
| `analyze_tidal_datasets.py` | 数据集统计分析 | 同上 |
| `analyze_shp_minimal.py` | 无 geopandas 的 SHP 统计 | 同上 |

#### 文档（有用，放 docs）

| 文件 | 建议位置 |
|------|----------|
| `导师汇报_潮滩系统测试与质量保障工作汇报.md` | `docs/reports/advisor_qa_report.md` |
| `system_full_diagnostics_log.md` | `docs/reports/e2e_diagnostics_log.md` |
| `system_meticulous_ux_diagnostics_log.md` | `docs/reports/ux_diagnostics_log.md` |

---

### 2.4 整个目录建议不上传 GitHub

| 目录/文件 | 文件数 | 处理方式 |
|-----------|--------|----------|
| `Llama-Factory/` | ~478 | 删除或移出工作区；README 写安装说明 |
| `Qwen2.5-7B-Instruct/` | 11 | 本地/HuggingFace 管理 |
| `qwen_rs_agent_lora/` | 36 | 同上（LoRA 权重） |
| `Qwen_Agent_Merged/` | 5 | 同上 |
| `_e2e_sandbox/` | 56 | `.gitignore`；保留生成脚本即可 |
| `.idea/` | 7 | `.gitignore` |
| `ngrok/` | 1 | 可保留 `ngrok.example.yml`，真实 token 不入库 |
| `gee/jb/` | 1 | 与 `scan_tidal_data` 重复，可合并 |

---

## 三、推荐的新项目结构（上传 GitHub 用）

建议：**以产品为中心建一个仓库**，不要直接把整个 `GEE` 文件夹推上去。

### 方案 A（推荐）：单仓库，清晰分层

```text
tidal-flat-rs/                    # 新仓库名（示例，可自定）
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
├── requirements.txt
├── pyproject.toml                # 可选，便于 pip install -e .
│
├── src/
│   └── cstf/                     # 包名：Coastal / CSTF
│       ├── app/
│       │   ├── streamlit_app.py      # 原 app.py
│       │   ├── sidebar_ui.py
│       │   └── gateway_server.py     # 原 cstf_gateway.py
│       ├── agents/
│       │   ├── llm_agent.py          # 原 agent.py
│       │   └── command_bridge.py     # 原 agent_command_bridge.py
│       ├── engines/
│       │   ├── inference_engine.py   # 原 pre_engine.py
│       │   ├── fusion_engine.py      # 原 post_engine.py
│       │   ├── index_extraction_engine.py
│       │   ├── gee_download_engine.py    # 原 m4_engine.py
│       │   ├── change_alert_engine.py    # 原 m5_engine.py
│       │   ├── consistency_engine.py     # 原 e1_engine.py
│       │   ├── threshold_tuning.py       # 原 auto_tune.py
│       │   └── geo_evaluation.py
│       ├── models/
│       │   ├── cdnet_model.py        # 原 YYnet.py
│       │   ├── model_backbone.py
│       │   └── model_modules.py
│       ├── visualization/
│       │   ├── cesium_globe.py
│       │   └── globe_server.py
│       ├── data/
│       │   ├── dataset_registry.py
│       │   └── assets_registry.py    # 或 example json
│       └── utils/
│           └── geo_ux.py             # 原 cstf_ux.py
│
├── research/                     # 原 jb/（精简后）
│   ├── multi_source_consistency.py
│   ├── spatial_change_alert.py
│   ├── gee_sentinel_export.py
│   ├── sea_surface_extraction.py
│   ├── acwi_low_tide_extraction.py
│   ├── tidal_flat_fusion.py
│   ├── accuracy_evaluation.py
│   └── README.md                 # 说明与 engines 的对应关系
│
├── tests/
│   ├── e2e/
│   │   └── run_diagnostics.py
│   ├── stress/
│   │   └── run_ux_stress.py
│   ├── unit/
│   │   └── test_agent_commands.py
│   └── fixtures/
│       └── generate_sandbox_data.py
│
├── scripts/
│   ├── start_gateway.ps1
│   ├── train_agent.py
│   └── local_llm_api.py
│
├── config/
│   ├── streamlit/config.toml
│   └── ngrok.example.yml
│
└── docs/
    ├── remote_demo.md
    └── reports/
        └── advisor_qa_report.md
```

### 方案 B（改动最小）：只改名 + 挪目录

若暂时不想拆 `src/` 包结构，至少做：

1. `YYnet-main/` → **`cstf-app/`** 或 **`YYnet/`**（与 README 一致）
2. 根目录测试脚本 → **`tests/`**
3. `jb/` → **`research/jb/`**
4. 删掉/移出 `Llama-Factory`、`Qwen*` 等大文件夹
5. 补根目录 **`.gitignore`**

---

## 四、完整重命名对照表（YYnet-main 改革用）

复制此表给 IDE 批量重构时参考。**改名后必须同步改 import**。

| 序号 | 现路径 | 建议新路径 | 改名理由 |
|------|--------|------------|----------|
| 1 | `YYnet-main/` | `cstf-app/` 或 `YYnet/` | 去掉 `-main` 后缀，与文档一致 |
| 2 | `YYnet.py` | `models/cdnet_model.py` | 避免「项目名=模型名」混淆 |
| 3 | `backbone.py` | `models/model_backbone.py` | 归入模型目录 |
| 4 | `modules.py` | `models/model_modules.py` | 同上 |
| 5 | `pre_engine.py` | `engines/inference_engine.py` | 语义：推理 |
| 6 | `post_engine.py` | `engines/fusion_engine.py` | 语义：时空融合 |
| 7 | `m4_engine.py` | `engines/gee_download_engine.py` | 去掉内部编号 |
| 8 | `m5_engine.py` | `engines/change_alert_engine.py` | 见名知意 |
| 9 | `e1_engine.py` | `engines/consistency_engine.py` | 见名知意 |
| 10 | `index_engine.py` | `engines/index_extraction_engine.py` | 见名知意 |
| 11 | `auto_tune.py` | `engines/threshold_tuning.py` | 见名知意 |
| 12 | `evaluation_geo.py` | `engines/geo_evaluation.py` | 统一 engines |
| 13 | `dataset_assets.py` | `data/dataset_registry.py` | registry 更清晰 |
| 14 | `globe_engine.py` | `visualization/cesium_globe.py` | 技术栈入名 |
| 15 | `globe_server.py` | `visualization/globe_server.py` | 归类 |
| 16 | `cstf_gateway.py` | `app/gateway_server.py` | 归类 |
| 17 | `app.py` | `app/streamlit_app.py` | 可选 |
| 18 | `agent.py` | `agents/llm_agent.py` | 归类 |
| 19 | `agent_command_bridge.py` | `agents/command_bridge.py` | 缩短 |
| 20 | `gee_engine.py` | — | **删除**（`m4_engine` 已覆盖） |
| 21 | `jb/1.py` | `research/raster_to_vector.py` | 消除无意义文件名 |
| 22 | `jb/data.py` | — | **删除或脱敏**（含 API Key） |
| 23 | `cstf_ux.py` | `src/cstf/utils/geo_ux.py` | 纳入包结构 |

---

## 五、模块编号（M1–M5、E1）是否保留？

你在论文/汇报里用了 M1–M5、E1 编号，建议：

| 策略 | 做法 |
|------|------|
| **代码文件名** | 用英文语义名（`change_alert_engine.py`），可读性更强 |
| **文档与论文** | 继续用 M5、E1 编号，在 README 加一张映射表 |
| **UI 与日志** | 可保留「M5 异常检测」「E1 一致性诊断」中文展示 |

**映射表（建议写进 README）**：

| 编号 | 含义 | 研究脚本 | 产品引擎 |
|------|------|----------|----------|
| M1 | 海面提取 | `sea_surface_extraction.py` | `index_extraction_engine` |
| M2 | ACWI 低潮频率 | `acwi_low_tide_extraction.py` | `index_extraction_engine` |
| M3 | 空间融合 | `tidal_flat_fusion.py` | `index_extraction_engine` |
| M4 | GEE 影像下载 | `gee_sentinel_export.py` | `gee_download_engine` |
| M5 | 时空异常告警 | `spatial_change_alert.py` | `change_alert_engine` |
| E1 | 多源一致性 | `multi_source_consistency.py` | `consistency_engine` |

---

## 六、GitHub 上传前检查清单

### 6.1 安全（必做）

- [ ] 删除或替换 `jb/data.py` 中的 **Moonshot API Key**
- [ ] 确保 `.env` 在 `.gitignore` 中，提供 `.env.example`
- [ ] 检查 `agent.py`、`train_agent.py` 无硬编码密钥
- [ ] 用 `git log` / 全局搜索 `sk-` 确认无密钥历史

### 6.2 仓库结构（必做）

- [ ] 决定唯一 Git 根：建议**取消** `YYnet-main/.git` 嵌套，在整理后的项目根初始化一个仓库
- [ ] 添加根目录 `.gitignore`（见下方模板）
- [ ] 更新 `README.md` 中的路径（`YYnet-main` → 新名）
- [ ] 补 `LICENSE`（MIT / Apache-2.0 等）

### 6.3 建议的 `.gitignore` 模板

```gitignore
# Secrets
.env
*.env.local

# Python
__pycache__/
*.py[cod]
.venv/
*.egg-info/

# IDE
.idea/
.vscode/

# Logs & temp
*.log
_analysis_vec_tmp/
_chat_upload_tmp/
**/chroma.sqlite3

# Data & models (large)
_e2e_sandbox/
DATA/
*.tif
*.tiff
*.shp
*.shx
*.dbf
*.prj
*.cpg
*.safetensors
*.bin
*.pth
Qwen*/
qwen_rs_agent_lora/
Qwen_Agent_Merged/
Llama-Factory/

# Test outputs
jb/e1_workspace/outputs_e1/
jb/e1_workspace/E1_rasters/

# OS
Thumbs.db
.DS_Store
```

### 6.4 上传后仓库应包含的最小集

- 应用 + 引擎 + 模型定义（`src/` 或整理后的 `YYnet/`）
- `requirements.txt`
- `tests/` + 沙盒生成脚本
- `docs/`（含 QA 报告可选）
- `scripts/` 启动脚本
- `.env.example`
- `README.md`

---

## 七、建议的执行顺序（分三步，降低风险）

### 第一步：清理（1 天，不改逻辑）

1. 移出/删除：`Llama-Factory`、`Qwen*`、`qwen_rs_agent_lora`、`.idea`
2. 删除：`gee_engine.py`、`jb/Qwen2.5-7B-Instruct/`、`jb/data.py`（或脱敏）
3. 把诊断日志、导师汇报移到 `docs/reports/`
4. 写好根 `.gitignore`

### 第二步：重命名（2–3 天，需跑测试回归）

1. `YYnet-main` → 新项目名（如 `cstf-app`）
2. 按第四节对照表重命名核心文件
3. 全局替换 import（`pre_engine` → `inference_engine` 等）
4. 跑 `python tests/e2e/run_diagnostics.py` 确认 13/13

### 第三步：上传 GitHub

1. 在整理后的目录 `git init`
2. 首次 commit：`chore: initial clean repository structure`
3. 创建 GitHub 仓库（建议名：`tidal-flat-rs` 或 `cstf-platform`）
4. push 前再用 `git status` 确认无 `.env`、无 `.tif`、无模型权重

---

## 八、仓库命名建议

| 候选名 | 优点 | 缺点 |
|--------|------|------|
| `YYnet` | 与论文/现有 README 一致 | 名字偏内部代号 |
| `cstf-platform` | 潮滩系统（CSTF）平台感强 | 需解释缩写 |
| `tidal-flat-rs` | 英文直观，遥感方向明确 | 与 YYnet 品牌不一致 |
| `TF_Agent` | 与现有 GitHub 远程一致 | 偏 Agent，未体现 RS 主功能 |

**建议**：若延续现有远程 `TF_Agent`，可在 README 标题写「YYnet / 潮滩遥感分析平台」，仓库名可不改。

---

## 九、你需要做的决定（请确认后我可代为执行）

1. **新项目名称**：`YYnet` / `cstf-app` / `tidal-flat-rs` / 其他？
2. **jb 是否进同一仓库**：合并为 `research/` 还是单独仓库？
3. **重构深度**：方案 A（完整 `src/` 包）还是方案 B（只改名挪目录）？
4. **现有 GitHub 远程 `TF_Agent`**：覆盖推送还是新建仓库？

确认以上 4 点后，我可以按你的选择**直接在工作区执行重命名、移动目录、更新 import，并生成 `.gitignore` 与 `.env.example`**。

---

*本规划基于 2026-07-09 工作区扫描生成。*
