# -*- coding: utf-8 -*-
"""本地 OpenAI-compatible 模型 API。

模型按首次请求惰性加载，默认只监听 loopback；ASGI lifespan 启动时强制校验认证配置；生产/局域网绑定必须显式配置
`CSTF_LOCAL_API_TOKEN`（或 `LOCAL_API_ACCESS_TOKEN`）。
"""
from __future__ import annotations

import asyncio
import hmac
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

try:
    from dotenv import load_dotenv

    _THIS_DIR = os.path.dirname(os.path.abspath(__file__))
    # Explicit process environment wins; the ignored local .env is a fallback.
    load_dotenv(os.path.join(_THIS_DIR, ".env"), override=False)
except ImportError:
    pass

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
import uvicorn

from gateway_auth import is_loopback_host


LOCAL_API_HOST = (os.environ.get("CSTF_LOCAL_API_HOST") or "127.0.0.1").strip()
LOCAL_API_PORT = int(os.environ.get("CSTF_LOCAL_API_PORT", "8000"))
LOCAL_API_ACCESS_TOKEN = (
    os.environ.get("CSTF_LOCAL_API_TOKEN")
    or os.environ.get("LOCAL_API_ACCESS_TOKEN")
    or ""
).strip()
MAX_REQUEST_BYTES = int(os.environ.get("CSTF_LOCAL_API_MAX_REQUEST_BYTES", str(2 * 1024 * 1024)))
MAX_GENERATION_TOKENS = int(os.environ.get("CSTF_LOCAL_API_MAX_GENERATION_TOKENS", "1024"))
MAX_CONCURRENT_REQUESTS = max(1, int(os.environ.get("CSTF_LOCAL_API_MAX_CONCURRENCY", "2")))
MODEL_PATH = os.environ.get("CSTF_LOCAL_MODEL_PATH", "./Qwen_Agent_Merged")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Validate auth before uvicorn starts serving the ASGI application."""
    validate_startup_config()
    yield


app = FastAPI(
    title="CSTF Local Model API",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
_bearer = HTTPBearer(auto_error=False)
_generation_slots = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
_model_bundle: Optional[tuple[Any, Any]] = None


class ChatRequest(BaseModel):
    model: str = Field(min_length=1, max_length=128)
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=64)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=MAX_GENERATION_TOKENS)


def validate_startup_config(host: str = LOCAL_API_HOST, token: str = LOCAL_API_ACCESS_TOKEN) -> None:
    if not is_loopback_host(host) and not token:
        raise RuntimeError("本地模型 API 绑定到非 loopback 地址时必须设置 CSTF_LOCAL_API_TOKEN。")
    if not token:
        raise RuntimeError("本地模型 API 必须设置 CSTF_LOCAL_API_TOKEN 才能启动。")


def _token_valid(credentials: Optional[HTTPAuthorizationCredentials]) -> bool:
    if not credentials or credentials.scheme.lower() != "bearer":
        return False
    expected = LOCAL_API_ACCESS_TOKEN
    provided = credentials.credentials or ""
    return bool(expected and hmac.compare_digest(expected.encode(), provided.encode()))


async def _require_bearer(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> None:
    if not _token_valid(credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.middleware("http")
async def request_size_limit(request: Request, call_next):
    length = request.headers.get("content-length")
    try:
        if length is not None and int(length) > MAX_REQUEST_BYTES:
            return await _plain_response("request too large", 413)
    except (TypeError, ValueError):
        return await _plain_response("invalid content length", 400)
    if not await _cache_body_within_limit(request, MAX_REQUEST_BYTES):
        return await _plain_response("request too large", 413)
    return await call_next(request)


async def _plain_response(text: str, code: int):
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(text, status_code=code)


async def _cache_body_within_limit(request: Request, limit: int) -> bool:
    """Consume and cache a bounded body so chunked requests cannot bypass limits."""
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > limit:
            return False
    request._body = bytes(chunks)  # Starlette Request.body() reuses this cache.
    return True


def _load_model_bundle() -> tuple[Any, Any]:
    global _model_bundle
    if _model_bundle is not None:
        return _model_bundle
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_PATH,
            torch_dtype=getattr(torch, "bfloat16", None),
            device_map=os.environ.get("CSTF_LOCAL_DEVICE_MAP", "auto"),
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("本地模型后端未配置或加载失败。") from exc
    _model_bundle = (tokenizer, model)
    return _model_bundle


def generate_text(request: ChatRequest) -> str:
    """同步生成函数，单元测试可替换；不打印 prompt、token 或输出内容。"""
    tokenizer, model = _load_model_bundle()
    text = tokenizer.apply_chat_template(
        request.messages, tokenize=False, add_generation_prompt=True
    )
    inputs = tokenizer([text], return_tensors="pt").to(model.device)
    outputs = model.generate(
        **inputs,
        max_new_tokens=request.max_tokens,
        temperature=request.temperature,
        do_sample=request.temperature > 0,
    )
    return tokenizer.decode(
        outputs[0][inputs.input_ids.shape[1] :], skip_special_tokens=True
    )


@app.post("/v1/chat/completions", dependencies=[Depends(_require_bearer)])
async def chat(request: ChatRequest):
    request_id = uuid.uuid4().hex[:12]
    started = time.monotonic()
    try:
        async with _generation_slots:
            text = await asyncio.to_thread(generate_text, request)
    except RuntimeError:
        # 不向客户端回显模型路径、依赖堆栈或内部异常。
        raise HTTPException(status_code=503, detail="local model backend unavailable") from None
    finally:
        _elapsed_ms = int((time.monotonic() - started) * 1000)
        # 只记录 request id / status / 耗时；不记录 token、prompt、图片或代理地址。
        _ = (request_id, _elapsed_ms)

    return {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion",
        "model": request.model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
    }


if __name__ == "__main__":
    validate_startup_config()
    uvicorn.run(app, host=LOCAL_API_HOST, port=LOCAL_API_PORT, log_level="warning")
