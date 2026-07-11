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

import aiohttp
import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from uvicorn import run as uvicorn_run

STREAMLIT_UPSTREAM = (os.environ.get("CSTF_STREAMLIT_UPSTREAM") or "http://127.0.0.1:8501").rstrip("/")
GLOBE_UPSTREAM = (os.environ.get("CSTF_GLOBE_UPSTREAM") or "http://127.0.0.1:8765").rstrip("/")
GATEWAY_PORT = int(os.environ.get("CSTF_GATEWAY_PORT", "9080"))

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
    headers["Accept-Encoding"] = "identity"
    headers.update(_forwarded_headers(raw, client_ip=request.client.host if request.client else None))
    return headers


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def http_proxy(request: Request, full_path: str) -> Response:
    path = f"/{full_path}" if full_path else "/"
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
    except httpx.ConnectError as exc:
        detail = f"upstream unreachable: {upstream}{path} ({exc})"
        return Response(content=detail.encode("utf-8"), status_code=502, media_type="text/plain")

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
    except Exception as exc:
        print(f"[CSTF] websocket proxy error {path}: {exc}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    for _pk in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(_pk, None)
    print(f"CSTF Gateway http://0.0.0.0:{GATEWAY_PORT}")
    print(f"  Streamlit -> {STREAMLIT_UPSTREAM}")
    print(f"  Globe     -> {GLOBE_UPSTREAM}")
    print("  下一步: ngrok http", GATEWAY_PORT)
    uvicorn_run(app, host="0.0.0.0", port=GATEWAY_PORT, log_level="warning")
