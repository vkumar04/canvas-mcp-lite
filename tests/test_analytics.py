import asyncio

import pytest

from canvas_mcp_lite.client import CanvasAPIError
from canvas_mcp_lite.tools import analytics


@pytest.fixture(autouse=True)
def fake_course_id(monkeypatch):
    async def fake(identifier):
        return 100

    monkeypatch.setattr(analytics, "get_course_id", fake)


def _assignment(aid, name, due, points=10, published=True):
    return {"id": aid, "name": name, "due_at": due, "points_possible": points, "published": published}


SUBS = [
    {"assignment_id": 3, "assignment": _assignment(3, "Draft 2", "2026-09-10T04:00:00Z"),
     "workflow_state": "graded", "score": 0, "missing": True, "submitted_at": None},
    {"assignment_id": 1, "assignment": _assignment(1, "Draft 1", "2026-09-01T04:00:00Z"),
     "workflow_state": "graded", "score": 8.5, "late": True, "submitted_at": "2026-09-02T01:00:00Z",
     "graded_at": "2026-09-03T01:00:00Z", "attempt": 2},
    {"assignment_id": 2, "assignment": _assignment(2, "Reading Quiz", "2026-09-05T04:00:00Z"),
     "workflow_state": "submitted", "score": None, "submitted_at": "2026-09-05T01:00:00Z"},
    {"assignment_id": 4, "assignment": _assignment(4, "Final", "2099-12-01T04:00:00Z"),
     "workflow_state": "unsubmitted", "score": None},
    {"assignment_id": 5, "assignment": _assignment(5, "Secret", "2026-09-08T04:00:00Z", published=False),
     "workflow_state": "unsubmitted", "score": None},
    {"assignment_id": 6, "assignment": _assignment(6, "Peer Review", "2026-09-07T04:00:00Z"),
     "workflow_state": "unsubmitted", "score": None, "excused": True},
    {"assignment_id": 7, "assignment": _assignment(7, "Journal 1", "2026-09-03T04:00:00Z"),
     "workflow_state": "unsubmitted", "score": None, "missing": True},
]

ENROLLMENT = {
    "user": {"name": "Sam Student"},
    "grades": {"current_grade": "C", "current_score": 72.5, "final_grade": "F", "final_score": 41.0},
    "last_activity_at": "2026-09-11T14:00:00Z",
    "total_activity_time": 7200,
    "enrollment_state": "active",
}


def _install(monkeypatch, other_enrollments=None, enrollment_error=None):
    async def fake_paginated(path, params=None, max_pages=20):
        if path.endswith("/students/submissions"):
            assert params["student_ids[]"] == "42"
            return list(SUBS)
        if path == "/courses/100/enrollments":
            if enrollment_error:
                raise enrollment_error
            assert params["user_id"] == "42"
            return [ENROLLMENT]
        if path == "/users/42/enrollments":
            if isinstance(other_enrollments, Exception):
                raise other_enrollments
            return list(other_enrollments or [])
        raise AssertionError(path)

    async def fake_request(method, path, params=None, json_body=None, data=None):
        assert method == "GET"
        cid = int(path.rsplit("/", 1)[1])
        if cid == 300:
            raise CanvasAPIError(403, "nope", path)
        return {"course_code": f"ENGL-{cid}", "name": "Writing"}

    monkeypatch.setattr(analytics, "canvas_paginated", fake_paginated)
    monkeypatch.setattr(analytics, "canvas_request", fake_request)


def test_student_grades_report(monkeypatch):
    _install(monkeypatch)
    out = asyncio.run(analytics.get_student_grades(100, 42))
    assert out.startswith("Grade record for Sam Student (user_id 42) in course 100")
    assert "Current grade: C (72.5%)" in out and "Final if missing work stays zero: F (41%)" in out
    assert "Total activity time: 2.0 h" in out
    # summary counts
    assert "6 published assignments: 1 graded above zero, 1 zero(s), 1 submitted awaiting grade, 1 missing, 1 excused, 1 not yet due" in out
    assert "1 unpublished assignment(s) hidden" in out
    # per-row statuses
    assert "- Draft 1 (due 2026-09-01 04:00 UTC): 8.5/10 (late) [submitted 2026-09-02 01:00 UTC; graded 2026-09-03 01:00 UTC; 2 attempts]" in out
    assert "- Draft 2 (due 2026-09-10 04:00 UTC): 0/10 — marked missing" in out
    assert "- Reading Quiz (due 2026-09-05 04:00 UTC): submitted, not yet graded" in out
    assert "- Journal 1 (due 2026-09-03 04:00 UTC): MISSING" in out
    assert "- Peer Review (due 2026-09-07 04:00 UTC): excused" in out
    assert "- Final (due 2099-12-01 04:00 UTC): not yet due" in out
    assert "Secret" not in out
    # due-date order
    assert out.index("- Draft 1") < out.index("- Journal 1") < out.index("- Reading Quiz") < out.index("- Draft 2") < out.index("- Final (due")
    assert "Other courses" not in out


def test_other_courses_listed_and_current_course_skipped(monkeypatch):
    _install(monkeypatch, other_enrollments=[
        {"course_id": 100, "grades": {"current_score": 72.5}},
        {"course_id": 200, "grades": {"current_grade": "B", "current_score": 85, "final_score": 80},
         "last_activity_at": "2026-09-12T10:00:00Z"},
        {"course_id": 300, "grades": {"current_score": None, "final_score": None}},
    ])
    out = asyncio.run(analytics.get_student_grades(100, 42, include_other_courses=True))
    assert "Other courses" in out
    assert "- ENGL-200 Writing (id 200): current B (85%), final-if-zeros 80%, last activity 2026-09-12 10:00 UTC" in out
    assert "- course 300 (id 300): current — (?%)" in out  # name lookup forbidden, still listed
    assert out.count("(id 100)") == 0


def test_other_courses_permission_error_is_reported_not_raised(monkeypatch):
    _install(monkeypatch, other_enrollments=CanvasAPIError(401, "unauthorized", "/users/42/enrollments"))
    out = asyncio.run(analytics.get_student_grades(100, 42, include_other_courses=True))
    assert "unavailable (Canvas returned 401" in out
    assert "Grade record for Sam Student" in out


def test_enrollment_lookup_failure_still_lists_assignments(monkeypatch):
    _install(monkeypatch, enrollment_error=CanvasAPIError(500, "boom", "/courses/100/enrollments"))
    out = asyncio.run(analytics.get_student_grades(100, 42))
    assert "Grade record for user 42" in out
    assert "enrollment lookup failed: 500" in out
    assert "- Draft 1 (due" in out


def test_pts_formatting():
    assert analytics._pts(10.0) == "10"
    assert analytics._pts(8.5) == "8.5"
    assert analytics._pts(None) == "?"
    assert analytics._pts("A") == "A"
