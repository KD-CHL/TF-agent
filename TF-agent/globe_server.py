"""本地 HTTP 服务：Cesium 页面与瓦片代理。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

_SERVER_VERSION = 8

_lock = threading.Lock()
_html_by_key: dict[str, str] = {}
_tile_templates: dict[str, str] = {}
_server: Optional[ThreadingHTTPServer] = None
_server_port: Optional[int] = None
_server_version_started: int = 0
_probe_cache: dict[str, tuple[float, bool]] = {}
_PROBE_TTL_SEC = 30.0


def html_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "yyglobe_html"
    d.mkdir(parents=True, exist_ok=True)
    return d


def register_tile_template(asset_key: str, upstream_template: str) -> str:
    token = hashlib.md5(asset_key.encode("utf-8", errors="replace")).hexdigest()[:16]
    with _lock:
        _tile_templates[token] = upstream_template
    return token


def public_globe_base_url() -> Optional[str]:
    """
    远程试用时设置环境变量 CSTF_GLOBE_PUBLIC_URL，例如 ngrok 转发的地球服务地址：
      https://xxxx.ngrok-free.app
    未设置则使用本机 127.0.0.1（仅本机浏览器可加载三维地球 iframe）。
    """
    raw = (os.environ.get("CSTF_GLOBE_PUBLIC_URL") or "").strip().rstrip("/")
    return raw or None


def _probe_url_ok(url: str, timeout: float = 2.5) -> bool:
    key = url.rstrip("/")
    now = time.time()
    cached = _probe_cache.get(key)
    if cached and (now - cached[0]) < _PROBE_TTL_SEC:
        return cached[1]
    ok = False
    try:
        with urllib.request.urlopen(f"{key}/health", timeout=timeout) as resp:
            ok = resp.status == 200
    except Exception:
        ok = False
    _probe_cache[key] = (now, ok)
    return ok


def globe_service_base(port: int, *, validate_public: bool = True) -> str:
    """浏览器加载 iframe/瓦片时使用的地球服务根 URL。"""
    pub = public_globe_base_url()
    if pub and validate_public:
        if _probe_url_ok(pub):
            return pub
        # 公网地址失效（ngrok 未启动等）时回退本机，避免 iframe 指向死链
        return _local_globe_base(port)
    if pub:
        return pub
    return _local_globe_base(port)


def _local_globe_base(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def globe_public_url_warning(port: int) -> Optional[str]:
    """若配置了不可达的 CSTF_GLOBE_PUBLIC_URL，返回提示文案。"""
    pub = public_globe_base_url()
    if not pub:
        return None
    if _probe_url_ok(pub):
        return None
    return (
        f"环境变量 CSTF_GLOBE_PUBLIC_URL={pub} 当前不可达（ngrok/网关未启动？），"
        f"已自动改用本机 http://127.0.0.1:{port}。"
        "若从其他设备访问 Streamlit，三维地球会空白——请启动 ngrok 并重新设置该变量后重启 Streamlit。"
    )


def overlay_tile_url(port: int, token: str) -> str:
    base = globe_service_base(port)
    return f"{base}/overlay/{token}/{{z}}/{{x}}/{{y}}.png"


def _refresh_tile_upstream(token: str, template: str) -> None:
    with _lock:
        _tile_templates[token] = template


def _build_test_html() -> str:
    try:
        import os

        try:
            from dotenv import load_dotenv

            load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
        except ImportError:
            pass

        import globe_engine as ge

        payload = ge.build_globe_payload(
            center=(35.0, 105.0),
            zoom=3,
            ion_token=os.environ.get("CESIUM_ION_TOKEN"),
            show_borders=False,
        )
        return ge.build_cesium_html(payload, full_viewport=True)
    except Exception:
        return "<html><body>globe test failed to build</body></html>"


def _build_minimal_html() -> str:
    import os

    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    except ImportError:
        pass

    ver = "1.128"
    ion = (os.environ.get("CESIUM_ION_TOKEN") or "").strip()
    ion_line = f"Cesium.Ion.defaultAccessToken = {json.dumps(ion)};" if ion else ""
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/>
<link rel="stylesheet" href="https://cesium.com/downloads/cesiumjs/releases/{ver}/Build/Cesium/Widgets/widgets.css"/>
<script src="https://cesium.com/downloads/cesiumjs/releases/{ver}/Build/Cesium/Cesium.js"></script>
<style>html,body,#cesiumContainer{{width:100%;height:100%;margin:0;padding:0;overflow:hidden;}}</style>
</head><body>
<div id="cesiumContainer"></div>
<script>
{ion_line}
const viewer = new Cesium.Viewer("cesiumContainer", {{
  baseLayer: Cesium.ImageryLayer.fromProviderAsync(
    Cesium.TileMapServiceImageryProvider.fromUrl(
      Cesium.buildModuleUrl("Assets/Textures/NaturalEarthII")
    )
  ),
  baseLayerPicker: false,
  animation: false, timeline: false, geocoder: false,
  skyAtmosphere: false,
}});
viewer.scene.globe.enableLighting = false;
viewer.camera.flyTo({{
  destination: Cesium.Rectangle.fromDegrees(73, 18, 135, 54),
  duration: 0,
}});
</script></body></html>"""


def _fetch_upstream_tile(template: str, z: str, x: str, y: str) -> tuple[int, bytes, str]:
    url = (
        template.replace("{z}", z)
        .replace("{x}", x)
        .replace("{y}", y)
        .replace("%7Bz%7D", z)
        .replace("%7Bx%7D", x)
        .replace("%7By%7D", y)
    )
    req = urllib.request.Request(url, headers={"User-Agent": "YYnetGlobe/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read()
        ctype = resp.headers.get("Content-Type", "image/png")
        return int(resp.status), body, ctype


class _GlobeHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def _send(
        self,
        code: int,
        body: bytes,
        content_type: str = "text/html; charset=utf-8",
        extra_headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass

    def do_GET(self) -> None:
        raw = self.path or "/"
        path = raw.split("?", 1)[0]
        query = raw.split("?", 1)[1] if "?" in raw else ""
        cache_key = ""
        if query:
            for part in query.split("&"):
                if part.startswith("v="):
                    cache_key = part[2:]
                    break

        overlay_m = re.match(r"^/overlay/([a-f0-9]{16})/(\d+)/(\d+)/(\d+)\.png$", path)
        if overlay_m:
            token, z, x, y = overlay_m.groups()
            with _lock:
                template = _tile_templates.get(token)
            if not template:
                self._send(404, b"tile template not found", "text/plain; charset=utf-8")
                return
            try:
                status, body, ctype = _fetch_upstream_tile(template, z, x, y)
                if status != 200:
                    self._send(status, body, ctype)
                    return
                self._send(200, body, ctype)
            except urllib.error.HTTPError as e:
                self._send(int(e.code), e.read() if e.fp else b"", "text/plain; charset=utf-8")
            except Exception:
                self._send(502, b"tile fetch failed", "text/plain; charset=utf-8")
            return

        if path in ("/", "/globe"):
            html = ""
            if cache_key:
                disk = html_dir() / f"{cache_key}.html"
                if disk.is_file():
                    html = disk.read_text(encoding="utf-8")
                else:
                    with _lock:
                        html = _html_by_key.get(cache_key, "")
            if not html:
                with _lock:
                    if _html_by_key:
                        html = next(reversed(_html_by_key.values()))
            if not html:
                self._send(
                    200,
                    b"<html><body style='background:#111;color:#ccc;font-family:sans-serif;"
                    b"padding:2rem'>Globe page not ready. Reload Streamlit app.</body></html>",
                )
                return
            self._send(200, html.encode("utf-8"))
            return
        if path == "/test":
            self._send(200, _build_test_html().encode("utf-8"))
            return
        if path == "/minimal":
            self._send(200, _build_minimal_html().encode("utf-8"))
            return
        if path == "/health":
            self._send(200, b"ok", "text/plain; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")


def _server_healthy(port: int) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5) as sock:
            sock.sendall(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            data = sock.recv(160)
            return b"200 OK" in data
    except Exception:
        return False


def ensure_running(preferred_port: int = 8765) -> int:
    global _server, _server_port, _server_version_started

    if (
        _server is not None
        and _server_port is not None
        and _server_version_started == _SERVER_VERSION
        and _server_healthy(_server_port)
    ):
        return _server_port

    if _server is not None:
        try:
            _server.shutdown()
        except Exception:
            pass
        _server = None
        _server_port = None

    bind_host = (os.environ.get("CSTF_GLOBE_BIND") or "127.0.0.1").strip() or "127.0.0.1"
    ThreadingHTTPServer.allow_reuse_address = True
    httpd: Optional[ThreadingHTTPServer] = None
    port = preferred_port
    for candidate in range(preferred_port, preferred_port + 20):
        try:
            httpd = ThreadingHTTPServer((bind_host, candidate), _GlobeHandler)
            port = candidate
            break
        except OSError:
            httpd = None
    if httpd is None:
        httpd = ThreadingHTTPServer((bind_host, 0), _GlobeHandler)
        port = int(httpd.server_address[1])

    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="yyglobe-http", daemon=True)
    thread.start()
    _server = httpd
    _server_port = port
    _server_version_started = _SERVER_VERSION
    for _ in range(40):
        if _server_healthy(port):
            return port
        time.sleep(0.05)
    return port


def publish_html(html: str, key: str) -> None:
    with _lock:
        _html_by_key[key] = html
    path = html_dir() / f"{key}.html"
    path.write_text(html, encoding="utf-8")


def globe_url(port: int, cache_key: str = "", bust: Optional[int] = None) -> str:
    suffix = f"?v={cache_key}" if cache_key else ""
    if bust is not None and cache_key:
        suffix += f"&b={int(bust)}"
    return f"{globe_service_base(port)}/globe{suffix}"


def test_url(port: int) -> str:
    return f"{globe_service_base(port)}/test"


def remote_mode_active() -> bool:
    return public_globe_base_url() is not None
