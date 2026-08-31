import asyncio
import urllib.parse

import pytest

from canvas_mcp_lite import google_client, google_oauth_flow
from canvas_mcp_lite.google_client import GoogleConfigError
from canvas_mcp_lite.tools import google_docs


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    google_oauth_flow._pending_states.clear()
    monkeypatch.setattr(google_client, "_runtime_refresh_token", None)
    yield
    google_oauth_flow._pending_states.clear()


@pytest.fixture
def configured_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "example.up.railway.app")


def test_begin_auth_builds_url_and_state(configured_env):
    url = google_oauth_flow.begin_auth()
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert params["redirect_uri"] == ["https://example.up.railway.app/oauth/google/callback"]
    assert params["access_type"] == ["offline"]
    assert params["state"][0] in google_oauth_flow._pending_states


def test_begin_auth_without_public_url_points_to_cli(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    with pytest.raises(GoogleConfigError, match="canvas-mcp-google-auth"):
        google_oauth_flow.begin_auth()


def test_callback_rejects_unknown_state(configured_env):
    status, page = asyncio.run(
        google_oauth_flow.handle_callback({"state": "bogus", "code": "abc"})
    )
    assert status == 400
    assert "connect_google_docs" in page


def test_callback_success_activates_runtime_token(configured_env, monkeypatch):
    async def fake_exchange(code):
        assert code == "authcode"
        return {"refresh_token": "1//refresh", "access_token": "at"}

    async def fake_account(access_token):
        return "Hayley Lawson <hlawson3@charlotte.edu>"

    monkeypatch.setattr(google_oauth_flow, "_exchange_code", fake_exchange)
    monkeypatch.setattr(google_oauth_flow, "_fetch_account", fake_account)

    url = google_oauth_flow.begin_auth()
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    status, page = asyncio.run(
        google_oauth_flow.handle_callback({"state": state, "code": "authcode"})
    )
    assert status == 200
    assert "hlawson3@charlotte.edu" in page
    assert "GOOGLE_OAUTH_REFRESH_TOKEN=1//refresh" in page
    # runtime token now satisfies _credentials even with no env refresh token
    monkeypatch.delenv("GOOGLE_OAUTH_REFRESH_TOKEN", raising=False)
    assert google_client._credentials()[2] == "1//refresh"
    # state is single-use
    status2, _ = asyncio.run(
        google_oauth_flow.handle_callback({"state": state, "code": "authcode"})
    )
    assert status2 == 400


def test_connect_tool_returns_link(configured_env):
    result = asyncio.run(google_docs.connect_google_docs())
    assert "accounts.google.com" in result
    assert "instructor" in result.lower()


def test_status_tool_reports_missing_client(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    result = asyncio.run(google_docs.google_docs_status())
    assert "MISSING" in result
    assert "Web application" in result


def test_status_tool_reports_connected(configured_env, monkeypatch):
    async def fake_email():
        return "hlawson3@charlotte.edu"

    monkeypatch.setattr(google_docs, "connected_account_email", fake_email)
    result = asyncio.run(google_docs.google_docs_status())
    assert "hlawson3@charlotte.edu" in result
    assert "ready" in result.lower()
