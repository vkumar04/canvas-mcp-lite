import asyncio
import json
from pathlib import Path

import pytest

from canvas_mcp_lite.client import CanvasAPIError
from canvas_mcp_lite.tools import new_quizzes

FIXTURES = Path(__file__).parent / "fixtures"
REPORT = json.loads((FIXTURES / "new_quiz_item_analysis.json").read_text())
ITEMS = json.loads((FIXTURES / "new_quiz_items.json").read_text())


@pytest.fixture(autouse=True)
def fake_course_id(monkeypatch):
    async def fake(identifier):
        return 100

    monkeypatch.setattr(new_quizzes, "get_course_id", fake)


def test_quiz_api_uses_site_root(monkeypatch):
    monkeypatch.setattr(new_quizzes, "CANVAS_API_URL", "https://school.instructure.com/api/v1")
    assert new_quizzes._quiz_api("/courses/1/quizzes") == "https://school.instructure.com/api/quiz/v1/courses/1/quizzes"


def test_format_orders_by_item_position_and_ranks_most_missed():
    out = new_quizzes.format_item_analysis(REPORT, ITEMS, "Unit Quiz", 2742718)
    # Report is [true-false, choice, matching]; positions say choice=2, matching=3, tf=4.
    per_q = out.split("PER-QUESTION STATISTICS:")[1]
    assert per_q.index("Q2 [choice]") < per_q.index("Q3 [matching]") < per_q.index("Q4 [true-false]")
    ranked = out.split("MOST MISSED")[1].split("PER-QUESTION")[0]
    lines = [l for l in ranked.splitlines() if l.strip().startswith("Q")]
    # matching 73% incorrect, true-false 59%, choice 23%
    assert lines[0].startswith("  Q3: 73% incorrect (16/22)")
    assert lines[1].startswith("  Q4: 59% incorrect")
    assert lines[2].startswith("  Q2: 23% incorrect")
    assert "Students analyzed: 22" in out


def test_format_shows_choice_distribution_and_matching_prompts():
    out = new_quizzes.format_item_analysis(REPORT, ITEMS, "Unit Quiz", 1)
    assert "✓  17 (77%)  Mercury is the closest planet to the sun." in out
    assert "Difficulty index: 0.27 (HARD)" in out
    assert "Triangle: 14 chose the correct match; most common wrong pick: \"The distance around a shape.\" (7)" in out
    assert "Perimeter: 22 chose the correct match" in out
    assert "&nbsp;" not in out and "<p>" not in out


def test_format_without_items_falls_back_to_report_order():
    out = new_quizzes.format_item_analysis(REPORT, [], "Quiz", 1)
    per_q = out.split("PER-QUESTION STATISTICS:")[1]
    assert per_q.index("Q1 [true-false]") < per_q.index("Q2 [choice]") < per_q.index("Q3 [matching]")


def test_item_analysis_end_to_end(monkeypatch):
    calls = []

    async def fake_request(method, path, params=None, json_body=None, data=None):
        calls.append((method, path, json_body))
        if path.endswith("/quizzes/55"):
            return {"id": "55", "title": "Unit Quiz"}
        if path.endswith("/reports"):
            assert json_body == {"quiz_report": {"report_type": "item_analysis", "format": "json"}}
            return {"progress": {"id": "9", "workflow_state": "queued", "url": "x"}}
        if path == "/progress/9":
            return {"workflow_state": "completed", "results": {"url": "https://files.example/report.json"}}
        raise AssertionError(path)

    async def fake_paginated(path, params=None, max_pages=20):
        assert path.endswith("/quizzes/55/items")
        return ITEMS

    class FakeResponse:
        status_code = 200

        def json(self):
            return REPORT

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            assert url == "https://files.example/report.json"
            return FakeResponse()

    async def no_sleep(_):
        pass

    monkeypatch.setattr(new_quizzes, "canvas_request", fake_request)
    monkeypatch.setattr(new_quizzes, "canvas_paginated", fake_paginated)
    monkeypatch.setattr(new_quizzes.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(new_quizzes.asyncio, "sleep", no_sleep)

    out = asyncio.run(new_quizzes.get_new_quiz_item_analysis(100, 55))
    assert out.startswith("Item analysis: Unit Quiz (assignment_id=55)")
    assert any(p.endswith("/api/quiz/v1/courses/100/quizzes/55/reports") for _, p, _ in calls)


def test_item_analysis_reports_conflict_and_failure(monkeypatch):
    async def conflict(method, path, params=None, json_body=None, data=None):
        if path.endswith("/quizzes/55"):
            return {"title": "Unit Quiz"}
        raise CanvasAPIError(409, "already running", path)

    async def fake_paginated(path, params=None, max_pages=20):
        return []

    monkeypatch.setattr(new_quizzes, "canvas_request", conflict)
    monkeypatch.setattr(new_quizzes, "canvas_paginated", fake_paginated)
    out = asyncio.run(new_quizzes.get_new_quiz_item_analysis(100, 55))
    assert "already generating" in out

    async def failed(method, path, params=None, json_body=None, data=None):
        if path.endswith("/quizzes/55"):
            return {"title": "Unit Quiz"}
        if path.endswith("/reports"):
            return {"progress": {"id": "9", "workflow_state": "failed", "message": "boom"}}
        raise AssertionError(path)

    monkeypatch.setattr(new_quizzes, "canvas_request", failed)
    out = asyncio.run(new_quizzes.get_new_quiz_item_analysis(100, 55))
    assert "failed to generate" in out and "boom" in out


def test_not_a_new_quiz(monkeypatch):
    async def missing(method, path, params=None, json_body=None, data=None):
        raise CanvasAPIError(404, "nope", path)

    monkeypatch.setattr(new_quizzes, "canvas_request", missing)
    out = asyncio.run(new_quizzes.get_new_quiz_item_analysis(100, 55))
    assert "isn't a New Quiz" in out
