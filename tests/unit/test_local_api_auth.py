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

import api_server  # noqa: E402


class TestLocalApiAuth(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_token = api_server.LOCAL_API_ACCESS_TOKEN
        self.old_limit = api_server.MAX_REQUEST_BYTES
        api_server.LOCAL_API_ACCESS_TOKEN = "unit-local-token"
        api_server.MAX_REQUEST_BYTES = 2048
        self.transport = httpx.ASGITransport(app=api_server.app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://testserver")

    async def asyncTearDown(self):
        await self.client.aclose()
        api_server.LOCAL_API_ACCESS_TOKEN = self.old_token
        api_server.MAX_REQUEST_BYTES = self.old_limit

    async def test_bearer_required_and_valid_request_works(self):
        payload = {"model": "local", "messages": [{"role": "user", "content": "hello"}]}
        denied = await self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(denied.status_code, 401)

        original = api_server.generate_text
        api_server.generate_text = lambda request: "ok"
        try:
            response = await self.client.post(
                "/v1/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer unit-local-token"},
            )
        finally:
            api_server.generate_text = original
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("unit-local-token", response.text)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "ok")

    async def test_generation_limit_and_body_limit(self):
        too_many = {
            "model": "local",
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": api_server.MAX_GENERATION_TOKENS + 1,
        }
        response = await self.client.post(
            "/v1/chat/completions",
            json=too_many,
            headers={"Authorization": "Bearer unit-local-token"},
        )
        self.assertEqual(response.status_code, 422)
        response = await self.client.post(
            "/v1/chat/completions",
            content=b"x" * 4096,
            headers={
                "Authorization": "Bearer unit-local-token",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(response.status_code, 413)

    async def test_chunked_body_limit_is_enforced_without_content_length(self):
        async def oversized_body():
            yield b"x" * (api_server.MAX_REQUEST_BYTES + 1)

        response = await self.client.post(
            "/v1/chat/completions",
            content=oversized_body(),
            headers={"Authorization": "Bearer unit-local-token", "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_startup_requires_token_for_all_modes(self):
        with self.assertRaises(RuntimeError):
            api_server.validate_startup_config("127.0.0.1", "")
        with self.assertRaises(RuntimeError):
            api_server.validate_startup_config("0.0.0.0", "")

    async def test_asgi_lifespan_fails_closed_without_token(self):
        old_token = api_server.LOCAL_API_ACCESS_TOKEN
        try:
            api_server.LOCAL_API_ACCESS_TOKEN = ""
            with self.assertRaises(RuntimeError):
                async with api_server.lifespan(api_server.app):
                    pass
        finally:
            api_server.LOCAL_API_ACCESS_TOKEN = old_token


if __name__ == "__main__":
    unittest.main()
