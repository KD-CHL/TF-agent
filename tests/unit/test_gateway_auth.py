# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import unittest

import httpx

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

from gateway_auth import (  # noqa: E402
    SessionStore,
    auth_required,
    is_loopback_host,
    origin_allowed,
    token_matches,
    validate_security_config,
)


def test_websocket_rejects_missing_session_before_accept():
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    import cstf_gateway as gateway

    old_host = gateway.GATEWAY_HOST
    old_public = gateway.PUBLIC_URL
    old_token = os.environ.get("CSTF_GATEWAY_ACCESS_TOKEN")
    gateway.GATEWAY_HOST = "0.0.0.0"
    gateway.PUBLIC_URL = "https://demo.example"
    os.environ["CSTF_GATEWAY_ACCESS_TOKEN"] = "gateway-token"
    gateway._SESSIONS = gateway.SessionStore()
    try:
        with TestClient(gateway.app) as client:
            try:
                with client.websocket_connect("/stream", headers={"Origin": "https://demo.example"}):
                    raise AssertionError("unauthenticated websocket was accepted")
            except WebSocketDisconnect as exc:
                assert exc.code == 1008
    finally:
        gateway.GATEWAY_HOST = old_host
        gateway.PUBLIC_URL = old_public
        if old_token is None:
            os.environ.pop("CSTF_GATEWAY_ACCESS_TOKEN", None)
        else:
            os.environ["CSTF_GATEWAY_ACCESS_TOKEN"] = old_token


class TestGatewayAuthPrimitives(unittest.TestCase):
    def test_loopback_default_does_not_require_auth(self):
        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertTrue(is_loopback_host("::1"))
        self.assertFalse(auth_required("127.0.0.1", ""))
        self.assertTrue(auth_required("127.0.0.1", "https://demo.example"))
        self.assertTrue(auth_required("0.0.0.0", ""))

    def test_public_bind_without_token_fails_closed(self):
        with self.assertRaises(RuntimeError):
            validate_security_config("0.0.0.0", "", "")
        with self.assertRaises(RuntimeError):
            validate_security_config("127.0.0.1", "https://demo.example", "")

    def test_constant_time_token_and_origin_helpers(self):
        self.assertTrue(token_matches("abc", "abc"))
        self.assertFalse(token_matches("abc", "abd"))
        self.assertTrue(origin_allowed("https://demo.example", "https://demo.example"))
        self.assertFalse(origin_allowed("https://evil.example", "https://demo.example"))

    def test_upstream_headers_do_not_forward_edge_session_cookie(self):
        from starlette.requests import Request
        import cstf_gateway as gateway

        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [
                (b"host", b"demo.example"),
                (b"cookie", b"cstf_session=edge-secret"),
                (b"authorization", b"Bearer edge-secret"),
            ],
            "client": ("127.0.0.1", 1234),
            "scheme": "https",
            "server": ("demo.example", 443),
        })
        forwarded = gateway._upstream_request_headers(request)
        self.assertNotIn("cookie", {key.lower() for key in forwarded})
        self.assertNotIn("authorization", {key.lower() for key in forwarded})
        websocket_scope = {
            "headers": [
                (b"host", b"demo.example"),
                (b"origin", b"https://demo.example"),
                (b"cookie", b"cstf_session=edge-secret"),
            ],
            "client": ("127.0.0.1", 1234),
        }
        ws_forwarded = gateway._ws_upstream_headers(websocket_scope)
        self.assertNotIn("cookie", {key.lower() for key in ws_forwarded})

    def test_upstream_headers_preserve_non_edge_cookies(self):
        from starlette.requests import Request
        import cstf_gateway as gateway

        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [
                (b"host", b"demo.example"),
                (b"cookie", b"cstf_session=edge-secret; streamlit_session=upstream-state"),
            ],
            "client": ("127.0.0.1", 1234),
            "scheme": "https",
            "server": ("demo.example", 443),
        })
        forwarded = gateway._upstream_request_headers(request)
        cookie = forwarded.get("Cookie") or forwarded.get("cookie") or ""
        self.assertIn("streamlit_session=upstream-state", cookie)
        self.assertNotIn("cstf_session", cookie)

        websocket_scope = {
            "headers": [
                (b"host", b"demo.example"),
                (b"origin", b"https://demo.example"),
                (b"cookie", b"cstf_session=edge-secret; streamlit_session=upstream-state"),
            ],
            "client": ("127.0.0.1", 1234),
        }
        ws_forwarded = gateway._ws_upstream_headers(websocket_scope)
        ws_cookie = ws_forwarded.get("Cookie") or ws_forwarded.get("cookie") or ""
        self.assertIn("streamlit_session=upstream-state", ws_cookie)
        self.assertNotIn("cstf_session", ws_cookie)

    def test_expiry_and_rotation_invalidate_sessions(self):
        now = [100.0]
        store = SessionStore(ttl_seconds=10, now_fn=lambda: now[0])
        session = store.create("first-token")
        self.assertIsNotNone(store.get(session.session_id, "first-token"))
        self.assertIsNone(store.get(session.session_id, "rotated-token"))
        now[0] = 111.0
        self.assertIsNone(store.get(session.session_id, "first-token"))


class TestGatewayHttpAuth(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import cstf_gateway as gateway

        self.gateway = gateway
        self.old_host = gateway.GATEWAY_HOST
        self.old_public = gateway.PUBLIC_URL
        self.old_limit = gateway.MAX_REQUEST_BYTES
        self.old_token = os.environ.get("CSTF_GATEWAY_ACCESS_TOKEN")
        gateway.GATEWAY_HOST = "0.0.0.0"
        gateway.PUBLIC_URL = "https://demo.example"
        os.environ["CSTF_GATEWAY_ACCESS_TOKEN"] = "gateway-token"
        gateway._SESSIONS = gateway.SessionStore()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway.app),
            base_url="https://demo.example",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.gateway.GATEWAY_HOST = self.old_host
        self.gateway.PUBLIC_URL = self.old_public
        self.gateway.MAX_REQUEST_BYTES = self.old_limit
        if self.old_token is None:
            os.environ.pop("CSTF_GATEWAY_ACCESS_TOKEN", None)
        else:
            os.environ["CSTF_GATEWAY_ACCESS_TOKEN"] = self.old_token

    async def test_http_login_session_csrf_and_rotation(self):
        denied = await self.client.get("/")
        self.assertEqual(denied.status_code, 401)
        bad = await self.client.post("/__auth/login", json={"access_token": "wrong"})
        self.assertEqual(bad.status_code, 401)

        login = await self.client.post("/__auth/login", json={"access_token": "gateway-token"})
        self.assertEqual(login.status_code, 200)
        self.assertNotIn("gateway-token", login.text)
        csrf = login.json()["csrf_token"]
        self.assertIn("cstf_session", self.client.cookies)
        cookie = login.headers.get("set-cookie", "").lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)

        # Authenticated GET reaches upstream (which is absent in this unit test), not 401.
        upstream = await self.client.get("/")
        self.assertNotEqual(upstream.status_code, 401)
        denied_write = await self.client.post("/", content=b"x", headers={"Origin": "https://demo.example"})
        self.assertEqual(denied_write.status_code, 403)
        bad_origin = await self.client.post(
            "/", content=b"x", headers={"Origin": "https://evil.example", "X-CSTF-CSRF": csrf}
        )
        self.assertEqual(bad_origin.status_code, 403)

        old_session = await self.client.get("/__auth/session")
        self.assertEqual(old_session.status_code, 200)
        os.environ["CSTF_GATEWAY_ACCESS_TOKEN"] = "rotated-token"
        rotated = await self.client.get("/")
        self.assertEqual(rotated.status_code, 401)

    async def test_login_page_does_not_put_token_in_url(self):
        page = await self.client.get("/__auth/login")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("CSTF_GATEWAY_ACCESS_TOKEN", page.text)
        self.assertNotIn("access_token=", page.text)

    async def test_http_public_url_does_not_mark_session_cookie_secure(self):
        old_public = self.gateway.PUBLIC_URL
        self.gateway.PUBLIC_URL = "http://demo.example"
        try:
            login = await self.client.post("/__auth/login", json={"access_token": "gateway-token"})
        finally:
            self.gateway.PUBLIC_URL = old_public
        self.assertEqual(login.status_code, 200)
        cookie = login.headers.get("set-cookie", "").lower()
        self.assertNotIn("; secure", cookie)

    async def test_logout_requires_csrf_and_revokes_session(self):
        login = await self.client.post("/__auth/login", json={"access_token": "gateway-token"})
        self.assertEqual(login.status_code, 200)
        csrf = login.json()["csrf_token"]
        denied = await self.client.post(
            "/__auth/logout", headers={"Origin": "https://demo.example"}
        )
        self.assertEqual(denied.status_code, 401)
        logout = await self.client.post(
            "/__auth/logout",
            headers={"Origin": "https://demo.example", "X-CSTF-CSRF": csrf},
        )
        self.assertEqual(logout.status_code, 200)
        self.assertTrue(logout.json()["logged_out"])
        session = await self.client.get("/__auth/session")
        self.assertEqual(session.status_code, 401)

    async def test_upstream_failure_does_not_reflect_internal_url_or_credentials(self):
        import httpx as _httpx

        original = self.gateway._HTTP_CLIENT.request

        async def fail(*args, **kwargs):
            raise _httpx.ConnectError("http://user:secret@127.0.0.1:8501")

        self.gateway._HTTP_CLIENT.request = fail
        try:
            login = await self.client.post("/__auth/login", json={"access_token": "gateway-token"})
            self.assertEqual(login.status_code, 200)
            response = await self.client.get("/")
        finally:
            self.gateway._HTTP_CLIENT.request = original
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.text, "upstream unavailable")
        self.assertNotIn("secret", response.text)
        self.assertNotIn("127.0.0.1", response.text)

    async def test_chunked_body_limit_is_enforced_without_content_length(self):
        login = await self.client.post("/__auth/login", json={"access_token": "gateway-token"})
        self.assertEqual(login.status_code, 200)
        csrf = login.json()["csrf_token"]
        self.gateway.MAX_REQUEST_BYTES = 32

        async def oversized_body():
            yield b"x" * 64

        response = await self.client.post(
            "/",
            content=oversized_body(),
            headers={"Origin": "https://demo.example", "X-CSTF-CSRF": csrf},
        )
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
