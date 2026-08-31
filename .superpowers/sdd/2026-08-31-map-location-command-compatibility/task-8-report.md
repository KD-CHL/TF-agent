# Task 8 报告：跨平台地图定位验收矩阵与协议文档

## 实现范围

- 新增 `tests/acceptance/map_location_matrix.py`，固定且仅覆盖 brief 指定的四个输入：
  canonical `lat/lon/zoom` JSON、legacy `center` JSON、`center + bounds` JSON、
  `COMMAND_UPDATE_MAP|lat|lon|zoom` 管道文本。
- 每个输入均通过 `parse_system_command` 与 `apply_system_command`，断言 canonical
  中心、session pending camera 和 zoom；再通过 `map_protocol` 验证 READY → FLY → ACK
  浏览器信封链路。
- 第三例断言 `bounds` 从 `[[south, west], [north, east]]` 归一化为有序
  `{west, south, east, north}`，并验证矩形中心 `(38.9, 121.6)`。
- 更新 `docs/dev/MAP_CAPABILITY_AOI_DESIGN.md` 与 `tests/acceptance/README.md`：
  明确 canonical 字段、legacy 别名、bounds 顺序、READY/FLY/ACK 时序、诊断症状映射，
  并标注 `center + bounds` 仅为兼容输入。
- 未修改运行时实现，未恢复文本/视觉模型分离，矩阵不启动 Streamlit、不访问公网或读取密钥。

## TDD 证据

### RED

先创建矩阵入口和四个精确 `CASES`，以未实现的 runner 运行：

```text
/opt/homebrew/Caskroom/miniconda/base/envs/tf-agent/bin/python tests/acceptance/map_location_matrix.py
```

结果：按预期以 `NotImplementedError: map location matrix runner is not implemented`
退出（return code 1）。

### GREEN

补齐 parser/bridge/protocol 验收链后运行同一命令，结果：

```text
{"case_count": 4, "equivalent_center_groups": [[1, 4], [2, 3]], "status": "PASS", ...}
```

四例均报告 `CSTF_MAP_READY`、`CSTF_FLY`、`CSTF_FLY_ACK`；中心分别为
`(30.5, 120.8)`、`(38.9126, 121.6174)`、`(38.9126, 121.6174)`、
`(30.5, 120.8)`。

## 聚焦验证

```text
/opt/homebrew/Caskroom/miniconda/base/envs/tf-agent/bin/python -m pytest \
  tests/unit/test_map_command_adapter.py tests/unit/test_agent_commands.py \
  tests/unit/test_map_protocol.py tests/unit/test_map_command_protocol.py \
  -q --tb=short -p no:cacheprovider
```

结果：`86 passed in 1.73s`。

```text
/opt/homebrew/Caskroom/miniconda/base/envs/tf-agent/bin/python -m py_compile \
  tests/acceptance/map_location_matrix.py
git diff --check
```

结果：均通过（return code 0）。

## 完整相关 suite

按 brief 运行：

```text
/opt/homebrew/Caskroom/miniconda/base/envs/tf-agent/bin/python -m pytest \
  tests/unit tests/smoke tests/browser -q
```

结果：`728 passed, 4 skipped, 1 failed, 2 warnings in 18.06s`（return code 1）。

唯一失败：

```text
tests/unit/test_p0_hardening.py::TestApplyAgentReplyImmediate::test_invalid_json_does_not_crash
AssertionError: '' != '[SYSTEM_COMMAND_JSON]\\n{not valid json\\n[/SYSTEM_COMMAND_JSON]'
```

该失败来自既有 malformed JSON clean 文本预期与当前运行时行为不一致；本 Task 未改动
`agent_command_bridge.py` 或其它运行时实现。两个 warning 是依赖版本提示：
`google.api_core` 的 Python 3.10 支持提醒，以及 `bqscales/traitlets` 的弃用提醒。

## 版本控制

将以单一 commit 提交本 Task 的矩阵、协议文档、README 和本报告。
