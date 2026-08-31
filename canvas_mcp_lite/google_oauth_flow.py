"""Chat-driven Google account connection for remote (HTTP) deployments.

The connect_google_docs tool hands the instructor a Google sign-in link whose
redirect lands on this server's public /oauth/google/callback route. The
callback exchanges the code, activates the refresh token in-process
immediately, and shows the value to save in the server's environment so the
connection survives restarts. No terminal needed anywhere.

Requires a **Web application** OAuth client (the server admin creates it once
at console.cloud.google.com) with this server's callback URL registered as an
authorized redirect URI.
"""

from __future__ import annotations

import html
import os
import secrets
import time
from typing import Optional

import httpx

from . import google_client
from .google_client import (
    GOOGLE_AUTH_URL,
    GOOGLE_DRIVE_API,
    GOOGLE_DRIVE_SCOPE,
    GOOGLE_TOKEN_URL,
    GoogleConfigError,
)

CALLBACK_PATH = "/oauth/google/callback"
STATE_TTL_SECONDS = 600

# Pending single-use states. Only the connect_google_docs tool (reachable solely
# through the secret MCP path) can mint one, so a stranger who finds the public
# callback URL can't complete a flow and swap in their own Google account.
_pending_states: dict[str, float] = {}


def public_base_url() -> str:
    explicit = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if explicit:
        return explicit
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    return f"https://{domain}" if domain else ""


def _oauth_client() -> tuple[str, str]:
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    if not (client_id and client_secret):
        raise GoogleConfigError(
            "The server admin hasn't set up the Google OAuth client yet. One-time "
            "admin setup at console.cloud.google.com: enable the Google Drive API, "
            "configure the OAuth consent screen (publish it so refresh tokens don't "
            "expire after 7 days), create an OAuth client of type 'Web application' "
            f"with authorized redirect URI {redirect_uri() or '<server URL>' + CALLBACK_PATH}, "
            "then set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in the "
            "server's environment."
        )
    return client_id, client_secret


def redirect_uri() -> str:
    base = public_base_url()
    return base + CALLBACK_PATH if base else ""


def begin_auth() -> str:
    """Mint a single-use state and return the Google sign-in URL for the instructor."""
    client_id, _ = _oauth_client()
    uri = redirect_uri()
    if not uri:
        raise GoogleConfigError(
            "This server has no public URL (PUBLIC_BASE_URL or RAILWAY_PUBLIC_DOMAIN), "
            "so the in-browser flow can't work — it's probably running locally over "
            "stdio. Run `canvas-mcp-google-auth` in a terminal instead."
        )
    now = time.time()
    for state, created in list(_pending_states.items()):
        if now - created > STATE_TTL_SECONDS:
            del _pending_states[state]
    state = secrets.token_urlsafe(24)
    _pending_states[state] = now
    return GOOGLE_AUTH_URL + "?" + httpx.QueryParams(
        {
            "client_id": client_id,
            "redirect_uri": uri,
            "response_type": "code",
            "scope": GOOGLE_DRIVE_SCOPE,
            "access_type": "offline",  # ask for a refresh token,
            "prompt": "consent",  # even if one was issued before
            "state": state,
        }
    ).__str__()


def _page(title: str, body_html: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui;max-width:44rem;margin:4rem auto;"
        "padding:0 1rem;line-height:1.5}code{background:#f0f0f0;padding:.15rem .3rem;"
        "border-radius:4px;word-break:break-all}</style></head>"
        f"<body><h2>{html.escape(title)}</h2>{body_html}</body></html>"
    )


async def _exchange_code(code: str) -> dict:
    client_id, client_secret = _oauth_client()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
    if response.status_code >= 400:
        raise GoogleConfigError(f"Google rejected the token exchange: {response.text}")
    return response.json()


async def _fetch_account(access_token: str) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GOOGLE_DRIVE_API}/about",
            params={"fields": "user(emailAddress,displayName)"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        return ""
    user = response.json().get("user") or {}
    return f"{user.get('displayName', '?')} <{user.get('emailAddress', '?')}>"


async def handle_callback(params: dict[str, str]) -> tuple[int, str]:
    """Process Google's redirect. Returns (http_status, html_page)."""
    if params.get("error"):
        return 400, _page(
            "Google sign-in failed",
            f"<p>Google returned: <code>{html.escape(params['error'])}</code>. "
            "Ask Claude to run connect_google_docs again for a fresh link.</p>",
        )
    state = params.get("state", "")
    created = _pending_states.pop(state, None)
    if created is None or time.time() - created > STATE_TTL_SECONDS:
        return 400, _page(
            "Link expired or invalid",
            "<p>This sign-in link isn't valid (already used, older than 10 minutes, "
            "or the server restarted). Ask Claude to run connect_google_docs again.</p>",
        )
    code = params.get("code", "")
    if not code:
        return 400, _page("Missing authorization code", "<p>Try the link again.</p>")

    try:
        tokens = await _exchange_code(code)
    except GoogleConfigError as exc:
        return 502, _page("Token exchange failed", f"<p><code>{html.escape(str(exc))}</code></p>")

    refresh_token: Optional[str] = tokens.get("refresh_token")
    if not refresh_token:
        return 502, _page(
            "No refresh token returned",
            "<p>Google didn't issue a refresh token. Remove this app's access at "
            "<a href='https://myaccount.google.com/permissions'>myaccount.google.com/permissions</a> "
            "and run connect_google_docs again.</p>",
        )

    account = await _fetch_account(tokens.get("access_token", ""))
    google_client.set_runtime_refresh_token(refresh_token)

    who = f" as <b>{html.escape(account)}</b>" if account else ""
    return 200, _page(
        "Google account connected",
        f"<p>Connected{who}. Google Docs commenting is <b>active now</b> — doc "
        "comments will post from this account. You can close this tab and go back "
        "to Claude.</p>"
        "<p><b>One follow-up so this survives server restarts:</b> add this "
        "variable to the server's environment (e.g. Railway &rarr; Variables) — "
        "or send it privately to whoever manages the server. Treat it like a "
        "password.</p>"
        f"<p><code>GOOGLE_OAUTH_REFRESH_TOKEN={html.escape(refresh_token)}</code></p>",
    )
