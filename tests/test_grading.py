import asyncio

import pytest

from canvas_mcp_lite.client import CanvasAPIError
from canvas_mcp_lite.tools import grading


@pytest.fixture(autouse=True)
def fake_course_id(monkeypatch):
    async def fake(identifier):
        return 100

    monkeypatch.setattr(grading, "get_course_id", fake)


def test_string_grade_used_as_posted_grade(monkeypatch):
    captured = {}

    async def fake_request(method, path, params=None, json_body=None, data=None):
        captured["method"] = method
        captured["json_body"] = json_body
        return {"score": 100.0, "grade": "complete", "excused": False, "workflow_state": "graded"}

    monkeypatch.setattr(grading, "canvas_request", fake_request)
    result = asyncio.run(grading.grade_submission(100, 1, 2, grade="complete"))
    assert captured["json_body"]["submission"]["posted_grade"] == "complete"
    assert "grade=complete" in result


def test_numeric_score_still_works(monkeypatch):
    captured = {}

    async def fake_request(method, path, params=None, json_body=None, data=None):
        captured["json_body"] = json_body
        return {"score": 95.0, "grade": "95", "excused": False, "workflow_state": "graded"}

    monkeypatch.setattr(grading, "canvas_request", fake_request)
    asyncio.run(grading.grade_submission(100, 1, 2, score=95))
    assert captured["json_body"]["submission"]["posted_grade"] == 95


def test_partial_success_detects_saved_comment(monkeypatch):
    async def fake_request(method, path, params=None, json_body=None, data=None):
        if method == "PUT":
            raise CanvasAPIError(400, "invalid grade", path)
        return {
            "score": None,
            "grade": None,
            "excused": False,
            "workflow_state": "submitted",
            "submission_comments": [{"comment": "Nice work, Chris."}],
        }

    monkeypatch.setattr(grading, "canvas_request", fake_request)
    result = asyncio.run(
        grading.grade_submission(100, 1, 2, score=100, comment="Nice work, Chris.")
    )
    assert "Grading call failed" in result
    assert "WAS saved" in result
    assert "do NOT resend" in result


def test_failure_with_unsaved_comment(monkeypatch):
    async def fake_request(method, path, params=None, json_body=None, data=None):
        if method == "PUT":
            raise CanvasAPIError(400, "invalid grade", path)
        return {"score": None, "grade": None, "excused": False,
                "workflow_state": "submitted", "submission_comments": []}

    monkeypatch.setattr(grading, "canvas_request", fake_request)
    result = asyncio.run(grading.grade_submission(100, 1, 2, score=100, comment="Hello"))
    assert "NOT saved" in result


def test_nothing_to_do():
    result = asyncio.run(grading.grade_submission(100, 1, 2))
    assert "Nothing to do" in result


def test_post_grades_mutation(monkeypatch):
    captured = {}

    async def fake_graphql(query, variables=None):
        captured["query"] = query
        captured["variables"] = variables
        return {"postAssignmentGrades": {"progress": {"_id": "1", "state": "queued"}}}

    monkeypatch.setattr(grading, "canvas_graphql", fake_graphql)
    result = asyncio.run(grading.post_grades(100, 2971237))
    assert captured["variables"] == {"assignmentId": "2971237", "gradedOnly": True}
    assert "postAssignmentGrades" in captured["query"]
    assert "queued" in result
