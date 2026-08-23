import asyncio

import pytest

from canvas_mcp_lite import util


@pytest.fixture(autouse=True)
def reset_cache():
    util._code_to_id.clear()
    util._cache_loaded_at = 0.0
    yield
    util._code_to_id.clear()
    util._cache_loaded_at = 0.0


def test_numeric_identifiers_pass_through():
    assert asyncio.run(util.get_course_id(264948)) == 264948
    assert asyncio.run(util.get_course_id("264948")) == 264948


def test_course_code_resolves_and_caches(monkeypatch):
    calls = {"n": 0}

    async def fake_paginated(path, params=None):
        calls["n"] += 1
        return [{"course_code": "WRDS-1103", "id": 42}]

    monkeypatch.setattr(util, "canvas_paginated", fake_paginated)
    assert asyncio.run(util.get_course_id("WRDS-1103")) == 42
    assert asyncio.run(util.get_course_id("WRDS-1103")) == 42
    assert calls["n"] == 1  # second call served from cache


def test_unknown_code_raises(monkeypatch):
    async def fake_paginated(path, params=None):
        return []

    monkeypatch.setattr(util, "canvas_paginated", fake_paginated)
    with pytest.raises(ValueError):
        asyncio.run(util.get_course_id("NO-SUCH-COURSE"))


def test_format_date():
    assert util.format_date(None) == "N/A"
    assert util.format_date("2026-08-20T19:40:00Z") == "2026-08-20 19:40 UTC"
    assert util.format_date("garbage") == "garbage"


def test_announcement_posting_status_delayed_future():
    topic = {"delayed_post_at": "2099-01-01T00:00:00Z", "posted_at": "2026-01-01T00:00:00Z"}
    assert "not yet visible" in util.announcement_posting_status(topic)


def test_announcement_posting_status_posted():
    topic = {"delayed_post_at": None, "posted_at": "2026-01-01T00:00:00Z"}
    assert util.announcement_posting_status(topic).startswith("Posted:")
