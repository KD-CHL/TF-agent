# Agent 明显限制与缺口整改任务清单

> **执行约束：** 本文件是跨子系统整改总清单，不直接授权修改代码。每个任务开始前，必须单独确认范围并形成可执行实施计划；实施时优先使用测试驱动，完成后独立评审与验收。

**目标：** 将当前 Agent 从“功能较完整的内部原型/验收版”逐步提升为行为可信、数据边界清晰、可恢复、可跨平台验证的工程化系统。

**总体策略：** 先建立可复现测试基线，再修复会导致错误判断或错误执行的 P0 问题；随后收敛执行架构、模型后端、会话与知识库；最后补齐任务恢复、真实外部链路和浏览器验收。各阶段保持现有手动侧栏流程可用，不以大规模重写替代小步迁移。

**技术栈：** Python、Streamlit、LangGraph/LangChain、DashScope/OpenAI-compatible API、PyTorch、Rasterio/GeoPandas、Google Earth Engine、ChromaDB、FastAPI、Cesium。

**审计基线：** `main@1dc08ba`，2026-08-22。证据以当前代码和本地只读验证为准，不沿用历史文档中的测试数量或完成状态。

## 全局约束

- [ ] 不得在缺少真实工具输出时宣称下载、推理、评价、变化检测或报告生成成功。
- [ ] 所有重型操作继续保留“计划 → 确认 → 执行 → 验证 → 登记 → 回复”闭环。
- [ ] 手动 UI 与 Agent 入口必须共享同一套参数校验和执行语义。
- [ ] 默认不向外部模型发送密钥、绝对路径、未授权影像或精确空间范围。
- [ ] 单元测试不得依赖真实 DashScope、GEE、GPU、公网或开发机固定盘符。
- [ ] 支持环境以 Python 3.10/3.11 为基线；Windows 与 macOS/Linux 的路径测试必须使用平台无关断言。
- [ ] 每个任务必须先出现能够稳定复现问题的失败测试，再进行实现。
- [ ] 每个任务独立提交；不得把无关重构、格式化或历史文档改写混入修复提交。
- [ ] 本清单中的“完成”必须同时满足代码、测试、文档和验收标准，不能只勾选实现步骤。

## 状态说明

| 标记 | 含义 |
|---|---|
| `[ ]` | 未开始 |
| `[~]` | 处理中；在任务标题后注明负责人和分支 |
| `[x]` | 已通过验收；在任务末尾记录提交和验证证据 |
| `[!]` | 外部条件阻塞；写明凭据、数据、硬件或决策依赖 |

## 推荐执行顺序

| 阶段 | 任务 | 退出条件 |
|---|---|---|
| Phase 0 · 基线 | AGENT-000 | 新机器可按单一命令运行核心单元测试，失败可区分环境问题与代码回归 |
| Phase 1 · P0 正确性 | AGENT-001 → 002 → 003 → 004 | Workflow 预检、能力状态、DAG 语义和命令校验均由测试证明 |
| Phase 2 · P0 安全边界 | AGENT-005 ∥ 006（公开入口优先） | 外部模型数据发送有明确授权；公网入口默认不可匿名执行任务 |
| Phase 3 · P1 架构收敛 | AGENT-007 → 008 → 009 → 010 | 单一执行语义、可配置模型后端、受控会话上下文和可维护知识库形成闭环 |
| Phase 4 · P1/P2 运行保障 | AGENT-011 → 012 | 后台任务可恢复，真实 GEE/GPU/LLM/浏览器验收有可追溯证据 |

## 任务总览

| ID | 优先级 | 任务 | 主要依赖 | 状态 |
|---|---|---|---|---|
| AGENT-000 | P0 | 建立可复现、跨平台的测试与依赖基线 | 无 | [~]（当前会话） |
| AGENT-001 | P0 | 恢复 Workflow 全局预检真实调用链 | 000 | [~]（当前会话） |
| AGENT-002 | P0 | 修正能力注册表判定与缓存生命周期 | 000 | [~]（当前会话） |
| AGENT-003 | P0 | 修正可选步骤失败导致 Workflow 整体失败 | 000、001 | [~]（当前会话） |
| AGENT-004 | P0 | 为 Agent 系统命令建立强类型 Schema 与统一校验 | 000 | [~]（当前会话） |
| AGENT-005 | P0 | 建立外部 LLM 数据最小化与用户授权机制 | 004 | [~]（当前会话） |
| AGENT-006 | P0 | 为远程 Gateway 和本地模型 API 增加访问控制 | 004；可与 005 并行 | [~]（当前会话） |
| AGENT-007 | P1 | 收敛新旧两套任务执行路径 | 001、002、003、004 | [~]（当前会话） |
| AGENT-008 | P1 | 抽象 LLM 后端并移除导入期硬失败 | 002、004、005 | [~]（当前会话） |
| AGENT-009 | P1 | 增加受控会话持久化与上下文预算 | 004、005、008 | [~]（当前会话） |
| AGENT-010 | P1 | 补齐知识库入库、版本和健壮性闭环 | 005、008 | [~]（当前会话） |
| AGENT-011 | P1 | 将后台线程状态升级为可恢复任务状态 | 007 | [~]（当前会话） |
| AGENT-012 | P2 | 建立真实外部链路、浏览器验收与文档证据更新流程 | 001–011 | [~]（离线矩阵完成，真实链路待受控验收） |

---

## [~] AGENT-000：建立可复现、跨平台的测试与依赖基线（当前会话）

**优先级：** P0，所有代码修复的前置任务。

**问题证据：**

- 当前环境缺少 `pytest`、NumPy、Rasterio、GeoPandas、Torch、Streamlit 等依赖，完整测试无法运行。
- `TF-agent/requirements.txt` 不包含测试运行器；CI 在 `.github/workflows/ci.yml:29-38` 临时安装另一套依赖。
- `tests/unit/test_agent_commands.py:215-223` 固定期待 Windows 反斜杠，在 macOS 上出现 20/21 通过、1 项路径分隔符失败。
- README 推荐 Python 3.10，CI 使用 Python 3.11，当前开发机默认 Python 3.12，缺少明确支持矩阵。

**解决步骤：**

- [x] 新增独立测试依赖清单，固定 `pytest`、测试覆盖率工具和测试需要的最小包；运行依赖与训练依赖分开。
- [x] 在 README 和 CI 中统一 Python 3.10/3.11 支持范围及安装命令。
- [x] 将路径断言改为比较 `os.path.normpath` 后的语义，Windows 路径语义测试使用 `ntpath`，并补齐 `_root` 路径字段规范化。
- [x] 提供一个核心单元测试命令和一个完整单元测试命令；两者均禁止访问真实外部服务。
- [~] CI 已增加 Python 3.10/3.11 Ubuntu 矩阵、Windows 路径/持久化契约 job 和失败摘要；远程 CI 尚未执行。
- [x] 增加最小应用启动烟测：用隔离端口启动 Streamlit，验证根页面可渲染；该烟测不调用外部 LLM。
- [x] CI Ubuntu 核心安装块已显式覆盖 FastAPI/Uvicorn、HTTP 代理、图像/PDF 和 embedding 运行依赖，不再依赖 hosted runner 的预装包；新增静态依赖契约测试。

**预计涉及文件：**

- 新增：`TF-agent/requirements-test.txt`
- 修改：`TF-agent/requirements.txt`（补充 PDF 运行依赖 `reportlab`）
- 修改：`.github/workflows/ci.yml`
- 修改：`README.md`、`TF-agent/README.md`
- 修改：`tests/unit/test_agent_commands.py`、`tests/unit/test_p0_hardening.py`
- 修改：`TF-agent/agent_command_bridge.py`（补齐 `_root` 路径字段规范化）
- 新增：`tests/smoke/__init__.py`、`tests/smoke/test_app_boot.py`

**验收标准：**

- [x] 全新环境按文档命令安装后，能够直接执行核心单元测试（当前 Conda/Python 3.10：204 项含烟测通过）。
- [ ] Python 3.10 和 3.11 CI 全部通过（等待远程 CI 执行）。
- [~] macOS/Linux 路径测试已在本地通过；Windows 3.10/3.11 runner 已配置 portable contract，尚未获得远程执行证据。
- [x] 测试报告能明确区分“依赖缺失”“外部凭据缺失”和“代码断言失败”；基线阶段剩余的 3 项 GEE P0 失败已转入 AGENT-001/002 并修复，当前全量结果单独记录在各阶段证据中。
- [~] 本地烟测已验证根页面实际渲染；至少一个 CI 环境的根页面验收等待远程 CI 执行。

**阶段验证记录（未完成最终跨平台验收）：**

- `conda run -n tf-agent python -m pip check`：通过。
- 历史基线（修复前）：核心烟测与单元测试 `122 passed`；全量 `377 passed, 3 failed`，失败集中于 GEE 模块名/项目能力判定和 GEE 确认桥接，已转入 AGENT-001/002 处理。
- 当前本地复核：核心命令（含 AppTest 与 API/Gateway 认证）`204 passed`；`tests/unit` 单元测试 `592 passed, 2 warnings`，仓库全量 `pytest -q` 为 `598 passed, 3 skipped`；远程 CI 仍未执行。
- 当前本机 Python 3.11 隔离 venv 已执行 portable contract：`tests/unit/test_agent_context_policy.py tests/unit/test_execution_request_contract.py tests/unit/test_job_recovery.py` 为 `43 passed`；该证据覆盖跨平台上下文、统一入口、跨进程账本和身份冲突门闩，不替代完整依赖矩阵或 Windows runner。
- 同一 Python 3.11 隔离 venv 加入时间线账本与子进程突然退出恢复回归后，本轮组合验证为 `67 passed`；覆盖空间元数据持久化脱敏、取消优先级、可配置时间线账本、跨进程 JobStore 恢复和现有跨平台契约。
- 本机曾用独立 Python 3.11 临时环境安装 `requirements.txt` 与 `requirements-test.txt`（新增脱敏回归测试之前），历史全量测试套件为 `508 passed, 1 warning`；离线核心矩阵状态 `PASS`（`164 passed`），且无子进程崩溃。该历史结果仅作补充，不替代当前 Python 3.11 portable contract 或远程 runner。
- 远程 GitHub Actions、Python 3.11/Windows runner：尚未执行，不能宣称 AGENT-000 全部完成；历史本地 Python 3.11 已有独立全量与核心矩阵证据。

**建议提交：** `test: establish reproducible cross-platform agent test baseline`

---

## [~] AGENT-001：恢复 Workflow 全局预检真实调用链（当前会话）

**优先级：** P0，直接影响用户确认前能否看到真实阻塞条件。

**问题证据：**

- `TF-agent/agent_command_bridge.py:1008-1024` 导入仓库中不存在的 `assets_registry` 模块。
- 同一代码调用不存在的 `capability_registry.load_capabilities()`。
- 异常被空 `except` 静默吞掉，导致 `validate_analysis_workflow()` 可能完全没有执行。
- 当前计划可以在缺少目录、权重、GEE 能力或完整 AOI 几何时进入确认阶段，直到子步骤执行才失败。

**解决步骤：**

- [x] 在 `tests/unit/test_workflow_orchestrator.py` 和桥接测试中增加“全局校验必须被调用”的失败用例，覆盖缺目录、缺模型、GEE 阻断和可选数据缺失；AOI/年份校验继续由既有校验覆盖。
- [x] 为现有资产注册表读取逻辑提供唯一公共入口 `workflow_orchestrator.load_assets_registry()`，停止导入不存在的模块。
- [x] 数据集资产 registry 读取损坏时保留 `.corrupt-*` 证据，写入使用临时文件 + `os.replace`，避免并发/中断造成半文件。
- [x] 复用 `capability_registry.Registry.statuses()` 作为公开只读状态接口，返回 `capability_id -> status`，不暴露路径和密钥。
- [x] 让 `propose_workflow_plan()` 显式处理校验异常：写入用户可见 blocker，并用安全摘要替代内部异常文本，禁止继续确认。
- [x] 为 Workflow 明确必需字段：有效 AOI 几何、目标年份、影像/成果/掩膜目录、模型权重，以及 GEE 项目条件。
- [x] 增加使用真实模块接口的桥接测试，并通过静态契约检查确保不再引用不存在的 `assets_registry` 或 `load_capabilities()`。

**预计涉及文件：**

- 修改：`TF-agent/agent_command_bridge.py`
- 修改：`TF-agent/workflow_orchestrator.py`
- 修改：`TF-agent/capability_registry.py`
- 测试：`tests/unit/test_workflow_orchestrator.py`
- 测试：`tests/unit/test_agent_commands.py`

**验收标准：**

- [x] 任一必需条件缺失时，计划展示具体 blocker，确认操作被拒绝。
- [x] 可选 E1 真值或 M5 基线缺失时只生成 warning/skip，不错误阻断必需步骤。
- [x] 全局校验异常不再被静默吞掉，且错误文本不包含密钥或完整绝对路径。
- [x] 计划层 blocker 与执行层验证结果一致；被阻断的直接 `run_workflow` 不再返回可执行动作类型。
- [x] `agent_command_bridge.py` 可在测试环境直接导入，并且调用的是仓库中真实存在、签名匹配的资产与能力接口。

**阶段验证记录（尚未完成跨 Python/Windows/远程 CI 验收）：**

- `tests/unit/test_agent_commands.py tests/unit/test_workflow_orchestrator.py`：106 passed。
- `pip check`：通过。
- 历史基线（AGENT-002 修复前）全量单元测试为 `382 passed, 3 failed`；失败已由能力状态与确认语义修复覆盖。
- 当前 `tests/unit` 单元测试：`592 passed, 2 warnings`；仓库全量 `pytest -q`：`598 passed, 3 skipped`。
- Workflow 与 Streamlit 资产登记现共用严格的 `load_assets_registry()` 读取入口；非法 JSON/记录会保留 `.corrupt-*` 证据并拒绝继续写入，不再静默视为空库。
- 参考数据集 registry 对非 object 记录也采用同样的 fail-closed 处理，避免目录查询将损坏条目当作可用数据集。
- 推理登记、成果报告、知识库 manifest 与 GEE 任务账本也复用同一 fail-closed 原则；损坏文件不会被当作空库覆盖，GEE 账本的远端错误摘要会先脱敏。
- 远程 GitHub Actions 与 Windows runner：待 AGENT-000 最终验收；本地 Python 3.11 已通过全量单元与离线核心矩阵，但不替代远程 runner。

**建议提交：** `fix(agent): restore workflow global preflight validation`

---

## [~] AGENT-002：修正能力注册表判定与缓存生命周期（当前会话）

**优先级：** P0，能力状态会直接影响 Agent 的工具选择与用户判断。

**问题证据：**

- `TF-agent/capability_registry.py:293-301` 检查模块名 `gee`，而 Earth Engine Python API 的实际导入名为 `ee`。
- `TF-agent/app.py:4271-4279` 和 `4312-4319` 无参数调用 `invalidate()`，但方法签名要求 `capability_id`；异常被吞掉，缓存可能保持旧值。
- 聊天兜底路径可能用空 context 创建能力注册表，导致模型路径、知识库和任务状态判断失真。
- 敏感值过滤没有通用覆盖 macOS `/Users/...` 绝对路径。

**解决步骤：**

- [x] 添加失败测试，证明安装 `ee` 且存在项目配置时状态为 `CONDITIONAL`，缺项目时为 `UNAVAILABLE`；网络未验证仍通过 warning 表达。
- [x] 将 Earth Engine 导入探测统一为真实模块 `ee`，并保持与 `m4_engine._resolve_ee_project` 相同的多源项目解析规则。
- [x] 完成后需要全量刷新时统一调用 `bump()`；单项刷新继续使用 `invalidate(capability_id)`。
- [x] 抽取 `capability_registry.build_context()`，侧栏面板、聊天注入和 Workflow 预检使用同一份 model、task、AutoTune 和知识库配置。
- [x] 能力上下文的知识库目录改为调用 `knowledge_store.knowledge_db_path()`，环境变量、Agent 查询、CLI 与能力检查不再各自拼接默认路径。
- [x] 使用通用绝对路径识别规则清理 evidence，覆盖 POSIX、Windows drive 和 UNC 路径。
- [x] 为每项状态定义“静态可用”和“运行时已验证”的差异；只有真实闭环显式调用 `mark_runtime_verified()` 后才提升验证级别。
- [x] 逐项审计 9 个默认能力谓词，使地图、推理、GEE、E1、M5、AutoTune、PDF 和知识库状态检查各自的真实运行前提；E1/M5 不再复用无关的通用模型路径。

**预计涉及文件：**

- 修改：`TF-agent/capability_registry.py`
- 修改：`TF-agent/app.py`
- 修改：`TF-agent/m4_engine.py`（仅在需要复用公共项目解析函数时）
- 测试：`tests/unit/test_capability_registry.py`
- 测试：`tests/unit/test_gee_agent_loop.py`

**验收标准：**

- [x] GEE、推理、知识库、报告、E1、M5 能力状态已覆盖各自主要静态前提；真实运行级验证仍按 AGENT-012 单独记录。
- [x] 推理或下载完成后，下一轮能力快照已通过 `bump()` 全量刷新。
- [x] 能力摘要/证据中不出现 API Key、token、代理凭据或 POSIX/Windows/UNC 绝对路径。
- [x] 同一 context 在 UI 面板、Agent prompt 和 Workflow 预检中通过共享构造函数保持一致。

**阶段验证记录（真实外部服务运行级验证待 AGENT-012）：**

- `tests/unit/test_capability_registry.py tests/unit/test_gee_agent_loop.py::TestB12Integration`：31 passed，另有 2 条第三方 warning；新增 E1/M5 前置条件、static/runtime 验证级别和能力异常摘要脱敏测试。
- 当前 `tests/unit` 单元测试：`592 passed, 2 warnings`；仓库全量 `pytest -q`：`598 passed, 3 skipped`。
- 2 条 warning 均为现有第三方依赖的 Python 3.10/deprecation 提示，不影响测试结果。

**建议提交：** `fix(agent): align capability detection and cache invalidation`

---

## [~] AGENT-003：修正可选步骤失败导致 Workflow 整体失败（当前会话）

**优先级：** P0，当前行为与代码声明的“部分成功”语义冲突。

**问题证据：**

- 最小复现：GEE、推理成功，自动可选 E1 失败，M5 跳过时，PDF 因 `依赖失败: e1_quality` 被标为 `BLOCKED`，最终 Workflow 为 `FAILED`。
- `TF-agent/workflow_orchestrator.py:638-650` 把任意失败依赖都视为硬阻塞，没有区分必需与可选依赖。
- 当前测试只直接调用 `_evaluate_workflow_status()` 验证可选失败，没有覆盖真实 DAG 调度到 PDF 的路径。

**解决步骤：**

- [x] 增加完整 DAG 回归测试：自动可选 E1 失败、M5 跳过后，PDF 仍执行，最终为 `COMPLETED_WITH_WARNINGS`。
- [x] 固定两组 DAG fixture：自动 E1 失败为部分成功；用户必选 E1 失败为整体失败并阻断 PDF，同时断言摘要。
- [x] 在步骤结构中区分硬依赖 `depends_on` 与软依赖 `optional_depends_on`。
- [x] 用户明确要求 E1/M5 时，将对应步骤放入 PDF 的硬依赖；自动“有则执行”时放入软依赖。
- [x] 调度器等待软依赖进入终态，但软依赖失败不阻断下游；失败原因进入结果 warnings 与账本步骤记录。
- [x] 结果摘要明确列出主成果完成状态和可选步骤失败原因。

**预计涉及文件：**

- 修改：`TF-agent/workflow_orchestrator.py`
- 测试：`tests/unit/test_workflow_orchestrator.py`
- 验收：`tests/acceptance/run_workflow_acceptance.py`

**验收标准：**

- [x] 自动可选步骤失败时，PDF 仍能基于现有成果生成。
- [x] 用户明确要求的 E1/M5 失败仍使 Workflow 失败，并阻断依赖其结果的步骤。
- [x] 跳过、可选失败、必需失败三种状态在账本和聊天摘要中可区分。
- [~] 重跑去重沿用既有资产复用语义，需在真实后台任务恢复验收（AGENT-011）中补充跨进程证据。

**阶段验证记录（尚未完成跨进程后台恢复验收）：**

- `tests/unit/test_workflow_orchestrator.py`：80 passed；PDF、E1/M5 引擎适配器在报告/资产登记失败时均返回失败，不再伪造成功；空成果选择也会被拒绝；可选步骤被阻塞时也会保持 `COMPLETED_WITH_WARNINGS` 并写入可操作 warning；血缘记录不再调用 Git 子进程；账本/血缘/资产 registry JSON 损坏会保留 `.corrupt-*` 证据并拒绝写入；`COMPLETED_WITH_WARNINGS` 在时间线保持 `WARNING`。
- `tests/unit/test_pdf_report.py`：11 passed；报告生成异常摘要已脱敏，不泄露本地路径或凭据片段。
- `tests/unit/test_asset_registry_schema.py`：3 passed；历史 Windows 路径格式可读，非法记录读取时过滤，写入边界拒绝非有限数字和错误类型。
- 核心回归（smoke + bridge + workflow + timeline）：`204 passed`；历史 Python 3.11 组合运行不再触发原生 segfault。
- 软依赖失败与硬依赖失败均由真实 DAG 调度路径覆盖；远程 CI/Windows runner 待 AGENT-000 最终验收。
- JobStore 已在 `BEGIN IMMEDIATE` 事务内按活动 `plan_id` 去重；同一计划跨进程复用已有 job，终态计划仍允许显式新 job 重跑。该证据覆盖后台启动去重，不替代真实远端资产复用验收。

**建议提交：** `fix(workflow): separate hard and optional step dependencies`

---

## [~] AGENT-004：为 Agent 系统命令建立强类型 Schema 与统一校验（当前会话）

**优先级：** P0，当前文本协议是 LLM 输出到系统状态的主要边界。

**问题证据：**

- `dispatch_system_command()` 仅包装字符串，不验证 JSON schema。
- Bridge 虽然逐字段做部分转换，但 map JSON 路径缺少完整经纬度/缩放范围校验。
- 用户可以在聊天框直接粘贴 `[SYSTEM_COMMAND_JSON]` 绕过 LLM。
- 新 JSON、旧 `COMMAND_*` 和自然语言坐标存在三套解析路径，错误语义不统一。

**解决步骤：**

- [x] 使用 Pydantic 定义 `SystemCommand`、`MapCommand`、`SidebarDelta` 和按 `type` 校验的 `PendingAction` 模型。
- [x] 增加越界坐标、未知字段、非法 action、legacy 适配和未确认重型操作回归测试；路径/日期/阈值继续由既有 bridge 校验覆盖。
- [x] 所有命令在进入 `queue_agent_command()` 前完成 schema 验证，在 `apply_system_command()` 前再次验证可信内部对象。
- [x] 保留 legacy 文本解析为只读兼容适配器；适配后立即转换为相同 Schema，不走独立执行路径。
- [x] 原始命令输入默认拒绝；仅在 `CSTF_ALLOW_RAW_SYSTEM_COMMAND=1` 且当前会话勾选开发授权时进入统一解析入口，重型任务仍需二次确认。
- [x] 校验错误统一返回安全摘要，不输出堆栈或敏感输入原文。

**预计涉及文件：**

- 新增：`TF-agent/agent_command_schema.py`
- 修改：`TF-agent/agent_command_bridge.py`
- 修改：`TF-agent/agent.py`
- 修改：`TF-agent/app.py`
- 测试：`tests/unit/test_agent_commands.py`
- 测试：`tests/unit/test_p0_hardening.py`

**验收标准：**

- [x] 非法命令不能修改 session state，也不能创建 pending task。
- [x] JSON、legacy 命令和内部 UI 操作最终进入相同 schema。
- [x] 地图坐标、日期、阈值、枚举和 action 已覆盖主要边界；新增 `M4Parameters` 及 action/sidebar 的日期、阈值嵌套校验，保留兼容字段的安全回退。
- [x] 未确认重型任务在任何入口都只能进入待确认状态。

**阶段验证记录（历史兼容字段仍允许 bridge 级安全回退）：**

- `tests/unit/test_agent_commands.py tests/unit/test_p0_hardening.py`：69 passed。
- 当前 `tests/unit` 单元测试：`592 passed, 2 warnings`；仓库全量 `pytest -q`：`598 passed, 3 skipped`。
- 新增 `TF-agent/agent_command_schema.py`；命令校验在入队和执行两个边界均生效，`M4Parameters` 拒绝倒置日期/土地阈值并规范云量、像元数和尺度。原始聊天命令增加环境开关 + 当前会话授权双门闩。
- Workflow 返回 `COMPLETED_WITH_WARNINGS` 时，主时间线事件现在使用 `WARNING`，不再把可选步骤失败显示为无条件成功；新增状态映射回归测试。

**建议提交：** `feat(agent): validate system commands with typed schemas`

---

## [~] AGENT-005：建立外部 LLM 数据最小化与用户授权机制（当前会话）

**优先级：** P0，涉及本地数据、路径和空间信息发送到第三方服务。

**问题证据：**

- `build_agent_sidebar_context()` 会注入原始影像目录等本地路径。
- `build_dataset_catalog_for_agent()` 会注入 `primary_path`。
- 图片和 GeoTIFF 内容以 data URL 发送到 DashScope；元信息可能包含精确 bounds、CRS 和分辨率。
- 当前系统提示词只要求模型不要在回复中展示路径，但没有阻止路径被发送给模型。

**解决步骤：**

- [x] 增加默认 minimal 策略层：外部上下文仅保留稳定 id、basename 和存在性；路径/凭据由统一清理器移除。
- [x] 将侧栏与数据集资产上下文中的绝对路径替换为文件描述和可用状态；执行层仍在本地解析真实路径。
- [x] 当前对话 UI 不显示外部发送确认开关；影像/GeoTIFF 外发与精确空间元数据默认固定拒绝，避免历史会话状态扩大边界。
- [x] `agent.chat_with_vlm()` 核心函数再次校验媒体授权；非 UI 调用者也不能绕过会话授权直接发送影像，GeoTIFF 元数据异常统一脱敏。
- [x] 将精确 bounds/CRS 元数据和地图 geometry 纳入独立空间隐私开关，默认不发送完整 geometry。
- [x] 临时上传文件删除放入 `finally`，异常与 TIFF 重试路径均清理。
- [x] 增加策略单元测试，断言不包含 `/Users/`、盘符路径、UNC、token 和代理凭据。

**预计涉及文件：**

- 新增：`TF-agent/agent_context_policy.py`
- 修改：`TF-agent/agent_command_bridge.py`
- 修改：`TF-agent/dataset_assets.py`
- 修改：`TF-agent/agent.py`
- 修改：`TF-agent/app.py`
- 测试：新增 `tests/unit/test_agent_context_policy.py`
- 测试：扩展 `tests/unit/test_p0_hardening.py`

**验收标准：**

- [x] 默认会话不会把绝对路径、密钥、代理凭据、完整 AOI 或影像发送给外部模型。
- [x] 当前 UI 无外部数据授权入口；运行时 gate 仍按会话状态拒绝外发，未来重新开放前需单独设计可见、可撤销授权。
- [x] 执行本地任务仍能通过资产 ID 找到真实文件，不要求 LLM 知道路径。
- [x] 上传成功、失败、超时和 TIFF 降级重试后均清理原始临时文件。

**阶段验证记录（预览缓存已纳入跨会话清理）：**

- `tests/unit/test_agent_context_policy.py tests/unit/test_agent_media_gate.py tests/unit/test_agent_commands.py tests/unit/test_p0_hardening.py`：87 passed；桥接异常、AOI/Workflow 构建错误、直接 Agent 调用、知识库输出和媒体/GeoTIFF 元数据异常会在进入 UI/Agent 摘要前脱敏路径和凭据。
- 通过策略测试验证 POSIX/Windows/UNC 路径、token、代理凭据、未授权地图中心不会进入外部上下文；AOI 精确 bbox/centroid 也默认脱敏。
- 历史消息上下文在未授权时继续脱敏 `bbox`、`centroid`、`map_center` 以及 GeoTIFF 元数据行中的 `bounds`、`crs`、`resolution`；会话持久化不保存这些精确空间字段，显式空间授权才允许本轮模型上下文保留标注字段。
- 影像外部发送与精确空间元数据在当前对话 UI 中固定关闭；保留策略 gate 以防旧 session 状态被复用。
- `TF-agent/preview_cache.py` 仅清理带 `preview_*.png` 前缀的本地 UI 临时文件，按 7 天/200 个文件上限执行，跳过符号链接并返回不含路径的汇总计数；Streamlit 新会话初始化时触发清理，目录可由 `CSTF_CHAT_PREVIEW_DIR` 覆盖。
- `tests/unit/test_preview_cache.py`：4 passed，覆盖过期清理、数量上限、符号链接安全和显式目录配置；预览文件不会随会话消息持久化，清理失败不会阻断工作台启动。
- `safe_error_summary()` 在脱敏器自身异常时仍只返回异常类型，避免错误处理路径二次泄露或崩溃；新增回归测试已纳入全量单元测试。
- 知识库检索结果、直接调用 `chat_with_vlm()` 的历史/目录文本也会在进入模型上下文前脱敏、去除精确空间字段并限制长度；报告生成器和成果报告适配器的 POSIX/Windows 路径与异常摘要同样经过统一清理。
- 任务时间线账本的 message/error/details 也复用同一空间字段脱敏策略，避免 `bounds`、`crs`、`resolution` 或 geometry 在本地持久化时绕过会话隐私边界。
- PDF 报告文本在进入 ReportLab `Paragraph` 前统一 HTML 转义，避免任务名、错误摘要或时间线消息中的 `<...>` 标记破坏生成或注入意外格式。

**建议提交：** `feat(agent): enforce minimal external model data policy`

---

## [~] AGENT-006：为远程 Gateway 和本地模型 API 增加访问控制（当前会话）

**优先级：** P0，远程演示时匿名访问者目前可能提交执行命令；本任务不依赖数据最小化实现，可在 AGENT-004 后与 AGENT-005 并行，公开入口已启用时应优先处理。

**问题证据：**

- `cstf_gateway.py` 默认监听 `0.0.0.0`，转发所有 HTTP 方法和 Streamlit WebSocket，没有身份校验。
- `api_server.py` 监听 `0.0.0.0:8000`，OpenAI-compatible 路由无认证。
- 命令确认是业务门闩，不是用户身份或权限授权。

**解决步骤：**

- [x] 本地模型 API 默认绑定 `127.0.0.1`；ASGI lifespan 与直接 CLI 启动都会校验 token，非 loopback 绑定缺少 token 时 fail closed。
- [x] Gateway 在非 loopback 或设置公开 URL 时要求 `CSTF_GATEWAY_ACCESS_TOKEN`，缺失则 fail closed。
- [x] 浏览器通过认证表单 POST body 提交 token；服务端常量时间比较并签发随机 session ID，令牌不进入 URL/localStorage/cookie；Cookie 设置 HttpOnly、SameSite=Strict，最长 8 小时。
- [x] 会话保存为内存短时状态；重启或轮换 token 时旧会话失效；状态修改请求校验 Origin 与 CSRF token。
- [x] HTTP 与 WebSocket 共用 session 校验已接入；缺失/非法握手关闭，并已在本机真实 Streamlit 上游浏览器链路验证；远程 ngrok/公网环境仍待验证。
- [x] `api_server.py` 要求 Bearer token，并设置请求体、并发和最大生成长度上限。
- [x] API 与 Gateway 同时限制带 `Content-Length` 和 chunked 传输的请求体，超限统一返回 413。
- [x] API/Gateway 不输出 token、完整 prompt、图片 base64 或代理 URL；错误响应仅返回安全摘要。
- [x] Gateway/API 启动时读取被忽略的 `TF-agent/.env` 作为配置回退，同时保留显式进程环境优先，避免演示脚本与模块读取不同配置。
- [x] `REMOTE_DEMO.md` 和 `.env.example` 已补充本地、公开绑定与 ngrok 的认证启动说明。

**预计涉及文件：**

- 修改：`TF-agent/cstf_gateway.py`
- 修改：`TF-agent/api_server.py`
- 修改：`TF-agent/REMOTE_DEMO.md`
- 修改：`TF-agent/.env.example`
- 测试：新增 `tests/unit/test_gateway_auth.py`
- 测试：新增 `tests/unit/test_local_api_auth.py`

**验收标准：**

- [x] 未认证 HTTP 请求被拒绝；WebSocket 在认证/Origin 不通过时不会 accept。
- [x] 已覆盖登录成功/失败、会话过期、密钥轮换、CSRF、非法 Origin，以及本机真实浏览器 WebSocket 握手与登出页面；远程 ngrok/公网验收仍待补充。
- [x] token 不出现在 URL、日志、错误消息和响应体。
- [x] 本地 loopback 开发流程保持简单，公开绑定缺少凭据时 fail closed。
- [~] 认证成功后的 Streamlit 静态资源和 WebSocket 已在本机真实上游验收；Globe 路径及实际 ngrok/公网环境仍需补充验收。

**阶段验证记录（本机真实上游已验收，远程公网链路待补充）：**

- `tests/unit/test_gateway_auth.py`：13 passed，包含 WebSocket 未认证拒绝、CSRF 登出/会话撤销、chunked 请求体上限、上游异常不回显内部 URL/凭据、HTTP 公网 URL 不错误标记 Secure cookie，以及仅移除边缘 session、保留 Streamlit 上游 Cookie 的回归。
- `tests/unit/test_local_api_auth.py`：5 passed，覆盖 ASGI lifespan 缺 token 启动失败和 chunked 请求体上限。
- Gateway 会话支持过期、登出和 token 轮换失效；本地 API 支持 Bearer、请求体和生成长度限制。
- 显式 Chromium 验收已在隔离本地 Gateway 上验证登录表单、错误 token、会话查询、CSRF 登出和未认证 WebSocket；新增真实本地 Streamlit 上游代理验收后为 `3 passed`，覆盖认证后根页面、Streamlit 健康接口和 WebSocket 驱动页面渲染；真实 ngrok/远程 Streamlit 上游仍待远程环境。

**建议提交：** `feat(security): protect remote gateway and local model api`

---

## [~] AGENT-007：收敛新旧两套任务执行路径（当前会话）

**优先级：** P1，降低同一任务在手动 UI 与 Agent 中行为不一致的风险。

**问题证据：**

- 新可信推理使用 `inference_agent_loop`，旧 `run_pipeline_sync` 仍直接调用底层引擎。
- 两条路径的计划、确认、验证、资产登记、M5/E1 后置语义和时间线并不完全一致。
- 手动按钮和 Agent 命令可能生成不同 pending schema。

**解决步骤：**

- [x] 定义统一 `ExecutionRequest`，包含 task、mode、plan_id、confirmation_source 和已验证参数快照。
- [x] 手动 UI 与 Agent 已在启动执行前附加同一请求契约；深度学习、指数法和 M4/GEE 计划均在启动线程前统一构建并校验。
- [x] 深度学习 Agent pending 和手动按钮均映射到 `inference_agent_loop`；指数法均映射到 `index_agent_loop`，M4/GEE 均映射到 `gee_agent_loop`，旧同步函数仅保留为无计划历史兼容入口。
- [x] 指数法入口已在契约中独立标识为 `index_agent_loop`；新增适配器负责计划校验、真实执行和结果验证，旧 UI 收尾逻辑通过适配器调用。
- [x] 旧 `run_pipeline_sync` 已明确标记为兼容旧入口，不再作为新的请求构建器。
- [x] 固定生产入口映射表，兼容层只负责参数快照，不复制执行算法。
- [x] `agent_task_framework.py` 已明确标记为 compatibility-only；生产请求不得从该模块进入后台执行，后续仅需清点历史调用后删除。
- [x] 增加契约测试：相同参数从 UI/Agent 生成的请求核心字段和入口一致。
- [x] 独立 M5/E1 后置路径只有输出校验通过才登记或加载成果；校验失败的摘要、时间线和 JobStore 状态均明确标记未完全通过。
- E1/M5 报告与可选空间成果统一要求文件非空；空 JSON、空 SHP/TIF 即使路径存在也不会通过验证、被选择或登记。
- Workflow 预测资产选择与成果报告候选筛选同样拒绝零字节文件，避免空资产被后续步骤复用。
- E1/M5 引擎适配器在登记返回空值或抛出异常时统一返回失败，并使用脱敏错误摘要，不再把“验证通过但未登记”报告成成功。

**预计涉及文件：**

- 新增：`TF-agent/execution_request.py`
- 新增或扩展：`TF-agent/index_agent_loop.py`
- 修改：`TF-agent/app.py`
- 修改：`TF-agent/agent_command_bridge.py`
- 修改：`TF-agent/inference_agent_loop.py`
- 测试：新增 `tests/unit/test_execution_request_contract.py`

**验收标准：**

- [x] 同一种任务的入口映射已集中定义；指数法和深度学习均先计划、确认后进入可信适配器。
- [x] 侧栏 AutoTune 入口已纳入同一计划/确认门闩；确认后附加 `execution_request_v1`，后台账本保留完整入口契约。
- [~] 手动 UI 与 Agent 的确认契约已统一，取消/停止/后置登记仍需真实端到端验收。
- [x] 旧资产注册表保持可读，已有任务不需要重新生成。
- [~] 旧同步函数仍保留用于显式 `legacy_dl` 历史资产兼容；正常 `dl/index/m4` 请求不会静默回退，最终删除仍待兼容资产清点。

**阶段验证记录（尚未完成深度学习旧路径删除与完整端到端验收）：**

- `tests/unit/test_execution_request_contract.py`：9 passed；旧同步入口仅接受显式 `legacy_dl` 模式，未知模式不会回退执行；AutoTune pending 也携带统一入口契约；相同计划和参数在 Streamlit rerun 中保留 request_id，参数变更会生成新身份；兼容框架无生产模块导入者。
- `tests/unit/test_index_agent_loop.py`：2 passed；指数法缺输入会在执行前返回 `BLOCKED`，有效结果必须通过非空文件校验。
- Agent pending 与 UI 启动任务均可生成 `execution_request_v1`，并携带统一入口映射；旧 `run_pipeline` 请求会在后台启动前补齐可信计划。
- `tests/unit/test_postflight_result_gates.py`：2 passed；M5/E1 校验失败时摘要不再声称“已验证”。
- 旧深度学习合成与 AutoTune 兼容收尾增加最终成果文件存在性/非空校验，缺少文件时不登记、不写入成功时间线。
- 推理资产登记在提交边界再次检查 Final TIF/SHP 存在且非空，即使上游验证字典过期或被伪造，也不会提交幽灵资产。
- GEE/M4 本地导出现在在适配器和旧兼容引擎两层检查非空 GeoTIFF，并按当前 `id_list + roi_name` 精确匹配文件名；云端筛选成功、本地文件缺失或旧目录文件冒充时均返回 `FAILED`，不再等到后置登记阶段才暴露。
- 历史 `run_m4` pending 仍保持兼容字段，但后台 worker 会先转换为 `gee_download_plan_v1`，再进入统一 GEE 执行、校验和登记适配器，不再直接调用无登记门闩的同步路径。
- 取消收尾增加终态门闩：用户中断优先于后台线程迟到的成功信号，不登记/加载成果，也不写入成功时间线。
- 报告后置登记增加非空文件门闩；即使路径存在但文件为空，也不会写入资产注册表。
- 独立 M5/E1 worker 现在把资产登记返回值纳入最终成功条件；登记抛错、返回空值或报告文件为空时，verification、JobStore 成功状态与用户摘要均改为失败/未完全通过，不再出现“报告验证通过但未登记仍返回成功”。
- 兼容深度学习/指数法主流程中的可选 M5/E1 后置阶段也复用输出校验与登记门闩；主流程可继续完成，但时间线只会记录真实的 `SUCCEEDED` 或 `WARNING`，不再因“存在报告”而无条件写入“校验通过”。
- 兼容主流程的可选后置校验失败会把 JobStore 终态标为 `WARNING`，同时保留主成果成功事实；警告摘要明确指出相关 M5/E1 成果未登记或未加载。
- `execute_local_inference()` 对后处理适配器的“空成功” fail-closed：Final TIF/SHP 缺失或为空时直接返回失败；回归覆盖 `test_12b_postprocess_true_without_artifacts_returns_failure`，避免无成果执行被误报为成功。

**建议提交：** `refactor(agent): converge task execution on trusted loops`

---

## [~] AGENT-008：抽象 LLM 后端并移除导入期硬失败（当前会话）

**优先级：** P1，保证无 API Key 时手动系统仍可启动，并明确本地模型能力边界。当前异常发生在聊天时的延迟导入，手动工作台仍可启动，因此低于错误执行和公网未授权风险；但发布前必须解决。

**问题证据：**

- 审计基线时 `agent.py` 在导入阶段检测不到 DashScope Key 就抛异常；当前已改为延迟构造后端并返回可操作状态。
- `api_server.py` 与 `train_agent.py` 是独立实验入口，没有接入主 Agent。
- 本地模型 API 目前只生成文本，尚未证明支持 LangGraph 所需的工具调用和视觉输入。

**解决步骤：**

- [x] 新增 `LLMBackendConfig` 和 `build_chat_model()`，配置来源统一为环境变量和显式参数。
- [x] Agent 模块导入不再要求 Key；首次聊天时返回可操作的“后端未配置”状态，手动推理和地图功能继续可用。
- [x] 定义 `text`、`tools`、`vision` 能力矩阵；工具型 Agent 只允许声明了 tools 的后端。
- [x] 支持 DashScope remote 和 OpenAI-compatible local 配置；本地无 tools 时进入纯问答路径，不会执行系统命令；图片输入仍需 vision 能力。
- [x] `api_server.py` 的模型路径、dtype、device、host 和端口已配置化，移除固定 RTX 5080 文案和硬编码目录。
- [x] `train_agent.py` 已改为无导入副作用 CLI，模型/数据/输出/训练参数可配置，并校验训练数据 schema。
- [x] 增加后端选择、缺 Key、能力不匹配和本地 API fake client 测试。

**预计涉及文件：**

- 新增：`TF-agent/llm_backend.py`
- 修改：`TF-agent/agent.py`
- 修改：`TF-agent/api_server.py`
- 修改：`TF-agent/train_agent.py`
- 修改：`TF-agent/app.py`
- 修改：`TF-agent/.env.example`
- 测试：新增 `tests/unit/test_llm_backend.py`

**验收标准：**

- [x] 无 DashScope Key 时 Agent 模块可导入，手动工作台不受影响。
- [x] 后端不可用时返回明确状态，不产生导入期长堆栈或循环重试。
- [x] 本地后端未声明 tools 时不会创建工具型 Agent，不执行系统命令。
- [x] 后端配置切换不改变 bridge、确认门闩和执行层接口。

**阶段验证记录（本地真实模型服务和视觉能力验收待 AGENT-012）：**

- `tests/unit/test_llm_backend.py tests/unit/test_train_agent_cli.py`：5 passed。
- `agent.py` 无 Key 时不再在导入阶段抛异常；训练脚本导入不加载模型、不启动训练。
- `chat_with_vlm()` 在无 tools 的 local 后端下调用纯文本模型，不返回系统命令执行能力；无 vision 声明时会拒绝图片上传。

**建议提交：** `refactor(agent): make llm backends configurable and import-safe`

---

## [~] AGENT-009：增加受控会话持久化与上下文预算（当前会话）

**优先级：** P1，解决刷新丢失和历史无限增长问题。

**问题证据：**

- 对话历史只存在 `st.session_state.messages`。
- LangGraph Agent 没有 checkpointer；每轮把历史重新拼入请求。
- 历史没有消息数、token 或图片大小预算，长会话可能导致成本、延迟和上下文溢出。

**解决步骤：**

- [x] 定义会话存储 schema：thread_id、role、content、created_at、附件引用和命令 ID；系统命令原文会被替换为不可重放占位符。
- [x] 使用本地 SQLite 保存消息，默认不保存图片内容，只保存受控 basename 引用。
- [x] 数据库使用 0600、WAL、busy timeout、schema version 和事务锁，避免并发写静默丢失。
- [x] 默认保留 30 天或最多 100 个会话，并在每轮初始化时清理过期记录。
- [x] 增加上下文预算器：保留最近消息并脱敏/裁剪超长上下文。
- [x] 页面刷新按 thread_id 恢复消息，历史系统命令不会重新入队执行。
- [x] 存储 API 已支持删除会话；UI 已提供新建/清空会话按钮并在当前 thread 上执行删除/切换。
- [x] 添加恢复、裁剪、附件脱敏和命令不重放测试。
- [x] `command_id` 只允许安全标识；异常值仅持久化不可逆短摘要，避免命令文本或凭据绕过正文脱敏写入 SQLite。

**预计涉及文件：**

- 新增：`TF-agent/conversation_store.py`
- 新增：`TF-agent/context_budget.py`
- 修改：`TF-agent/agent.py`
- 修改：`TF-agent/app.py`
- 测试：新增 `tests/unit/test_conversation_store.py`
- 测试：新增 `tests/unit/test_context_budget.py`

**验收标准：**

- [x] 刷新页面后对话可恢复，历史命令不会再次入队。
- [x] 超长会话请求大小受预算限制，并保留最近消息。
- [x] 删除会话后本地数据库中不再保留消息或附件引用。
- [x] 首次建库、并发写、保留期清理、新建/清空会话、v1→v2 版本迁移和损坏数据库保留恢复均有测试。
- [x] 持久化记录不包含密钥、绝对路径、图片 base64 或未经授权的精确 AOI。

**阶段验证记录：**

- `tests/unit/test_conversation_store.py tests/unit/test_context_budget.py`：13 passed；覆盖并发追加、旧 schema 只补列不丢历史、消息凭据/command_id 和精确空间字段脱敏，损坏库会保留为 `.corrupt-*` 后重建。
- Streamlit 已接入 thread_id 恢复、快照保存与有界上下文注入；数据库路径可由 `CSTF_CONVERSATION_DB_PATH` 配置。
- 聊天区已提供“新会话/清空会话”操作，清空会删除当前 thread 的消息和附件引用，不触发历史命令重放。

**建议提交：** `feat(agent): persist conversations with bounded context`

---

## [~] AGENT-010：补齐知识库入库、版本和健壮性闭环（当前会话）

**优先级：** P1，当前知识库只有查询入口，无法从仓库内可靠构建和维护。

**问题证据：**

- `agent.py` 使用 `get_or_create_collection()` 和 query，但仓库内没有文献入库或更新工具。
- 默认知识库路径与能力面板使用的路径曾出现不一致。
- 检索结果直接读取 `meta['source']`，缺少 source 的记录可能导致异常。
- BGE 首次使用可能临时下载模型，离线演示不可预测。

**解决步骤：**

- [x] 规定知识文档输入格式：稳定 document_id、source、title、published_at、checksum 和正文。
- [x] 新增幂等入库 CLI，支持新增、更新、删除失效记录和 dry-run；相同 checksum 不重复向量化。
- [x] 文档显式 checksum 必须与正文 SHA-256 匹配，拒绝以错误 checksum 静默跳过内容更新。
- [x] 知识库路径统一通过 `knowledge_store.knowledge_db_path()` 解析，能力检查、Agent 查询和 CLI 使用同一函数。
- [x] 查询结果使用安全 metadata fallback，并对空库、损坏库、模型缺失和无结果返回 grounded 状态。
- [x] 知识库 CLI 对 Chroma/embedding 依赖缺失、模型初始化失败和入库异常返回安全可操作摘要，不输出原始堆栈或内部路径。
- [x] embedding 模型名已配置化；README 已补充离线预缓存、dry-run 和统一路径说明，真实模型下载仍需在有网环境单独验收。
- [x] 使用 fake collection 编写入库、更新、删除、dry-run 和缺 metadata 测试，不依赖公网模型。

**预计涉及文件：**

- 新增：`TF-agent/knowledge_store.py`
- 新增：`TF-agent/scripts/build_knowledge_base.py`（仓库根目录 `scripts/build_knowledge_base.py` 提供同等兼容入口）
- 修改：`TF-agent/agent.py`
- 修改：`TF-agent/capability_registry.py`
- 修改：`TF-agent/README.md`
- 测试：新增 `tests/unit/test_knowledge_store.py`、`tests/unit/test_knowledge_cli.py`

**验收标准：**

- [~] 新机器可按 CLI 从 JSONL 做 dry-run 并构建知识库；本机真实 Chroma/embedding 已验收，其他机器仍需按环境单独确认模型缓存与下载。
- [x] 数据更新或删除后检索结果与 manifest 一致。
- [x] 空库或 metadata 缺失时返回明确提示，不导致整轮 Agent 失败。
- [x] 回答引用只使用实际检索到的 source，缺失时显示“未知来源”。

**阶段验证记录（已补充本机真实 Chroma/embedding 烟测）：**

- `tests/unit/test_knowledge_store.py`、`tests/unit/test_knowledge_cli.py`：14 passed；正文 checksum 不匹配会在入库前拒绝；非法 JSON、非 object manifest/JSONL 行和非法记录结构都会先保留为 `.corrupt-*` 并拒绝增量写入，只有显式调用保存才会原子重建；重复 `document_id` 会在 JSONL 入库前拒绝，并验证 embedding 模型配置可注入、不依赖 POSIX 专属 `fchmod`、CLI 可显式固定模型和 collection。
- 新增 `knowledge_store.py` 与 `scripts/build_knowledge_base.py`；入库流程不依赖聊天首轮隐式写库，Agent 查询与 CLI 共用 embedding 模型配置。
- CLI 现在支持显式 `--embedding-model` 和 `--collection`，manifest 使用随机临时文件、`fsync`、`os.replace` 和 0600 权限，避免并发构建互相覆盖。
- README 与 `.env.example` 已记录 JSONL schema、`--dry-run`、`CHROMA_RS_DB_PATH`、`CSTF_KB_EMBEDDING_MODEL` 及离线模型缓存要求；dry-run 输出文档数和实际 embedding 配置，不加载模型。
- `tf-agent` 环境使用 `sentence-transformers/paraphrase-MiniLM-L3-v2`、临时 Chroma DB 和 `smoke_collection` 完成 1 条文档真实 embedding 入库；manifest 与 collection 均核验为 `smoke-001`/`count=1`。该证据不替代其他机器、GPU 或公网模型缓存验收。

**建议提交：** `feat(agent): add maintainable knowledge ingestion workflow`

---

## [~] AGENT-011：将后台线程状态升级为可恢复任务状态（当前会话）

**优先级：** P1，降低 Streamlit rerun、浏览器断开或进程退出造成的任务状态丢失。当前目标仍是单机内部工作台，进程中断不会被当作已验证成功，因此排在 P0 正确性/安全之后；在承诺无人值守或长任务可靠性前必须完成。

**问题证据：**

- 当前重型任务使用 `daemon=True` 线程和内存 `pipeline_shared`。
- 浏览器或 Streamlit 会话中断后，实时状态依赖下一次 rerun；进程退出时 daemon 线程可被直接终止。
- GEE/Workflow 有部分账本，但不同任务类型的恢复语义不统一。

**解决步骤：**

- [x] 定义统一持久化 JobRecord：job_id、task、kind、plan_id、status、progress、attempt、started_at、updated_at、artifact_ids 和安全错误摘要。
- [x] 每次 job 状态迁移先原子写 SQLite 账本；进程启动时扫描非终态任务并执行 reconcile，保留损坏账本副本。
- [x] GEE 任务根据远端 task_id 恢复轮询，同一 plan 不重复提交；未知/失败状态不会伪造成功。
- [x] 已固定本地恢复分类：完整验证的 Final 直接复用，完整 mask 且 Final 缺失时续跑后处理，部分/无效输出分类为待隔离重试，无可信检查点标记 `INTERRUPTED_WAIT_CONFIRMATION`；新增隔离函数，必须显式确认后才移动到审计保留目录。
- [x] 为 stop、进程退出和异常增加明确终态（`CANCELLED`/`INTERRUPTED`/`FAILED`），不把未知状态当成功。
- [x] 时间线事件的 message/error 与会话账本使用同一类路径、凭据脱敏，避免错误摘要写入持久化账本。
- [x] 时间线账本支持 `CSTF_TIMELINE_LEDGER_PATH` 覆盖，浏览器验收与多实例运行可隔离历史记录；默认路径保持兼容。
- [x] 第一阶段保留本地线程执行器，只实现可靠账本和恢复；暂不引入 Celery/Redis。
- [~] 已添加崩溃前状态、重复启动、账本损坏、不可恢复、GEE task_id 恢复和本地检查点分类测试；真实远端资产续跑仍待受控链路验收。

**预计涉及文件：**

- 新增：`TF-agent/job_store.py`
- 修改：`TF-agent/app.py`
- 修改：`TF-agent/task_timeline.py`
- 修改：`TF-agent/gee_agent_loop.py`
- 修改：`TF-agent/inference_agent_loop.py`
- 修改：`TF-agent/workflow_orchestrator.py`
- 测试：新增 `tests/unit/test_job_recovery.py`

**验收标准：**

- [x] 页面刷新不会丢失任务账本、终态和最近一次进度；监控区会周期性镜像进度到 JobStore，进程重启后展示最后进度并明确标记 `INTERRUPTED`，不会自动重跑。
- [x] 进程重启后，未完成任务明确标记 `INTERRUPTED`，不会伪造成功或自动重复执行。
- [x] 同一 job_id 只允许一个原子 claim；plan_id 由执行请求关联并记录。
- [x] 账本损坏时保留原文件并创建新账本，同时返回可操作的恢复状态。

**阶段验证记录（GEE 远端恢复与完整 UI 进度回放待补充）：**

- `tests/unit/test_job_recovery.py` 新增真实子进程突然退出后的重开/`reconcile` 回归；覆盖跨进程原子 claim、活动 `plan_id` 与 task/kind 身份一致性、job_id 身份冲突拒绝、终态显式重跑、损坏主库/JSON 行与 WAL/SHM 证据保留、同秒备份不覆盖、执行请求审计摘要恢复及精确空间元数据脱敏。JobStore 的 claim/plan 去重已用 SQLite `BEGIN IMMEDIATE` 串行化，避免并发双成功；`tests/unit/test_gee_agent_loop.py` 新增 M4/GEE 本地空导出 fail-closed 回归，覆盖适配器返回空文件和旧兼容引擎导出后文件缺失两条边界；`tests/unit/test_inference_agent_loop.py`：45 passed，覆盖远端 task 恢复、本地检查点分类、后处理空成功 fail-closed、需确认的隔离动作和 GEE 账本损坏/错误摘要边界。
- `tests/unit` 单元测试：`592 passed, 2 warnings`；仓库全量 `pytest -q`：`598 passed, 3 skipped`；核心命令（含 AppTest 与 API/Gateway 认证）：`204 passed`；应用启动烟测：`1 passed`；Python 3.11 最小跨平台契约（上下文、执行请求、JobStore）：`43 passed`。
- `TF-agent/app.py` 已在任务入队、原子 claim、完成/失败/取消收尾处接入 JobStore；默认账本为 `TF-agent/data/jobs.sqlite3`，可由 `CSTF_JOB_DB_PATH` 覆盖。
- JobStore 的错误摘要与审计 metadata 现在也复用空间字段脱敏，账本不会保存精确 `bounds`、`crs` 或 `resolution`。
- AppTest 已覆盖进程重启后的时间线回放、报告计划生成、独立确认门闩和取消计划；报告不会因历史 `SUCCEEDED` 事件自动生成。

**建议提交：** `feat(agent): persist and reconcile background jobs`

---

## [~] AGENT-012：建立真实外部链路、浏览器验收与文档证据更新流程（当前会话）

**优先级：** P2，作为所有整改任务的最终交付门槛。

**问题证据：**

- 当前单元测试大量使用 fake engine、mock EE 和 override executor。
- Workflow acceptance 的 GEE 阶段复用本地沙盒影像，不证明真实 GEE 下载。
- 仓库没有自动化浏览器测试证明 Streamlit、地图 iframe、确认按钮和结果回写完整可用。
- 历史进展文档包含已过时的测试数量、Windows 路径和 PDF 状态。

**解决步骤：**

- [x] 将验收分为离线必跑与有凭据手动/受控运行两组；默认 CI 只跑离线组。
- [x] 外部验收必须显式设置 `RUN_EXTERNAL_ACCEPTANCE=1`，并按 DashScope、GEE、GPU、Browser 分开开关；凭据或硬件缺失记为带原因的 `SKIPPED`，不得记为通过。
- [x] 已增加并运行真实 DashScope 受限纯问答冒烟（1 次、32 tokens）；当前报告只保留响应摘要校验和，工具调用、图片授权、超时和限流场景仍待真实凭据验收。
- [!] 已增加真实 GEE project/集合查询探针并固定 25 km²/31 天/3 景预算；当前凭据调用历史 project `ctfseg-481406` 被拒绝（缺少 `serviceusage.services.use`），GeoTIFF 下载、CRS 和资产登记仍待具备权限的 project。
- [~] 已增加真实权重存在性、校验摘要和设备探针；小样本推理与 Final TIF/SHP 验证仍待真实权重环境。
- [x] 新增 Streamlit 原生 AppTest 离线交互验收：根页面、会话新建/清空、原始命令默认关闭，以及深度学习/指数法按钮先生成计划再显示确认入口；报告入口已改为先生成计划、确认后执行；中断普通推理任务只提供重新生成计划按钮，不自动重跑。
- [x] 已增加并运行显式 opt-in Playwright 根页面、会话控制、侧栏深度学习/指数法切换、两类提取计划阻断门闩、历史时间线回放、报告计划确认/取消、Cesium AOI 工具栏、当前视图 AOI 发送/清除、真实 canvas 矩形拖拽和三点右键闭合多边形操作、CSTF_FLY 本地地图定位，以及“认证 Gateway → 本地真实 Streamlit 上游”测试，使用隔离临时目录和随机端口；报告按钮现在也写入计划/确认/校验/登记时间线，公网地图跳转和执行结果状态等完整交互覆盖仍待扩展。
- [x] 固定外部测试预算：GEE 不超过 25 km²/31 天/3 景；DashScope 不超过 5 次/512 tokens/2 MB；GPU 只运行一个 fixture。超过上限不自动扩容或重试消费。
- [x] 外部测试输出写入独立临时根目录并由 `TemporaryDirectory` 清理；仅保留脱敏 manifest、摘要校验和、耗时、预算和失败证据。
- [x] 验收矩阵生成机器可读 JSON 摘要；文档引用当前本地离线结果，真实外部结果仅在显式运行后登记。
- [x] README 与验收 README 已更新当前离线测试、AppTest、受限 DashScope 和 Playwright 命令；[~] GEE/GPU 真实链路、远程 ngrok/Streamlit 和完整浏览器交互证据仍待补充。

**预计涉及文件：**

- 新增：`tests/smoke/` 下的受控外部服务测试
- 新增：`tests/smoke/test_streamlit_apptest.py`（无 Chromium 的离线交互验收）
- 新增：`tests/acceptance/run_acceptance_matrix.py`
- 新增：`tests/acceptance/README.md`
- 新增：`tests/unit/test_acceptance_matrix.py`
- 新增：`tests/browser/test_streamlit_ui.py`（显式 Playwright opt-in）
- 新增：`TF-agent/requirements-browser.txt`（可选 Playwright/Chromium 运行时）
- 新增：`tests/browser/` 下的浏览器验收
- 修改：`tests/acceptance/run_workflow_acceptance.py`
- 修改：`.github/workflows/ci.yml`
- 修改：`README.md`、`TF-agent/README.md`
- 更新：相关 `docs/dev/*_PROGRESS.md`，保留历史日期和证据来源

**验收标准：**

- [x] 离线验收矩阵可重复通过，且不需要任何真实密钥、GPU 或公网；CI 已加入 `--offline-only` 步骤。
- [x] DashScope 受限纯问答和浏览器根页面验收已产生可追溯报告；[!] GEE 探针因 project 权限失败，GPU/真实权重推理与 Final TIF/SHP 验收仍待分别运行。
- [x] 验收报告记录 opt-in 开关、资源上限、实际消耗摘要、输出隔离和清理结果。
- [x] 离线 AppTest 已覆盖根页面、会话控制、历史时间线回放和报告确认门闩；[x] Playwright 已验证根页面、智能分析助手、会话按钮、输入框、侧栏深度学习/指数法切换、提取计划阻断门闩、历史任务报告计划确认/取消、Cesium AOI 工具栏、当前视图 AOI 发送/清除、矩形鼠标拖拽、三点多边形闭合和本地 CSTF_FLY 定位，以及认证 Gateway 代理到本地真实 Streamlit 上游的页面/健康接口，远程 ngrok 和公网地图链路仍待扩展。
- [~] 离线测试数量与当前证据一致；GEE/GPU、完整浏览器交互和远程 CI 证据待补充。

**阶段验证记录（真实外部服务与浏览器验收待补充）：**

- `RUN_EXTERNAL_ACCEPTANCE=1 RUN_DASHSCOPE_ACCEPTANCE=1 RUN_BROWSER_ACCEPTANCE=1 python tests/acceptance/run_acceptance_matrix.py`：离线核心 `197 passed`、DashScope `PASS`（1 request/32 tokens）、Browser `3 passed`，报告状态 `PASS`；GEE/GPU 因未启用对应开关保持 `SKIPPED`；机器可读报告写入 `tests/acceptance/_out/acceptance_matrix.json`。
- 本轮默认验收矩阵已复跑：离线核心 `204 passed`，DashScope/GEE/GPU/Browser 均按未显式 opt-in 记为 `SKIPPED`；报告已更新至 `tests/acceptance/_out/acceptance_matrix.json`。
- 本轮新增 GEE/M4 本地导出 fail-closed 回归：即使底层适配器返回云端筛选成功，只要本地 GeoTIFF 缺失、为空或仅存在上一计划的旧文件，执行结果为 `FAILED`，不会进入资产登记或成功回复。
- Python 3.11 同一离线矩阵在新增本轮回归测试前也为 `PASS`（`164 passed`）。这项历史本地解释器证据不替代当前 Python 3.10 结果或 GitHub Ubuntu/Windows runner 证据。
- `uvicorn api_server:app` 在空 `CSTF_LOCAL_API_TOKEN` 下由 ASGI lifespan 拒绝启动（退出码 3），证明非直接 CLI 启动路径同样 fail closed。
- 使用临时 token 启动同一 ASGI app 后，未认证 POST 请求返回 `401 authentication required`；服务已正常监听并在验证后停止。
- 默认矩阵运行：离线 `PASS`；DashScope/GEE/GPU/Browser 均因未显式 opt-in 为 `SKIPPED`，没有把缺少凭据记为通过。
- 历史受限外部矩阵曾记录 DashScope `PASS`（1 request、32 tokens）；本轮显式启用 DashScope/Browser 的完整矩阵报告写入 `tests/acceptance/_out/acceptance_matrix.json`：离线核心 `PASS`（197 passed）、DashScope `PASS`（1 request/32 tokens）、GEE/GPU `SKIPPED`（未启用对应开关）、Browser `PASS`（3 passed，含 Gateway 登录/登出、未认证 WebSocket 边界和认证后本地 Streamlit 上游页面）。未保存 key 或完整响应；具备凭据的历史 GEE 探针如实为 `FAIL`（缺少 `serviceusage.services.use`）。
- 报告 schema：`cstf_acceptance_matrix_v1`；预算和临时输出清理字段已写入机器可读 JSON。当前 `tests/unit` 为 `592 passed, 2 warnings`，仓库全量 `pytest -q` 为 `598 passed, 3 skipped`。
- 默认浏览器测试：未设置外部验收开关时 `3 skipped`；显式 opt-in 后 `3 passed`，不会把未安装或未运行的浏览器记为通过。

**建议提交：** `test: add evidence-backed agent acceptance matrix`

---

## 每个任务完成时的记录模板

复制以下区块到对应任务末尾，完成前不得填写成功结论：

```markdown
**完成记录：**

- 负责人：
- 分支：`codex/<task-slug>`
- 提交：
- 目标测试命令：
- 目标测试结果：
- 全量测试命令：
- 全量测试结果：
- 浏览器/真实服务验收：不适用或证据路径
- 已知剩余限制：无，或逐项列出
```

## 总体验收清单

- [ ] AGENT-000 至 AGENT-006 全部完成后，才允许把远程演示描述为“具备基础安全边界”。
- [ ] AGENT-007 至 AGENT-011 全部完成后，才允许把 Agent 描述为“单一可信执行架构且可恢复”。
- [ ] AGENT-012 完成后，才允许在 README 中声明真实 GEE、真实模型和浏览器端到端链路已验证。
- [ ] 所有完成声明均附当前提交、命令、结果和外部环境条件。
