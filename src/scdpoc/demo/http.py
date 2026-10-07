"""Minimal HTTP transport abstraction so the demo can call its own standards
endpoints over real HTTP in production and through an in-process test client
in the test suite."""
from __future__ import annotations

from typing import Protocol

import httpx


class Http(Protocol):
    def post(self, url: str, body: bytes, content_type: str) -> tuple[int, str, bytes]: ...

    def get(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, str, bytes]: ...


class HttpxTransport:
    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout

    def post(self, url: str, body: bytes, content_type: str) -> tuple[int, str, bytes]:
        r = httpx.post(url, content=body, headers={"Content-Type": content_type}, timeout=self.timeout)
        return r.status_code, r.headers.get("content-type", ""), r.content

    def get(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, str, bytes]:
        r = httpx.get(url, headers=headers or {}, timeout=self.timeout)
        return r.status_code, r.headers.get("content-type", ""), r.content


class TestClientTransport:
    """Adapter for starlette.testclient.TestClient (used by the test suite)."""

    def __init__(self, client, base_url: str):
        self.client = client
        self.base = base_url.rstrip("/")

    def _path(self, url: str) -> str:
        return url.removeprefix(self.base)

    def post(self, url: str, body: bytes, content_type: str) -> tuple[int, str, bytes]:
        r = self.client.post(self._path(url), content=body, headers={"Content-Type": content_type})
        return r.status_code, r.headers.get("content-type", ""), r.content

    def get(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, str, bytes]:
        r = self.client.get(self._path(url), headers=headers or {})
        return r.status_code, r.headers.get("content-type", ""), r.content
