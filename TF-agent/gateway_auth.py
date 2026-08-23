# -*- coding: utf-8 -*-
"""CSTF Gateway / 本地 API 共用的最小认证原语。

只保存会话 ID 的哈希指纹、CSRF 随机值和过期时间；绝不保存或记录明文访问令牌。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Dict, Optional
from urllib.parse import urlparse


SESSION_COOKIE = "cstf_session"
SESSION_TTL_SECONDS = 8 * 60 * 60


def is_loopback_host(host: str) -> bool:
    value = (host or "").strip().lower()
    if value in {"localhost", "127.0.0.1", "::1", "[::1]"}:
        return True
    try:
        return ip_address(value.strip("[]")).is_loopback
    except ValueError:
        return False


def auth_required(bind_host: str, public_url: str = "") -> bool:
    return bool(public_url.strip()) or not is_loopback_host(bind_host)


def validate_security_config(bind_host: str, public_url: str, access_token: str) -> None:
    if auth_required(bind_host, public_url) and not (access_token or "").strip():
        raise RuntimeError(
            "非 loopback 或公开 URL 模式必须设置 CSTF_GATEWAY_ACCESS_TOKEN。"
        )


def token_matches(expected: str, provided: str) -> bool:
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def token_fingerprint(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


@dataclass
class Session:
    session_id: str
    token_fp: str
    csrf_token: str
    expires_at: float


class SessionStore:
    def __init__(self, ttl_seconds: int = SESSION_TTL_SECONDS, now_fn=None):
        self.ttl_seconds = int(ttl_seconds)
        self._now = now_fn or time.time
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.RLock()

    def create(self, access_token: str) -> Session:
        now = float(self._now())
        session = Session(
            session_id=secrets.token_urlsafe(32),
            token_fp=token_fingerprint(access_token),
            csrf_token=secrets.token_urlsafe(32),
            expires_at=now + self.ttl_seconds,
        )
        with self._lock:
            self._purge(now)
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str, access_token: str) -> Optional[Session]:
        if not session_id or not access_token:
            return None
        now = float(self._now())
        with self._lock:
            self._purge(now)
            session = self._sessions.get(session_id)
            if not session or session.expires_at <= now:
                return None
            # token rotation invalidates all sessions created with the old token.
            if not hmac.compare_digest(session.token_fp, token_fingerprint(access_token)):
                return None
            return session

    def revoke(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def _purge(self, now: float) -> None:
        expired = [sid for sid, session in self._sessions.items() if session.expires_at <= now]
        for sid in expired:
            self._sessions.pop(sid, None)


def origin_allowed(origin: str, expected_origin: str, request_host: str = "") -> bool:
    """只允许精确 origin；无 Origin 的同源服务端请求由调用方单独决定。"""
    origin = (origin or "").strip().rstrip("/")
    expected = (expected_origin or "").strip().rstrip("/")
    if not origin:
        return False
    if expected:
        return hmac.compare_digest(origin, expected)
    parsed = urlparse(origin)
    return bool(parsed.scheme and parsed.netloc and parsed.netloc == (request_host or ""))


__all__ = [
    "SESSION_COOKIE", "SESSION_TTL_SECONDS", "Session", "SessionStore",
    "auth_required", "is_loopback_host", "origin_allowed", "token_matches",
    "validate_security_config",
]
