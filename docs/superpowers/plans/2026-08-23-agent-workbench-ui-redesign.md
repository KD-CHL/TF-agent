# Agent Workbench UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Agent 对话从右侧堆叠面板重构为可收起、对话/历史分离且首屏可用的 Agent Dock。

**Architecture:** 在现有 Streamlit 单页结构中保留左侧任务栏和中央地图，把右侧 UI 改成一个状态驱动的 Dock。`agent_dock_view` 只决定渲染对话或历史，`agent_dock_collapsed` 决定列比例；ConversationStore 提供“第一条用户消息预览”以避免欢迎语污染历史标题。

**Tech Stack:** Streamlit、SQLite ConversationStore、现有 Cesium iframe、Python unittest/pytest、浏览器 DOM/截图验收。

**Spec:** `docs/superpowers/specs/2026-08-23-agent-workbench-ui-redesign.md`

## Global Constraints

- 不恢复影像外发和精确空间元数据授权复选框。
- 保持现有任务确认门闩、地图跳转协议和本地数据脱敏边界。
- 所有新增行为先写失败测试，再写最小实现。
- 不重写 Cesium 数据协议；只调整导航帮助的默认可见性和外围布局。

---

### Task 1: 会话预览语义

**Files:**
- Modify: `TF-agent/conversation_store.py:218-270`
- Test: `tests/unit/test_conversation_store.py`

**Interfaces:**
- Produces: `ConversationStore.list_threads()` rows with `preview` based on the latest meaningful user message, plus `include_empty=False` for the history view.

- [ ] **Step 1: Write the failing test**

```python
def test_list_threads_preview_prefers_user_message_over_greeting(self):
    store.create_thread("thread_preview")
    store.append_message("thread_preview", "assistant", "您好，我是智能分析助手")
    store.append_message("thread_preview", "user", "分析 2022 年潮滩变化")
    rows = store.list_threads()
    assert rows[0]["preview"] == "分析 2022 年潮滩变化"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n tf-agent python -m pytest tests/unit/test_conversation_store.py::TestConversationStore::test_list_threads_preview_prefers_user_message_over_greeting -q`

Expected: FAIL because the current query returns the latest assistant greeting.

- [ ] **Step 3: Write minimal implementation**

Change the SQL preview subquery to select the latest `role='user'` message and return an empty preview when no user message exists.

- [ ] **Step 4: Run test to verify it passes**

Run the same command; expected PASS.

- [ ] **Step 5: Refactor**

Keep the existing read-time redaction and limit behavior; do not add title persistence.

### Task 2: Agent Dock state and view separation

**Files:**
- Modify: `TF-agent/app.py:3740-4410`
- Test: `tests/unit/test_chat_ui_contract.py`

**Interfaces:**
- Consumes: `agent_chat_width_pct`, `ConversationStore.list_threads()`.
- Produces: `agent_dock_view` values `对话|历史` and `agent_dock_collapsed` boolean.

- [ ] **Step 1: Write failing source-contract tests**

Assert that the app source contains `agent_dock_view`, `agent_dock_collapsed`, `历史`, and a collapse/reopen control, while the full session list is not rendered in the default conversation branch.

- [ ] **Step 2: Run the targeted test and verify RED**

Run: `conda run -n tf-agent python -m pytest tests/unit/test_chat_ui_contract.py -q`

Expected: FAIL because the current UI always renders the session list and has no Dock state.

- [ ] **Step 3: Implement the smallest Dock change**

Add defaults, change the column ratio for collapsed mode, add a compact view selector, render history only in `历史`, and retain the composer in `对话`.

- [ ] **Step 4: Run the targeted tests and verify GREEN**

Run the same command; expected PASS.

- [ ] **Step 5: Refactor CSS**

Make the message container flex-grow, keep the composer at the bottom, and style the Dock selector/collapse control without adding another permanent panel.

### Task 3: Map overlay default state

**Files:**
- Modify: `TF-agent/globe_engine.py:640-660`
- Test: `tests/unit/test_globe_engine.py` (or the existing globe engine contract test file)

**Interfaces:**
- Produces: Cesium Viewer config with `navigationHelpButton: false` by default; the existing toolbar remains available.

- [ ] **Step 1: Write the failing test**

Assert that the generated viewer configuration disables the navigation help button.

- [ ] **Step 2: Run the test and verify RED**

Run the focused globe test; expected FAIL because the current config sets `navigationHelpButton: true`.

- [ ] **Step 3: Implement minimal change**

Set the default to `false` and leave camera, AOI, layer, and postMessage behavior unchanged.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the same test; expected PASS.

### Task 4: Browser acceptance and documentation

**Files:**
- Modify: `TF-agent/README.md`
- Test evidence: `docs/dev/ui-audit/`

- [ ] **Step 1: Run unit/full tests**

Run: `conda run -n tf-agent python -m pytest -q --tb=short -p no:cacheprovider`

- [ ] **Step 2: Verify browser states**

At `http://127.0.0.1:8501/`, verify default `对话` view, composer visible, switching to `历史`, session switching, Dock collapse/reopen, and no default Cesium help overlay.

- [ ] **Step 3: Capture a screenshot and inspect it**

Save the accepted screenshot under `docs/dev/ui-audit/` and inspect it before handoff.

- [ ] **Step 4: Update documentation**

Document the Dock views, collapse behavior, session preview rule, and the unchanged data-sending safety boundary.
