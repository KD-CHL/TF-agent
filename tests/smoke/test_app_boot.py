"""Verify that the real Streamlit shell starts without external credentials."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen


_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_PATH = _REPO_ROOT / "TF-agent" / "app.py"
_STARTUP_TIMEOUT_SECONDS = 30.0


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _tail(path: Path, lines: int = 80) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"unable to read child log: {exc}"
    return "\n".join(content.splitlines()[-lines:])


def test_streamlit_root_page_starts_without_external_credentials():
    port = _free_loopback_port()
    url = f"http://127.0.0.1:{port}/"
    child_env = os.environ.copy()
    # app.py loads TF-agent/.env with override=False; empty values here prevent
    # a developer's local credential from being used by this smoke process.
    for name in (
        "DASHSCOPE_API_KEY",
        "CSTF_LLM_API_KEY",
        "QWEN_API_KEY",
        "CSTF_LLM_BACKEND",
        "CSTF_LLM_MODEL",
        "CSTF_LLM_BASE_URL",
        "EE_PROJECT",
        "GOOGLE_CLOUD_PROJECT",
        "EARTHENGINE_PROJECT",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "CSTF_ALLOW_RAW_SYSTEM_COMMAND",
    ):
        child_env[name] = ""

    with tempfile.TemporaryDirectory(prefix="tf-agent-smoke-") as temp_dir:
        log_path = Path(temp_dir) / "streamlit.log"
        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "streamlit",
                    "run",
                    str(_APP_PATH),
                    "--server.headless",
                    "true",
                    "--server.address",
                    "127.0.0.1",
                    "--server.port",
                    str(port),
                    "--server.fileWatcherType",
                    "none",
                ],
                cwd=str(_REPO_ROOT),
                env=child_env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )

        deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
        body = ""
        last_error: Exception | None = None
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    with urlopen(url, timeout=1.0) as response:
                        body = response.read(64 * 1024).decode("utf-8", errors="replace")
                        if response.status == 200:
                            break
                except (URLError, OSError) as exc:
                    last_error = exc
                time.sleep(0.25)
            else:
                last_error = TimeoutError(
                    f"Streamlit did not become ready within {_STARTUP_TIMEOUT_SECONDS:.0f}s"
                )

            assert body, (
                f"Streamlit root page was not returned; last_error={last_error!r}\n"
                f"child log:\n{_tail(log_path)}"
            )
            assert "<title>Streamlit" in body, (
                f"unexpected root page content\nchild log:\n{_tail(log_path)}"
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
