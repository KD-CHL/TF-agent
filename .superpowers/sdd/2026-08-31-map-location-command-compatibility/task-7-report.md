# Task 7 报告：本地优先确定性地名解析

## 实现范围

- 新增 `TF-agent/location_resolver.py`，仅使用 `map_protocol.CAMERA_PRESETS` 做本地解析。
- 精确命中或唯一包含匹配返回经纬度、label 和 `resolver_source=local_preset`。
- 未命中返回 `reason=unresolved`；多候选返回 `reason=ambiguous` 和候选列表；非法坐标/预设返回显式错误。
- 空地名配合显式坐标可返回 `resolver_source=provided_coordinates`，不执行地名推断。
- `agent.py` 保持 `change_map_view(location_name, lat, lon, zoom, ...)` 签名和显式坐标输出不变；本地命中仅补充稳定 label。
- `agent_command_bridge.py` 在 canonical schema 校验前消费兼容字段 `location_name`：无坐标时必须本地唯一解析；有完整 lat/lon 时删除非 canonical 字段并原样保留显式坐标。
- 未引入网络请求、外部地理编码、provider 自动选择或精确坐标外发逻辑；既有 targetOrigin/channel/command/confirmation、bounds/AOI、结果自适应和 2D fallback 未改动。

## TDD 证据

### RED

先创建 `tests/unit/test_location_resolver.py`，运行：

```text
conda run -n tf-agent python -m pytest tests/unit/test_location_resolver.py -q
```

结果：6 项失败，均为预期的 `ModuleNotFoundError: No module named 'location_resolver'`。

### GREEN

实现解析器和 bridge/agent 最小接入后，运行：

```text
conda run -n tf-agent python -m pytest tests/unit/test_location_resolver.py tests/unit/test_agent_commands.py -q --tb=short -p no:cacheprovider
```

结果：`53 passed`。

## 其他验证

```text
conda run -n tf-agent python -m py_compile TF-agent/location_resolver.py TF-agent/agent.py TF-agent/agent_command_bridge.py
```

通过。

```text
git diff --check
```

通过，无空白错误。

## 风险与边界

- 本地表当前由杭州湾、乐清湾、中国三个既有相机预设组成；未知地名不会回退到网络服务。
- 包含匹配仅用于发现唯一/歧义候选，不做模糊评分、转写或行政区推断。
- 带完整显式坐标的命令保留 direct lat/lon 语义，即使同时带未知 `location_name` 也不会把坐标归因给该地名；调用方可据此展示显式坐标结果。
- 全量测试、真实外部地图/GEE 链路未在本 Task 运行；本报告仅声明聚焦 resolver/agent commands、编译和差异检查结果。

## 复审 Minor 修复（M1）

复审发现 `location_name` 与 legacy `center` 同时出现时，地名预设可能覆盖显式中心点。已在 `_resolve_map_location_alias` 中将 `center` 键（list/object，先不判断有效性）视为显式输入：仅移除非 canonical 的 `location_name`，交给既有 adapter/schema 处理；因此完整 `lat/lon`、legacy `center` 和 bounds 的既有优先级与校验保持不变。

### 修复验证

先添加 list/object center 回归测试并运行 resolver 测试，修复前 3 项失败：list/object center 被杭州湾预设覆盖，且无效 center 未被拒绝。

修复后运行：

```text
conda run -n tf-agent python -m pytest tests/unit/test_location_resolver.py tests/unit/test_agent_commands.py tests/unit/test_map_command_adapter.py tests/unit/test_map_protocol.py tests/unit/test_map_camera_presets.py -q --tb=short -p no:cacheprovider
```

结果：`91 passed`。

```text
conda run -n tf-agent python -m py_compile TF-agent/location_resolver.py TF-agent/agent.py TF-agent/agent_command_bridge.py
git diff --check
```

均通过。复审修复未触及文本/视觉模型边界、canonical `change_map_view` 签名、targetOrigin/channel/command/confirmation、AOI 或 2D fallback。
