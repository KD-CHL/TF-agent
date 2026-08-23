"""
CSTF 远程演示网关：单端口合并 Streamlit(8501) + 三维地球(8765)。

用法（本机先启动 streamlit 与地球服务后）：
  python cstf_gateway.py

然后只开一条 ngrok：
  ngrok http 9080

将 ngrok 的 https 地址同时设为访问入口，并写入：
  $env:CSTF_GLOBE_PUBLIC_URL = "https://xxxx.ngrok-free.dev"
重启 streamlit 后，导师浏览器访问同一域名即可加载 3D 地球。
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

try:
    from dotenv import load_dotenv

    _THIS_DIR = os.path.dirname(os.path.abspath(__file__))
    # Explicit process environment wins; the ignored local .env is a fallback.
    load_dotenv(os.path.join(_THIS_DIR, ".env"), override=False)
except ImportError:
    pass

import aiohttp
import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from uvicorn import run as uvicorn_run

from gateway_auth import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    Session,
    SessionStore,
    auth_required,
    is_loopback_host,
    origin_allowed,
    token_matches,
    validate_security_config,
)

STREAMLIT_UPSTREAM = (os.environ.get("CSTF_STREAMLIT_UPSTREAM") or "http://127.0.0.1:8501").rstrip("/")
GLOBE_UPSTREAM = (os.environ.get("CSTF_GLOBE_UPSTREAM") or "http://127.0.0.1:8765").rstrip("/")
GATEWAY_PORT = int(os.environ.get("CSTF_GATEWAY_PORT", "9080"))
GATEWAY_HOST = (os.environ.get("CSTF_GATEWAY_HOST") or "127.0.0.1").strip()
PUBLIC_URL = (os.environ.get("CSTF_PUBLIC_URL") or os.environ.get("CSTF_GLOBE_PUBLIC_URL") or "").rstrip("/")
MAX_REQUEST_BYTES = int(os.environ.get("CSTF_GATEWAY_MAX_REQUEST_BYTES", str(8 * 1024 * 1024)))

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

# httpx 会解压 gzip 响应体，若仍保留 Content-Encoding 会导致浏览器空白页
_STRIP_RESPONSE = {"content-encoding", "content-length"}

app = FastAPI(title="CSTF Gateway", docs_url=None, redoc_url=None)
_SESSIONS = SessionStore()


class LoginRequest(BaseModel):
    access_token: str = Field(min_length=1, max_length=4096)


def _access_token() -> str:
    # 每次读取，令轮换后的旧 session 立即失效。
    return (os.environ.get("CSTF_GATEWAY_ACCESS_TOKEN") or "").strip()


def _auth_is_required() -> bool:
    return auth_required(GATEWAY_HOST, PUBLIC_URL)


def _public_origin(request: Request | WebSocket) -> str:
    if PUBLIC_URL:
        return PUBLIC_URL
    headers = request.headers
    proto = headers.get("x-forwarded-proto") or ("https" if headers.get("host", "").endswith(":443") else "http")
    return f"{proto}://{headers.get('x-forwarded-host') or headers.get('host') or '127.0.0.1'}"


def _session_from_request(request: Request | WebSocket) -> Optional[Session]:
    if not _auth_is_required():
        return None
    return _SESSIONS.get(request.cookies.get(SESSION_COOKIE, ""), _access_token())


def _csrf_ok(request: Request, session: Session) -> bool:
    value = request.headers.get("x-cstf-csrf") or ""
    return token_matches(session.csrf_token, value)


def _origin_ok(request: Request | WebSocket) -> bool:
    origin = request.headers.get("origin") or ""
    return origin_allowed(origin, _public_origin(request), request.headers.get("host", ""))


def _unauthorized() -> JSONResponse:
    # 不回显令牌、session ID 或内部鉴权原因。
    return JSONResponse({"detail": "authentication required"}, status_code=401)


@app.middleware("http")
async def request_size_limit(request: Request, call_next):
    length = request.headers.get("content-length")
    try:
        if length is not None and int(length) > MAX_REQUEST_BYTES:
            return Response(content=b"request too large", status_code=413, media_type="text/plain")
    except (TypeError, ValueError):
        return Response(content=b"invalid content length", status_code=400, media_type="text/plain")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_REQUEST_BYTES:
            return Response(content=b"request too large", status_code=413, media_type="text/plain")
    request._body = bytes(chunks)
    return await call_next(request)


@app.get("/__auth/login", response_class=HTMLResponse)
async def login_page() -> str:
    return """<!doctype html><meta charset='utf-8'><title>CSTF Login</title>
    <form method='post' action='/__auth/login' onsubmit='return login(event)'>
      <label>Access token <input id='token' type='password' autocomplete='off'></label>
      <button>Sign in</button>
    </form><p id='status'></p>
    <script>
    async function login(e) { e.preventDefault();
      const r = await fetch('/__auth/login', {method:'POST', headers:{'content-type':'application/json'},
        body: JSON.stringify({access_token: document.getElementById('token').value})});
      const d = await r.json();
      if (r.ok) { window.__cstfCsrf = d.csrf_token; location.href='/'; }
      else document.getElementById('status').textContent='登录失败';
      return false;
    }
    </script>"""


@app.post("/__auth/login")
async def login(payload: LoginRequest, response: Response):
    expected = _access_token()
    if not expected or not token_matches(expected, payload.access_token):
        return _unauthorized()
    session = _SESSIONS.create(expected)
    # A cookie marked Secure is silently dropped by browsers on an explicit
    # ``http://`` public URL (including local proxy acceptance).  Only infer
    # HTTPS from the configured public scheme; when no public URL is supplied,
    # retain the fail-closed non-loopback default for deployments behind TLS.
    secure = PUBLIC_URL.lower().startswith("https://") or (
        not PUBLIC_URL and not is_loopback_host(GATEWAY_HOST)
    )
    response.set_cookie(
        SESSION_COOKIE,
        session.session_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    # CSRF token 仅返回响应体，绝不写入 URL/localStorage/cookie。
    return {"authenticated": True, "csrf_token": session.csrf_token, "expires_in": SESSION_TTL_SECONDS}


@app.get("/__auth/session")
async def session_info(request: Request):
    session = _session_from_request(request)
    if _auth_is_required() and session is None:
        return _unauthorized()
    if session is None:
        return {"authenticated": False}
    return {"authenticated": True, "csrf_token": session.csrf_token, "expires_at": session.expires_at}


@app.post("/__auth/logout")
async def logout(request: Request):
    session = _session_from_request(request)
    if _auth_is_required() and (session is None or not _origin_ok(request) or not _csrf_ok(request, session)):
        return _unauthorized()
    if session:
        _SESSIONS.revoke(session.session_id)
    response = JSONResponse({"logged_out": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response

# 访问本机 8501/8765 必须直连，不能走 Clash 系统代理
_HTTP_CLIENT = httpx.AsyncClient(
    follow_redirects=False,
    timeout=300.0,
    trust_env=False,
    proxy=None,
)


def _globe_path(path: str) -> bool:
    if path in ("/globe", "/health", "/test", "/minimal"):
        return True
    return path.startswith("/overlay/")


def _filter_headers(headers: httpx.Headers, *, is_response: bool = False) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in _HOP_BY_HOP:
            continue
        if is_response and lk in _STRIP_RESPONSE:
            continue
        if not is_response and lk in ("host", "accept-encoding"):
            continue
        out[k] = v
    return out


def _strip_edge_session_cookie(cookie_header: str) -> str:
    """Remove only the gateway session cookie before proxying upstream.

    Streamlit and other upstreams may use their own cookies for a browser
    session; dropping the complete Cookie header breaks those sessions.  The
    edge credential is the only cookie that must stay at the gateway.
    """
    kept: list[str] = []
    for part in str(cookie_header or "").split(";"):
        name, separator, value = part.strip().partition("=")
        if not separator or name.strip().lower() == SESSION_COOKIE.lower():
            if separator and name.strip().lower() != SESSION_COOKIE.lower():
                kept.append(f"{name.strip()}={value.strip()}")
            continue
        kept.append(f"{name.strip()}={value.strip()}")
    return "; ".join(kept)


def _scope_headers(scope: dict) -> dict[str, str]:
    return {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}


def _public_host(headers: dict[str, str]) -> str:
    return headers.get("x-forwarded-host") or headers.get("host") or "127.0.0.1:8501"


def _public_proto(headers: dict[str, str], *, fallback: str = "http") -> str:
    return headers.get("x-forwarded-proto") or fallback


def _forwarded_headers(headers: dict[str, str], *, client_ip: str | None = None) -> dict[str, str]:
    host = _public_host(headers)
    proto = _public_proto(headers)
    out = {
        "Host": host,
        "X-Forwarded-Host": host,
        "X-Forwarded-Proto": proto,
    }
    if client_ip:
        prior = headers.get("x-forwarded-for")
        out["X-Forwarded-For"] = f"{prior}, {client_ip}" if prior else client_ip
    return out


def _upstream_request_headers(request: Request) -> dict[str, str]:
    raw = {k.lower(): v for k, v in request.headers.items()}
    headers = _filter_headers(request.headers)
    # Gateway credentials remain at the edge; never forward them to Streamlit/Globe.
    headers.pop("authorization", None)
    for key in list(headers):
        if key.lower() == "cookie":
            filtered_cookie = _strip_edge_session_cookie(headers[key])
            if filtered_cookie:
                headers[key] = filtered_cookie
            else:
                headers.pop(key, None)
    headers.pop("x-cstf-csrf", None)
    headers["Accept-Encoding"] = "identity"
    headers.update(_forwarded_headers(raw, client_ip=request.client.host if request.client else None))
    return headers


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def http_proxy(request: Request, full_path: str) -> Response:
    path = f"/{full_path}" if full_path else "/"
    if _auth_is_required() and path not in {"/__auth/login", "/__auth/session", "/__auth/logout"}:
        session = _session_from_request(request)
        if session is None:
            return _unauthorized()
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not _origin_ok(request) or not _csrf_ok(request, session):
                return Response(content=b"forbidden", status_code=403, media_type="text/plain")
    upstream = GLOBE_UPSTREAM if _globe_path(path) else STREAMLIT_UPSTREAM
    query = request.url.query
    url = f"{upstream}{path}" + (f"?{query}" if query else "")

    body = await request.body()
    headers = _upstream_request_headers(request)

    try:
        upstream_resp = await _HTTP_CLIENT.request(
            request.method,
            url,
            headers=headers,
            content=body if body else None,
        )
    except httpx.ConnectError:
        # Do not reflect internal upstream URLs, credentials or transport
        # exceptions to the public client.
        return Response(content=b"upstream unavailable", status_code=502, media_type="text/plain")

    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=_filter_headers(upstream_resp.headers, is_response=True),
    )


def _ws_subprotocols(scope: dict) -> list[str]:
    raw = _scope_headers(scope)
    proto_hdr = raw.get("sec-websocket-protocol", "")
    if not proto_hdr:
        return []
    return [p.strip() for p in proto_hdr.split(",") if p.strip()]


def _ws_upstream_headers(scope: dict) -> dict[str, str]:
    skip = {
        "host",
        "connection",
        "upgrade",
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
        "sec-websocket-protocol",
    }
    raw = _scope_headers(scope)
    out: dict[str, str] = {}
    for k, v in scope.get("headers", []):
        name = k.decode().lower()
        if name in skip:
            continue
        out[k.decode()] = v.decode()
    if "cookie" in raw:
        filtered_cookie = _strip_edge_session_cookie(raw["cookie"])
        for key in list(out):
            if key.lower() == "cookie":
                out.pop(key, None)
        if filtered_cookie:
            out["Cookie"] = filtered_cookie
    client = scope.get("client")
    client_ip = client[0] if client else None
    out.update(_forwarded_headers(raw, client_ip=client_ip))
    return out


@app.websocket("/{full_path:path}")
async def websocket_proxy(websocket: WebSocket, full_path: str) -> None:
    path = f"/{full_path}" if full_path else "/"
    if _globe_path(path):
        await websocket.close(code=1008)
        return

    if _auth_is_required():
        session = _session_from_request(websocket)
        if session is None or not _origin_ok(websocket):
            await websocket.close(code=1008)
            return

    scope_hdrs = _scope_headers(websocket.scope)
    subprotocols = _ws_subprotocols(websocket.scope)
    await websocket.accept(subprotocol=subprotocols[0] if subprotocols else None)

    target = STREAMLIT_UPSTREAM.replace("http://", "ws://").replace("https://", "wss://")
    query = websocket.scope.get("query_string", b"").decode()
    base = f"{target}/{full_path}" if full_path else f"{target}/"
    target_url = f"{base}?{query}" if query else base
    ws_headers = _ws_upstream_headers(websocket.scope)
    origin = ws_headers.get("Origin") or (
        f"{_public_proto(scope_hdrs)}://{_public_host(scope_hdrs)}"
    )

    try:
        timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=None)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.ws_connect(
                target_url,
                headers=ws_headers,
                origin=origin,
                protocols=subprotocols or None,
                autoping=False,
                heartbeat=None,
                max_msg_size=0,
            ) as upstream:

                async def client_to_upstream() -> None:
                    try:
                        while True:
                            msg = await websocket.receive()
                            if msg["type"] == "websocket.disconnect":
                                break
                            if msg.get("text") is not None:
                                await upstream.send_str(msg["text"])
                            elif msg.get("bytes") is not None:
                                await upstream.send_bytes(msg["bytes"])
                    except WebSocketDisconnect:
                        pass

                async def upstream_to_client() -> None:
                    try:
                        async for msg in upstream:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await websocket.send_text(msg.data)
                            elif msg.type == aiohttp.WSMsgType.BINARY:
                                await websocket.send_bytes(msg.data)
                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSE,
                                aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.CLOSED,
                            ):
                                break
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                break
                    except Exception:
                        pass

                await asyncio.gather(client_to_upstream(), upstream_to_client())
    except Exception:
        # Keep transport details out of logs; the path is sufficient for local
        # diagnosis and contains no credentials.
        print(f"[CSTF] websocket proxy unavailable: {path}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    for _pk in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(_pk, None)
    validate_security_config(GATEWAY_HOST, PUBLIC_URL, _access_token())
    print(f"CSTF Gateway http://{GATEWAY_HOST}:{GATEWAY_PORT}")
    print(f"  Streamlit -> {STREAMLIT_UPSTREAM}")
    print(f"  Globe     -> {GLOBE_UPSTREAM}")
    print("  下一步: ngrok http", GATEWAY_PORT)
    uvicorn_run(app, host=GATEWAY_HOST, port=GATEWAY_PORT, log_level="warning")
