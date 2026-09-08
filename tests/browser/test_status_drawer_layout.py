"""Real-browser regression for progress clipped below the map viewport."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

import pytest


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "tests" / "fixtures" / "status_layout_app.py"
sys.path.insert(0, str(ROOT / "TF-agent"))
from task_timeline import TimelineStore  # noqa: E402


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _fully_visible(locator):
    if not locator.count():
        return False
    return locator.evaluate("""el => {
        const r = el.getBoundingClientRect();
        let top = 0, bottom = innerHeight, left = 0, right = innerWidth;
        for (let p = el.parentElement; p; p = p.parentElement) {
            const css = getComputedStyle(p), pr = p.getBoundingClientRect();
            if (/(auto|scroll|hidden|clip)/.test(css.overflowY)) {
                top = Math.max(top, pr.top); bottom = Math.min(bottom, pr.bottom);
            }
            if (/(auto|scroll|hidden|clip)/.test(css.overflowX)) {
                left = Math.max(left, pr.left); right = Math.min(right, pr.right);
            }
        }
        return r.width > 0 && r.height > 0 && r.top >= top - 1 &&
            r.bottom <= bottom + 1 && r.left >= left - 1 && r.right <= right + 1;
    }""")


def _wheel_to(page, locator, surface):
    """Reach content using genuine wheel events, never DOM scroll assignments."""
    for _ in range(35):
        if _fully_visible(locator):
            return
        box = surface.bounding_box()
        assert box is not None
        top = max(0, box["y"])
        bottom = min(page.viewport_size["height"] - 8, box["y"] + box["height"])
        target = locator.bounding_box()
        assert target is not None
        delta = 100 if target["y"] > (top + bottom) / 2 else -100
        page.mouse.move(box["x"] + box["width"] - 20, bottom - min(24, (bottom - top) / 2))
        page.mouse.wheel(0, delta)
        page.wait_for_timeout(80)
    pytest.fail(f"Content remains clipped after mouse-wheel scrolling: {locator.inner_text()[:100]}")


def _assert_drawer_in_viewport(drawer, viewport):
    bounds = drawer.bounding_box()
    assert bounds and bounds["height"] > 100
    assert bounds["y"] >= 0
    assert bounds["y"] + bounds["height"] <= viewport[1] + 1
    assert bounds["x"] + bounds["width"] <= viewport[0] + 1
    return bounds


@pytest.mark.external
@pytest.mark.parametrize("viewport", [(1440, 900), (1280, 720)])
def test_task_progress_remains_accessible_in_resizable_drawer(tmp_path, viewport):
    if os.environ.get("RUN_EXTERNAL_ACCEPTANCE") != "1" or os.environ.get("RUN_BROWSER_ACCEPTANCE") != "1":
        pytest.skip("browser acceptance is opt-in")
    pw = pytest.importorskip("playwright.sync_api")
    timeline_path = tmp_path / "timeline.json"
    timeline = TimelineStore(str(timeline_path))
    for index in range(12):
        timeline.add("layout-fixture", "VERIFY", f"抽屉验收记录 {index:02d}", status="SUCCEEDED")
    timeline.save()
    env = os.environ.copy()
    env.update({
        "CSTF_CONVERSATION_DB_PATH": str(tmp_path / "conversations.sqlite3"),
        "CSTF_JOB_DB_PATH": str(tmp_path / "jobs.sqlite3"),
        "CSTF_TIMELINE_LEDGER_PATH": str(timeline_path),
        "CSTF_CHAT_PREVIEW_DIR": str(tmp_path / "previews"),
        "NO_PROXY": "127.0.0.1,localhost,::1", "no_proxy": "127.0.0.1,localhost,::1",
    })
    for key in ("DASHSCOPE_API_KEY", "CSTF_LLM_API_KEY", "QWEN_API_KEY", "EE_PROJECT",
                "GOOGLE_CLOUD_PROJECT", "EARTHENGINE_PROJECT", "HTTP_PROXY", "HTTPS_PROXY",
                "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy", "CSTF_GLOBE_PUBLIC_URL"):
        env[key] = ""
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(APP), "--server.headless", "true",
         "--server.address", "127.0.0.1", "--server.port", str(port), "--server.fileWatcherType", "none"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        with pw.sync_playwright() as api:
            browser = api.chromium.launch(headless=True, args=["--no-proxy-server"])
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
            page.route("**/*", lambda route: route.continue_()
                       if urlparse(route.request.url).hostname in {"127.0.0.1", "localhost", None}
                       else route.abort())
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            deadline = time.monotonic() + 40
            while True:
                try:
                    page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=3000)
                    break
                except pw.Error:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.2)
            page.get_by_role("textbox", name="chat_input", exact=True).wait_for(timeout=45000)
            page.locator(".cstf-status-edge-handle").wait_for(timeout=15000)
            map_column = page.locator('[data-testid="stColumn"]').filter(
                has=page.locator(".cockpit-map-col"))
            progress = page.locator("summary").filter(has_text="任务进度（12）")
            _wheel_to(page, progress, map_column)
            progress.click()
            oldest = page.get_by_text("抽屉验收记录 00", exact=False)
            report = page.get_by_role("button", name="📄 生成成果报告", exact=True)
            _wheel_to(page, report, map_column)
            assert _fully_visible(oldest), "oldest event must be reachable with the report actions"
            drawer = page.locator(".st-key-map_status_drawer")
            assert drawer.count() == 1
            _assert_drawer_in_viewport(drawer, viewport)
            assert drawer.evaluate("el => el.scrollHeight > el.clientHeight")

            # The short terminal viewport scrolls its own 30 lines first. Once
            # it reaches the last line, the same wheel gesture scrolls the drawer.
            terminal = page.locator(".st-key-task_terminal_log")
            _wheel_to(page, terminal, drawer)
            log_box = terminal.bounding_box()
            assert log_box
            page.mouse.move(log_box["x"] + 30, log_box["y"] + log_box["height"] / 2)
            page.mouse.wheel(0, 10000)
            page.wait_for_timeout(200)
            assert terminal.evaluate("el => el.scrollTop > 0")
            outer_before = drawer.evaluate("el => el.scrollTop")
            page.mouse.wheel(0, 160)
            page.wait_for_timeout(200)
            assert drawer.evaluate("el => el.scrollTop") > outer_before

            diagnostics = drawer.locator("summary").filter(has_text="地图加载诊断")
            _wheel_to(page, diagnostics, drawer)
            before = _assert_drawer_in_viewport(drawer, viewport)
            diagnostics.click()
            _wheel_to(page, page.get_by_text(
                "诊断仅临时显示；持久化日志仅保留坐标存在性、范围结果与哈希。", exact=True), drawer)
            after = _assert_drawer_in_viewport(drawer, viewport)
            assert abs(after["height"] - before["height"]) <= 1
            _wheel_to(page, diagnostics, drawer)
            diagnostics.click()

            # Drag the real map boundary rather than assigning height styles.
            page.wait_for_timeout(750)  # settle the diagnostic expander rerender
            handle = page.locator(".cstf-status-edge-handle")
            hb = handle.bounding_box()
            assert hb
            before = _assert_drawer_in_viewport(drawer, viewport)
            x, y = hb["x"] + hb["width"] * 0.25, hb["y"] + hb["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            assert page.locator('.cstf-resize-capture').count() == 1
            page.mouse.move(x, y - 70, steps=8)
            page.mouse.up()
            page.wait_for_timeout(200)
            after = _assert_drawer_in_viewport(drawer, viewport)
            assert after["height"] > before["height"] + 30
            _wheel_to(page, report, drawer)
            assert _fully_visible(oldest)

            page.get_by_role("button", name="收起任务状态与系统日志", exact=True).click()
            pw.expect(drawer).to_have_count(0)
            page.get_by_role("button", name="展开任务状态与系统日志", exact=True).click()
            pw.expect(drawer).to_have_count(1)
            _assert_drawer_in_viewport(drawer, viewport)
            progress = drawer.locator("summary").filter(has_text="任务进度（12）")
            _wheel_to(page, progress, drawer)
            if not progress.evaluate("el => el.parentElement.open"):
                progress.click()
            _wheel_to(page, report, drawer)
            assert _fully_visible(oldest)
            assert page.locator('[data-testid="stException"]').count() == 0
            assert not [error for error in errors if "removeChild" in error or "NotFoundError" in error]
            browser.close()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
