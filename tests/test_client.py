import asyncio

import httpx
import pytest

from canvas_mcp_lite import client


def make_mock_client(handler):
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://canvas.test/api/v1",
    )


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    monkeypatch.setattr(client, "BACKOFF_BASE_SECONDS", 0)


def test_paginated_follows_link_header(monkeypatch):
    def handler(request):
        if request.url.path.endswith("/page2"):
            return httpx.Response(200, json=[{"id": 3}])
        return httpx.Response(
            200,
            json=[{"id": 1}, {"id": 2}],
            headers={"Link": '<https://canvas.test/api/v1/page2>; rel="next"'},
        )

    monkeypatch.setattr(client, "_client", lambda: make_mock_client(handler))
    results = asyncio.run(client.canvas_paginated("/courses"))
    assert [r["id"] for r in results] == [1, 2, 3]
    assert results.truncated is False


def test_paginated_stops_at_max_pages(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json=[{"id": 1}],
            headers={"Link": '<https://canvas.test/api/v1/more>; rel="next"'},
        )

    monkeypatch.setattr(client, "_client", lambda: make_mock_client(handler))
    results = asyncio.run(client.canvas_paginated("/courses", max_pages=3))
    assert len(results) == 3
    assert results.truncated is True
    assert "truncated" in client.truncation_note(results)


def test_retry_on_500_then_success(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, text="Internal Server Error")
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(client, "_client", lambda: make_mock_client(handler))
    result = asyncio.run(client.canvas_request("GET", "/courses/1"))
    assert result == {"ok": True}
    assert calls["n"] == 3


def test_retry_on_rate_limit_403(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(403, text="403 Forbidden (Rate Limit Exceeded)")
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(client, "_client", lambda: make_mock_client(handler))
    result = asyncio.run(client.canvas_request("GET", "/courses/1"))
    assert result == {"ok": True}
    assert calls["n"] == 2


def test_real_403_is_not_retried(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(403, text="user not authorized to perform that action")

    monkeypatch.setattr(client, "_client", lambda: make_mock_client(handler))
    with pytest.raises(client.CanvasAPIError) as exc_info:
        asyncio.run(client.canvas_request("GET", "/courses/1"))
    assert exc_info.value.status_code == 403
    assert calls["n"] == 1


def test_error_includes_body_and_url(monkeypatch):
    def handler(request):
        return httpx.Response(400, json={"errors": [{"message": "invalid grade"}]})

    monkeypatch.setattr(client, "_client", lambda: make_mock_client(handler))
    with pytest.raises(client.CanvasAPIError) as exc_info:
        asyncio.run(client.canvas_request("PUT", "/courses/1/assignments/2"))
    assert "invalid grade" in str(exc_info.value)


def test_write_is_not_retried_on_500(monkeypatch):
    """A 5xx on PUT/POST is ambiguous — Canvas may have saved the comment already."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500, text="Internal Server Error")

    monkeypatch.setattr(client, "_client", lambda: make_mock_client(handler))
    with pytest.raises(client.CanvasAPIError):
        asyncio.run(client.canvas_request("PUT", "/courses/1/assignments/2/submissions/3", json_body={}))
    assert calls["n"] == 1


def test_write_is_not_retried_on_read_timeout(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setattr(client, "_client", lambda: make_mock_client(handler))
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(client.canvas_request("POST", "/courses/1/assignments/2/submissions/update_grades", json_body={}))
    assert calls["n"] == 1


def test_write_is_retried_on_connect_error(monkeypatch):
    """The request never reached Canvas, so a retry cannot duplicate anything."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(client, "_client", lambda: make_mock_client(handler))
    result = asyncio.run(client.canvas_request("PUT", "/courses/1", json_body={}))
    assert result == {"ok": True}
    assert calls["n"] == 2


def test_write_is_retried_on_rate_limit(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(403, text="403 Forbidden (Rate Limit Exceeded)")
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(client, "_client", lambda: make_mock_client(handler))
    result = asyncio.run(client.canvas_request("POST", "/courses/1", json_body={}))
    assert result == {"ok": True}
    assert calls["n"] == 2


def test_read_still_retried_on_timeout(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(client, "_client", lambda: make_mock_client(handler))
    assert asyncio.run(client.canvas_request("GET", "/courses/1")) == {"ok": True}
    assert calls["n"] == 2


def test_graphql_post_is_retried_on_500(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="oops")
        return httpx.Response(200, json={"data": {"ok": True}})

    monkeypatch.setattr(client, "_client", lambda: make_mock_client(handler))
    monkeypatch.setattr(client, "CANVAS_API_URL", "https://canvas.test/api/v1")
    monkeypatch.setattr(client, "CANVAS_API_TOKEN", "t")
    assert asyncio.run(client.canvas_graphql("mutation { x }")) == {"ok": True}
    assert calls["n"] == 2
