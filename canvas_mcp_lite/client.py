"""Minimal Canvas LMS API client: auth, pagination, error surfacing, retry."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv

# Resolve .env relative to the package location, not the process cwd —
# MCP clients (Claude Desktop) launch the server with an arbitrary cwd,
# so a bare load_dotenv() silently finds nothing there.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

CANVAS_API_URL = os.environ.get("CANVAS_API_URL", "").rstrip("/")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN", "")

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.5


class CanvasAPIError(Exception):
    def __init__(self, status_code: int, message: str, url: str):
        self.status_code = status_code
        self.url = url
        super().__init__(f"Canvas API error {status_code} for {url}: {message}")


def _require_config() -> None:
    if not CANVAS_API_URL or not CANVAS_API_TOKEN:
        raise RuntimeError(
            "CANVAS_API_URL and CANVAS_API_TOKEN must be set in the environment (.env)"
        )


def _client() -> httpx.AsyncClient:
    _require_config()
    # Canvas occasionally takes >30s on heavier endpoints (large rosters,
    # submissions with many overrides) — connect fast-fails, but give reads
    # more room, and retry transient timeouts below rather than surfacing
    # a bare failure on what's usually just a slow response.
    return httpx.AsyncClient(
        base_url=CANVAS_API_URL,
        headers={"Authorization": f"Bearer {CANVAS_API_TOKEN}"},
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0),
    )


async def _request_with_retry(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> httpx.Response:
    last_error: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            return await client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(BACKOFF_BASE_SECONDS * (2**attempt))
    assert last_error is not None
    raise last_error


async def canvas_request(
    method: str,
    path: str,
    params: Optional[dict[str, Any]] = None,
    json_body: Optional[dict[str, Any]] = None,
    data: Optional[dict[str, Any]] = None,
) -> Any:
    """Single Canvas API call. Raises CanvasAPIError on non-2xx, retries transient timeouts."""
    async with _client() as client:
        response = await _request_with_retry(
            client, method, path, params=params, json=json_body, data=data
        )
        if response.status_code >= 400:
            raise CanvasAPIError(response.status_code, response.text, str(response.url))
        if response.status_code == 204 or not response.content:
            return None
        return response.json()


async def canvas_paginated(path: str, params: Optional[dict[str, Any]] = None) -> list[Any]:
    """Follow Canvas's Link-header pagination and return the full result list."""
    results: list[Any] = []
    async with _client() as client:
        url: Optional[str] = path
        request_params = dict(params or {})
        request_params.setdefault("per_page", 100)
        while url:
            response = await _request_with_retry(client, "GET", url, params=request_params)
            if response.status_code >= 400:
                raise CanvasAPIError(response.status_code, response.text, str(response.url))
            page = response.json()
            if isinstance(page, list):
                results.extend(page)
            else:
                results.append(page)
            url = None
            request_params = {}
            link_header = response.headers.get("link", "")
            for part in link_header.split(","):
                if 'rel="next"' in part:
                    url = part[part.find("<") + 1 : part.find(">")]
                    break
    return results
