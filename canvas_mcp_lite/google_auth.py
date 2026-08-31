"""One-time OAuth setup for the Google Docs grading tools.

The instructor runs `canvas-mcp-google-auth` on their own machine, approves
access in their browser, and gets back the three GOOGLE_OAUTH_* values the
server needs. Nothing here runs on the server or at request time.

Prerequisite (one-time, ~5 minutes at console.cloud.google.com):
  1. Create a project (or reuse one) and enable the "Google Drive API".
  2. OAuth consent screen: External, add yourself as a test user
     (publish the app later to stop refresh tokens expiring after 7 days).
  3. Credentials -> Create credentials -> OAuth client ID -> "Desktop app".
     That gives you the client ID and secret this script asks for.
"""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .google_client import GOOGLE_DRIVE_API, GOOGLE_DRIVE_SCOPE, GOOGLE_TOKEN_URL

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class _CodeCatcher(BaseHTTPRequestHandler):
    """Catches the single OAuth redirect and stashes the query params on the server."""

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self.server.oauth_params = urllib.parse.parse_qs(  # type: ignore[attr-defined]
            urllib.parse.urlparse(self.path).query
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<h2>Google account connected.</h2>"
            b"<p>You can close this tab and return to the terminal.</p>"
        )

    def log_message(self, *args):  # silence per-request stderr noise
        pass


def _get_code_via_local_server(auth_url: str, port: int, state: str) -> str:
    server = HTTPServer(("127.0.0.1", port), _CodeCatcher)
    print("\nOpening your browser for Google sign-in...")
    print("(If it doesn't open, paste this URL into any browser ON THIS machine:)")
    print(f"\n{auth_url}\n")
    webbrowser.open(auth_url)
    server.handle_request()  # blocks until Google redirects back
    params = getattr(server, "oauth_params", {})
    server.server_close()
    if params.get("error"):
        raise SystemExit(f"Google returned an error: {params['error'][0]}")
    if params.get("state", [""])[0] != state:
        raise SystemExit("OAuth state mismatch — aborting (possible interception, just retry).")
    if not params.get("code"):
        raise SystemExit("No authorization code in the redirect — try again.")
    return params["code"][0]


def _get_code_manually(auth_url: str, state: str) -> str:
    print("\nOpen this URL in a browser (any machine) and approve access:")
    print(f"\n{auth_url}\n")
    print(
        "After approving, the browser will try to load a localhost page and fail — "
        "that's expected. Copy the FULL URL from the browser's address bar "
        "(it contains ?code=...) and paste it here."
    )
    pasted = input("\nPaste the redirected URL: ").strip()
    params = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
    if params.get("state", [""])[0] != state:
        raise SystemExit("OAuth state mismatch — make sure you pasted the URL from this run.")
    if not params.get("code"):
        raise SystemExit("No ?code= found in that URL — paste the full address bar contents.")
    return params["code"][0]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _update_env_file(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    lines.extend(f"{key}={value}" for key, value in remaining.items())
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Connect a Google account for the Google Docs grading tools "
        "(run this once, on your own machine)."
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="No browser on this machine: print the auth URL and paste the redirect back.",
    )
    parser.add_argument(
        "--env-file",
        default=str(ENV_PATH),
        help=f"Where to save the credentials (default: {ENV_PATH}).",
    )
    args = parser.parse_args()

    load_dotenv(args.env_file)
    print(__doc__)
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or input("OAuth client ID: ").strip()
    client_secret = (
        os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or input("OAuth client secret: ").strip()
    )
    if not client_id or not client_secret:
        raise SystemExit("Both the client ID and secret are required — see the steps above.")

    port = 8765 if args.manual else _free_port()
    redirect_uri = f"http://localhost:{port}/"
    state = secrets.token_urlsafe(16)
    auth_url = GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_DRIVE_SCOPE,
            "access_type": "offline",  # ask for a refresh token,
            "prompt": "consent",  # even if one was issued before
            "state": state,
        }
    )

    if args.manual:
        code = _get_code_manually(auth_url, state)
    else:
        code = _get_code_via_local_server(auth_url, port, state)

    token_response = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30.0,
    )
    if token_response.status_code >= 400:
        raise SystemExit(f"Token exchange failed: {token_response.text}")
    tokens = token_response.json()
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise SystemExit(
            "Google didn't return a refresh token. Revoke the app at "
            "https://myaccount.google.com/permissions and run this again."
        )

    about = httpx.get(
        f"{GOOGLE_DRIVE_API}/about",
        params={"fields": "user(emailAddress,displayName)"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        timeout=30.0,
    )
    user = (about.json().get("user") or {}) if about.status_code < 400 else {}
    who = f"{user.get('displayName', '?')} <{user.get('emailAddress', '?')}>"
    print(f"\nConnected Google account: {who}")
    print("Doc comments posted by the server will appear as this account.")

    values = {
        "GOOGLE_OAUTH_CLIENT_ID": client_id,
        "GOOGLE_OAUTH_CLIENT_SECRET": client_secret,
        "GOOGLE_OAUTH_REFRESH_TOKEN": refresh_token,
    }
    save = input(f"\nSave these to {args.env_file}? [y/N] ").strip().lower()
    if save == "y":
        _update_env_file(Path(args.env_file), values)
        print("Saved. Restart the MCP server to pick them up.")
    else:
        print("\nAdd these to the server's environment (e.g. Railway → Variables):\n")
        for key, value in values.items():
            print(f"{key}={value}")


if __name__ == "__main__":
    main()
