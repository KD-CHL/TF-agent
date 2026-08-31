# Agent 验收矩阵

默认只运行不访问公网、不读取真实密钥的离线核心验收：

```bash
python tests/acceptance/run_acceptance_matrix.py --offline-only
```

地图定位跨平台矩阵是独立的离线验收入口，固定只覆盖四个协议输入（canonical
`lat/lon/zoom` JSON、legacy `center` JSON、`center + bounds` JSON、以及
`COMMAND_UPDATE_MAP|lat|lon|zoom` 文本）：

```bash
/opt/homebrew/Caskroom/miniconda/base/envs/tf-agent/bin/python \
  tests/acceptance/map_location_matrix.py
```

矩阵不启动 Streamlit、不访问公网或真实密钥。每个输入都会经过命令解析与
`apply_system_command`，再验证浏览器通信的 `CSTF_MAP_READY → CSTF_FLY →
CSTF_FLY_ACK` 信封；等价坐标必须得到相同 canonical 中心，带 bounds 的输入还
必须得到 `west/south/east/north` 有序的归一化矩形。`center + bounds` 仅为兼容
旧客户端的输入形式，不能作为新命令的输出格式。

离线核心矩阵包含 Streamlit 原生 AppTest、Gateway/本地 API 认证与请求体边界测试，可验证根页面、会话控制以及深度学习/指数法计划确认门闩；AutoTune 的统一执行契约由单元测试覆盖。它不需要 Chromium。Playwright 浏览器测试仍单独受 `RUN_BROWSER_ACCEPTANCE=1` 控制。

需要运行浏览器验收时，先安装可选依赖和 Chromium：

```bash
python -m pip install -r TF-agent/requirements-browser.txt
python -m playwright install chromium
```

完整矩阵仍然默认跳过外部服务。只有显式设置总开关和对应 provider 开关才会尝试外部调用：

```bash
RUN_EXTERNAL_ACCEPTANCE=1 RUN_DASHSCOPE_ACCEPTANCE=1 \
python tests/acceptance/run_acceptance_matrix.py
```

当前环境已用 `TF-agent/.env` 完成一次受限 DashScope 纯问答验收（1 次请求、32 tokens），并完成 Playwright 根页面/计划门闩、Gateway 登录/登出/WebSocket 边界及认证 Gateway 代理到本地真实 Streamlit 上游验收（3 passed）；报告只保存摘要校验和。重跑 DashScope 时可使用：

```bash
set -a; . TF-agent/.env; set +a
RUN_EXTERNAL_ACCEPTANCE=1 RUN_DASHSCOPE_ACCEPTANCE=1 \
  python tests/acceptance/run_acceptance_matrix.py
```

可用开关：`RUN_DASHSCOPE_ACCEPTANCE`、`RUN_GEE_ACCEPTANCE`、`RUN_GPU_ACCEPTANCE`、`RUN_BROWSER_ACCEPTANCE`。缺少凭据、权重、Playwright 或 Chromium 时结果为 `SKIPPED`，不会记为通过；失败为 `FAIL`。每次报告都会记录预算、opt-in 状态、摘要校验和及临时目录清理结果，不保存 API key、绝对路径或完整模型响应。

报告默认写入 `tests/acceptance/_out/acceptance_matrix.json`。CI 使用 `--offline-only`，外部验收在受控环境单独运行。
