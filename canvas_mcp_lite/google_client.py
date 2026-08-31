"""Minimal Google Drive API client for the Google Docs grading tools.

Auth model: the instructor runs `canvas-mcp-google-auth` ONCE, in their own
browser on their own machine, to mint a long-lived OAuth refresh token for
their Google account. That token (plus the OAuth client id/secret) goes into
the server's environment — .env locally, or env vars on Railway. From then on
the server mints short-lived access tokens itself; no interactive Google login
ever happens where the server runs.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_API = "https://www.googleapis.com/drive/v3"
GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"

SETUP_MESSAGE = (
    "Google Docs integration isn't configured on this server. The instructor "
    "needs to run `canvas-mcp-google-auth` once (in a browser on their own "
    "machine) to connect their Google account, then set GOOGLE_OAUTH_CLIENT_ID, "
    "GOOGLE_OAUTH_CLIENT_SECRET, and GOOGLE_OAUTH_REFRESH_TOKEN in the server's "
    ".env (or Railway environment variables) and restart the server."
)


class GoogleConfigError(RuntimeError):
    """Google credentials missing or no longer valid — setup/re-auth needed."""


class GoogleAPIError(Exception):
    def __init__(self, status_code: int, message: str, url: str):
        self.status_code = status_code
        self.url = url
        super().__init__(f"Google Drive API error {status_code} for {url}: {message}")


def _credentials() -> tuple[str, str, str]:
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN", "")
    if not (client_id and client_secret and refresh_token):
        raise GoogleConfigError(SETUP_MESSAGE)
    return client_id, client_secret, refresh_token


# Access tokens last ~1 hour; cache one per process and refresh just before expiry.
_access_token: Optional[str] = None
_token_expires_at: float = 0.0


async def _get_access_token(force: bool = False) -> str:
    global _access_token, _token_expires_at
    if _access_token and not force and time.time() < _token_expires_at - 60:
        return _access_token

    client_id, client_secret, refresh_token = _credentials()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if response.status_code >= 400:
        # invalid_grant means the refresh token was revoked or expired
        # (e.g. OAuth consent screen still in "Testing" mode caps tokens at 7 days).
        if "invalid_grant" in response.text:
            raise GoogleConfigError(
                "The stored Google refresh token is no longer valid — the "
                "instructor needs to re-run `canvas-mcp-google-auth` and update "
                "GOOGLE_OAUTH_REFRESH_TOKEN. (If this keeps happening, publish "
                "the OAuth consent screen: apps left in 'Testing' mode expire "
                "refresh tokens after 7 days.)"
            )
        raise GoogleAPIError(response.status_code, response.text, GOOGLE_TOKEN_URL)

    body = response.json()
    _access_token = body["access_token"]
    _token_expires_at = time.time() + int(body.get("expires_in", 3600))
    return _access_token


async def google_request(
    method: str,
    path: str,
    params: Optional[dict[str, Any]] = None,
    json_body: Optional[dict[str, Any]] = None,
    raw: bool = False,
) -> Any:
    """Single Drive API call (path is relative to the v3 base). Raises
    GoogleAPIError on non-2xx; retries once on 401 with a fresh access token.
    raw=True returns response text (for file exports) instead of parsed JSON."""
    response: Optional[httpx.Response] = None
    for attempt in range(2):
        token = await _get_access_token(force=attempt > 0)
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.request(
                method,
                GOOGLE_DRIVE_API + path,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code != 401:
            break
    assert response is not None
    if response.status_code >= 400:
        raise GoogleAPIError(response.status_code, response.text, str(response.url))
    if raw:
        return response.text
    if not response.content:
        return None
    return response.json()


async def connected_account_email() -> str:
    """Email of the Google account the server is acting as ('' if unavailable)."""
    try:
        about = await google_request("GET", "/about", params={"fields": "user(emailAddress)"})
        return (about.get("user") or {}).get("emailAddress", "")
    except (GoogleConfigError, GoogleAPIError, httpx.HTTPError):
        return ""


_DOC_ID_PATTERNS = [
    # https://docs.google.com/document/d/<id>/edit, /file/d/<id>/view, etc.
    re.compile(r"/(?:document|file|spreadsheets|presentation)/(?:u/\d+/)?d/([A-Za-z0-9_-]{10,})"),
    # https://drive.google.com/open?id=<id>
    re.compile(r"[?&]id=([A-Za-z0-9_-]{10,})"),
]


def extract_doc_id(url_or_id: str) -> str:
    """Pull the Drive file ID out of a Google Docs/Drive URL, or pass a bare ID through.
    Raises ValueError when nothing ID-shaped is found (e.g. published /d/e/2PACX links,
    which point at a read-only rendering, not the commentable document)."""
    candidate = url_or_id.strip()
    for pattern in _DOC_ID_PATTERNS:
        match = pattern.search(candidate)
        if match:
            return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", candidate):
        return candidate
    raise ValueError(
        f"Couldn't find a Google Doc ID in {url_or_id!r}. Expected a link like "
        "https://docs.google.com/document/d/<id>/... — note that published "
        "'/d/e/2PACX-...' links are read-only renderings and can't be commented on; "
        "ask the student for the sharing link instead."
    )
