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
    gets = {"n": 0}

    async def fake_request(method, path, params=None, json_body=None, data=None):
        if method == "PUT":
            raise CanvasAPIError(400, "invalid grade", path)
        # First GET is the pre-check (nothing there yet); the second is the
        # post-failure check, by which time Canvas has saved the comment.
        gets["n"] += 1
        return {
            "score": None,
            "grade": None,
            "excused": False,
            "workflow_state": "submitted",
            "submission_comments": [{"comment": "Nice work, Chris."}] if gets["n"] > 1 else [],
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


def test_comment_pinned_to_latest_attempt_with_warning(monkeypatch):
    captured = {}

    async def fake_request(method, path, params=None, json_body=None, data=None):
        if method == "GET":
            return {
                "attempt": 3,
                "submission_history": [
                    {"submitted_at": "2026-09-01T10:00:00Z"},
                    {"submitted_at": "2026-09-03T10:00:00Z"},
                    {"submitted_at": "2026-09-05T10:00:00Z"},
                ],
            }
        captured["json_body"] = json_body
        return {"score": 90.0, "grade": "90", "excused": False, "workflow_state": "graded"}

    monkeypatch.setattr(grading, "canvas_request", fake_request)
    result = asyncio.run(grading.grade_submission(100, 1, 2, score=90, comment="Better."))
    assert captured["json_body"]["comment"] == {"text_comment": "Better.", "attempt": 3}
    assert "submitted 3 times" in result
    assert "attached to attempt 3" in result


def test_single_attempt_has_no_warning_and_no_attempt_field(monkeypatch):
    captured = {}

    async def fake_request(method, path, params=None, json_body=None, data=None):
        if method == "GET":
            return {"attempt": 1, "submission_history": [{"submitted_at": "2026-09-01T10:00:00Z"}]}
        captured["json_body"] = json_body
        return {"score": 90.0, "grade": "90", "excused": False, "workflow_state": "graded"}

    monkeypatch.setattr(grading, "canvas_request", fake_request)
    result = asyncio.run(grading.grade_submission(100, 1, 2, score=90, comment="Good."))
    assert captured["json_body"]["comment"] == {"text_comment": "Good.", "attempt": 1}
    assert "submitted" not in result


def test_attempt_lookup_failure_does_not_block_grading(monkeypatch):
    async def fake_request(method, path, params=None, json_body=None, data=None):
        if method == "GET":
            raise CanvasAPIError(500, "hiccup", path)
        assert "attempt" not in json_body["comment"]
        return {"score": 90.0, "grade": "90", "excused": False, "workflow_state": "graded"}

    monkeypatch.setattr(grading, "canvas_request", fake_request)
    result = asyncio.run(grading.grade_submission(100, 1, 2, score=90, comment="Good."))
    assert result.startswith("Graded user 2")


def test_rubric_comment_pinned_to_latest_attempt(monkeypatch):
    captured = {}

    async def fake_request(method, path, params=None, json_body=None, data=None):
        if method == "GET":
            return {"attempt": 2, "submission_history": [{"submitted_at": "2026-09-01T10:00:00Z"}, {"submitted_at": "2026-09-02T10:00:00Z"}]}
        captured["json_body"] = json_body
        return {"score": 18.0, "grade": "18"}

    monkeypatch.setattr(grading, "canvas_request", fake_request)
    result = asyncio.run(grading.grade_with_rubric(100, 1, 2, {"_1": 18}, comment="Nice."))
    assert captured["json_body"]["comment"]["attempt"] == 2
    assert "submitted 2 times" in result


def test_existing_identical_comment_is_not_reposted(monkeypatch):
    captured = {}

    async def fake_request(method, path, params=None, json_body=None, data=None):
        if method == "GET":
            return {"attempt": 1, "submission_comments": [{"comment": "  Good.  "}]}
        captured["json_body"] = json_body
        return {"score": 90.0, "grade": "90", "excused": False, "workflow_state": "graded"}

    monkeypatch.setattr(grading, "canvas_request", fake_request)
    result = asyncio.run(grading.grade_submission(100, 1, 2, score=90, comment="Good."))
    assert "comment" not in captured["json_body"]
    assert captured["json_body"]["submission"]["posted_grade"] == 90
    assert "NOT posted again" in result


def test_comment_only_call_with_existing_comment_does_nothing(monkeypatch):
    async def fake_request(method, path, params=None, json_body=None, data=None):
        assert method == "GET", "no write should happen"
        return {"attempt": 1, "submission_comments": [{"comment": "Good."}]}

    monkeypatch.setattr(grading, "canvas_request", fake_request)
    result = asyncio.run(grading.grade_submission(100, 1, 2, comment="Good."))
    assert "nothing was posted" in result


def test_rubric_skips_existing_comment(monkeypatch):
    captured = {}

    async def fake_request(method, path, params=None, json_body=None, data=None):
        if method == "GET":
            return {"attempt": 1, "submission_comments": [{"comment": "Nice."}]}
        captured["json_body"] = json_body
        return {"score": 18.0, "grade": "18"}

    monkeypatch.setattr(grading, "canvas_request", fake_request)
    result = asyncio.run(grading.grade_with_rubric(100, 1, 2, {"_1": 18}, comment="Nice."))
    assert "comment" not in captured["json_body"]
    assert "NOT posted again" in result


def _bulk_fakes(monkeypatch, existing_by_user, progress_states):
    captured = {}
    polls = iter(progress_states)

    async def fake_paginated(path, params=None, max_pages=20):
        return [
            {"user_id": uid, "submission_comments": [{"comment": c} for c in comments]}
            for uid, comments in existing_by_user.items()
        ]

    async def fake_request(method, path, params=None, json_body=None, data=None):
        if method == "POST":
            captured["grade_data"] = json_body["grade_data"]
            return {"url": "https://canvas.test/api/v1/progress/1"}
        nxt = next(polls)
        if isinstance(nxt, Exception):
            raise nxt
        return {"workflow_state": nxt, "message": ""}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(grading, "canvas_paginated", fake_paginated)
    monkeypatch.setattr(grading, "canvas_request", fake_request)
    monkeypatch.setattr(grading.asyncio, "sleep", no_sleep)
    return captured


def test_bulk_skips_comment_for_students_who_already_have_it(monkeypatch):
    captured = _bulk_fakes(monkeypatch, {7: ["Great job."], 8: ["Different note"]}, ["completed"])
    result = asyncio.run(
        grading.bulk_grade_submissions(100, 1, {"7": 10, "8": 9, "9": 8}, comment="Great job.")
    )
    gd = captured["grade_data"]
    assert "text_comment" not in gd["7"]
    assert gd["8"]["text_comment"] == "Great job."
    assert gd["9"]["text_comment"] == "Great job."
    assert gd["7"]["posted_grade"] == 10
    assert "Comment skipped for 1 student(s)" in result and "7" in result
    assert "completed" in result


def test_bulk_without_comment_does_not_read_submissions(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("should not list submissions when there is no comment")

    captured = _bulk_fakes(monkeypatch, {}, ["completed"])
    monkeypatch.setattr(grading, "canvas_paginated", boom)
    asyncio.run(grading.bulk_grade_submissions(100, 1, {"7": 10}))
    assert captured["grade_data"] == {"7": {"posted_grade": 10}}


def test_bulk_still_running_tells_caller_not_to_repeat(monkeypatch):
    _bulk_fakes(monkeypatch, {}, ["queued"] * 20)
    result = asyncio.run(grading.bulk_grade_submissions(100, 1, {"7": 10}, comment="Hi"))
    assert "still running" in result
    assert "Do NOT call bulk_grade_submissions again" in result


def test_bulk_progress_read_failure_is_not_a_grading_failure(monkeypatch):
    _bulk_fakes(monkeypatch, {}, [CanvasAPIError(502, "bad gateway", "/progress/1")])
    result = asyncio.run(grading.bulk_grade_submissions(100, 1, {"7": 10}, comment="Hi"))
    assert "accepted by Canvas" in result
    assert "Do NOT call bulk_grade_submissions again" in result


def test_bulk_dedupe_lookup_failure_still_grades_with_warning(monkeypatch):
    captured = _bulk_fakes(monkeypatch, {}, ["completed"])

    async def failing_paginated(path, params=None, max_pages=20):
        raise CanvasAPIError(500, "hiccup", path)

    monkeypatch.setattr(grading, "canvas_paginated", failing_paginated)
    result = asyncio.run(grading.bulk_grade_submissions(100, 1, {"7": 10}, comment="Hi"))
    assert captured["grade_data"]["7"]["text_comment"] == "Hi"
    assert "no duplicate check" in result
