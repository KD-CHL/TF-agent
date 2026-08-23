# AGENT-000 Test Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Each task uses checkbox (`- [ ]`) tracking and must complete its own test cycle before the next task begins.

**Goal:** Make the TF-agent test and startup baseline reproducible on Python 3.10/3.11, without requiring real DashScope, GEE, GPU, credentials, or developer-specific paths.

**Architecture:** Keep runtime dependencies in `TF-agent/requirements.txt` and add a small, separately installable test layer in `TF-agent/requirements-test.txt`. Run a core offline test set from the repository root while retaining the full `tests/unit` command for regression inventory, and run a process-level Streamlit smoke test that starts the real app on an ephemeral local port, requests the root page, then always terminates the child process.

**Tech Stack:** Python 3.10/3.11, Conda, pytest, pytest-cov, Streamlit, `urllib.request`, GitHub Actions.

**Spec:** `docs/dev/AGENT_TECH_DEBT_TASKS.md` — AGENT-000.

## Global Constraints

- No test may call real DashScope, GEE, GPU, public network services, or fixed developer paths.
- Python 3.10 and 3.11 are the supported test versions for this phase.
- Windows path syntax tests must use platform-independent assertions; macOS/Linux tests must not expect Windows separators.
- The smoke test must use a random free local port, a bounded startup timeout, and `finally` cleanup.
- Do not modify Agent execution semantics in this phase; changes are limited to test infrastructure, path-test expectations, CI, documentation, and test-only launch code.
- Do not print or load any API key as a test assertion; startup smoke must run without a key and only assert the rendered root page.

---

### Task 1: Add pinned test dependencies

**Files:**
- Create: `TF-agent/requirements-test.txt`
- Modify: `TF-agent/requirements.txt` to declare the existing PDF runtime dependency
- Modify: `README.md` (test setup section)
- Modify: `TF-agent/README.md` (environment section)

**Interfaces:**
- Produces the install command `python -m pip install -r requirements-test.txt` for a runtime environment that already installed `requirements.txt`.

- [ ] **Step 1: Write the failing dependency check**

Add a shell-level verification command to the plan execution notes:

```bash
conda run -n tf-agent python -c 'import pytest; print(pytest.__version__)'
```

Expected before installation: the command fails with `ModuleNotFoundError` when the environment has only runtime dependencies.

- [ ] **Step 2: Run the check to verify the baseline is missing**

Run:

```bash
conda run -n tf-agent python -c 'import pytest; print(pytest.__version__)'
```

Expected: failure caused by the missing test runner, not by a repository import error.

- [ ] **Step 3: Add the minimal test dependency file**

Create `TF-agent/requirements-test.txt` with these exact constraints, and add `reportlab` to `TF-agent/requirements.txt` because the existing PDF engine and capability check require it:

```text
pytest==8.3.5
pytest-cov==6.0.0
```

Document both installation commands in the two README files:

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-test.txt
```

- [ ] **Step 4: Run the check to verify installation**

Run:

```bash
conda run -n tf-agent python -m pip install -r TF-agent/requirements-test.txt
conda run -n tf-agent python -c 'import pytest; print(pytest.__version__)'
conda run -n tf-agent python -m pip check
```

Expected: pytest imports, the installed version is `8.3.5`, and `pip check` reports no broken requirements.

- [ ] **Step 5: Commit**

```bash
git add TF-agent/requirements-test.txt README.md TF-agent/README.md
git commit -m "test: add reproducible agent test dependencies"
```

### Task 2: Make path assertions cross-platform

**Files:**
- Modify: `tests/unit/test_agent_commands.py:215-223`
- Modify: `tests/unit/test_p0_hardening.py` only if the same separator-specific assertion is found during the test run
- Modify: `TF-agent/agent_command_bridge.py:464-488` only when the focused regression test proves a mapped filesystem field is skipped by normalization

**Interfaces:**
- Consumes the existing `apply_agent_reply_immediate()` path normalization behavior.
- Produces assertions that compare normalized semantic paths and do not force a Windows separator on macOS/Linux.

- [ ] **Step 1: Write the failing test**

Replace the platform-specific expected value in the existing test with an explicit semantic expectation:

```python
def test_immediate_apply_for_unit_tests(self):
    state = _base_state()
    reply = (
        "[SYSTEM_COMMAND_JSON]\n"
        + json.dumps({"sidebar_states": {"root_dir": "E:/Data/test"}}, ensure_ascii=False)
        + "\n[/SYSTEM_COMMAND_JSON]"
    )
    apply_agent_reply_immediate(state, reply)
    self.assertEqual(state["ui_root_dir"], os.path.normpath("E:/Data/test"))
```

Add a separate pure Windows syntax assertion where the test needs Windows semantics:

```python
import ntpath


def test_windows_path_fixture_uses_windows_semantics(self):
    self.assertEqual(ntpath.normpath(r"E:/Data/test"), r"E:\\Data\\test")
```

The first assertion describes the host filesystem behavior; the second describes Windows syntax without depending on the host OS.

- [ ] **Step 2: Run the focused test to verify the original failure**

Run before changing the test:

```bash
conda run -n tf-agent python -m pytest tests/unit/test_agent_commands.py::TestStreamlitDeferQueue::test_immediate_apply_for_unit_tests -q
```

Expected before the fix: the existing assertion fails on macOS because it requires a Windows backslash path.

- [ ] **Step 3: Implement the minimal path normalization correction**

Use `os.path.normpath()` for paths normalized by the running platform, and `ntpath.normpath()` only for explicitly Windows-formatted fixtures. If the related hardening test shows that `mask_root` is not normalized despite being a mapped filesystem path, include the `_root` suffix in the existing path-field classification; do not refactor the bridge or change URL/name handling.

- [ ] **Step 4: Run focused and related tests**

Run:

```bash
conda run -n tf-agent python -m pytest tests/unit/test_agent_commands.py tests/unit/test_p0_hardening.py -q --tb=short -p no:cacheprovider
```

Expected: all tests in both files pass on macOS; the assertions do not contain host-specific separator assumptions.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_agent_commands.py tests/unit/test_p0_hardening.py
git commit -m "test: make agent path assertions cross-platform"
```

### Task 3: Add the process-level Streamlit startup smoke test

**Files:**
- Create: `tests/smoke/__init__.py`
- Create: `tests/smoke/test_app_boot.py`

**Interfaces:**
- Produces one pytest test named `test_streamlit_root_page_starts_without_external_credentials`.
- Starts `TF-agent/app.py` using the current test interpreter, requests `http://127.0.0.1:<free-port>/`, and terminates the process in `finally`.

- [ ] **Step 1: Write the failing test**

Create a test with this behavior:

```python
def test_streamlit_root_page_starts_without_external_credentials():
    # allocate a free loopback port, start `python -m streamlit run TF-agent/app.py`,
    # poll the root URL for at most 30 seconds, assert HTTP 200 and `<title>Streamlit`,
    # then terminate the child process in a finally block.
```

The implementation must remove `DASHSCOPE_API_KEY`, `QWEN_API_KEY`, `EE_PROJECT`, and proxy variables from the child environment so the test proves local startup does not make external calls. It must capture stdout/stderr to a temporary file and include the last 80 lines in a failure message.

- [ ] **Step 2: Run the smoke test to verify the missing test file**

Run:

```bash
conda run -n tf-agent python -m pytest tests/smoke/test_app_boot.py -q --tb=short
```

Expected before implementation: pytest reports that the smoke test file does not exist.

- [ ] **Step 3: Implement the minimal process harness**

Use only the standard library (`socket`, `subprocess`, `tempfile`, `time`, `urllib.request`, `os`, `signal`, `sys`, `pathlib`). Use `socket.bind(("127.0.0.1", 0))` to allocate a free port, pass `--server.headless true`, `--server.address 127.0.0.1`, and `--server.port <port>`, and treat HTTP 200 as the only success condition. Terminate gracefully, then kill if the child remains alive.

- [ ] **Step 4: Run smoke and core unit tests**

Run:

```bash
conda run -n tf-agent python -m pytest tests/smoke/test_app_boot.py -q --tb=short
conda run -n tf-agent python -m pytest \
  tests/unit/test_agent_commands.py \
  tests/unit/test_p0_hardening.py \
  tests/unit/test_workflow_orchestrator.py \
  tests/unit/test_agent_task_timeline.py \
  -q --tb=short -p no:cacheprovider
```

Expected: the smoke test returns one pass, and the core unit set reports 121 passing tests without contacting external services. The full `tests/unit` command remains a separate regression inventory until AGENT-001/002 and the other listed P0 gaps are fixed.

- [ ] **Step 5: Commit**

```bash
git add tests/smoke/__init__.py tests/smoke/test_app_boot.py
git commit -m "test: add local Streamlit startup smoke test"
```

### Task 4: Make CI test the supported Python matrix

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces a GitHub Actions `unit-tests` matrix for Python 3.10 and 3.11.
- Keeps the existing CPU Torch and GIS installation strategy, while installing `TF-agent/requirements-test.txt` and running unit plus smoke tests from the repository root.

- [ ] **Step 1: Write the failing CI configuration expectation**

Add a repository-local text assertion in the review checklist that the workflow contains both versions:

```bash
rg -n 'python-version: "3\.10"|python-version: "3\.11"' .github/workflows/ci.yml
```

Expected before the edit: only Python 3.11 is present.

- [ ] **Step 2: Run the expectation check**

Run the command above and verify it finds only the existing 3.11 entry.

- [ ] **Step 3: Implement the matrix and test commands**

Add:

```yaml
strategy:
  fail-fast: false
  matrix:
    python-version: ["3.10", "3.11"]
```

Use `${{ matrix.python-version }}` in `actions/setup-python`, include `TF-agent/requirements-test.txt` in the cache dependency paths, install test requirements, and run the core offline set:

```bash
python -m pytest \
  ../tests/smoke/test_app_boot.py \
  ../tests/unit/test_agent_commands.py \
  ../tests/unit/test_p0_hardening.py \
  ../tests/unit/test_workflow_orchestrator.py \
  ../tests/unit/test_agent_task_timeline.py \
  -q --tb=short -p no:cacheprovider
```

Keep external services and credentials out of CI. Preserve failure artifacts for both smoke and unit test output.

- [ ] **Step 4: Run local YAML and test checks**

Run:

```bash
conda run -n tf-agent python -m pytest tests/smoke tests/unit -q --tb=short -p no:cacheprovider
git diff --check
```

Expected: local tests pass or report an explicitly reproducible dependency/code failure; the workflow diff has no whitespace errors.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: test agent on Python 3.10 and 3.11"
```

### Task 5: Update the baseline completion evidence

**Files:**
- Modify: `docs/dev/AGENT_TECH_DEBT_TASKS.md` only after all previous tasks pass
- Modify: `README.md` and `TF-agent/README.md` if command or version wording still disagrees with the tested commands

**Interfaces:**
- Produces a completion record containing the exact environment, commands, pass/fail counts, and known external limitations.

- [ ] **Step 1: Run the complete local verification**

Run:

```bash
conda run -n tf-agent python -m pip check
conda run -n tf-agent python -m pytest \
  tests/smoke/test_app_boot.py \
  tests/unit/test_agent_commands.py \
  tests/unit/test_p0_hardening.py \
  tests/unit/test_workflow_orchestrator.py \
  tests/unit/test_agent_task_timeline.py \
  -q --tb=short -p no:cacheprovider
conda run -n tf-agent python -m pytest tests/unit -q --tb=short -p no:cacheprovider
git diff --check
git status --short --branch
```

- [ ] **Step 2: Record actual evidence**

Record the actual commands and outputs in the AGENT-000 completion record. If a test is blocked by missing credentials, GPU, or external data, record it as a blocker or skip with the exact reason; do not mark it passed.

- [ ] **Step 3: Commit the evidence update**

```bash
git add docs/dev/AGENT_TECH_DEBT_TASKS.md README.md TF-agent/README.md
git commit -m "docs: record agent test baseline evidence"
```
