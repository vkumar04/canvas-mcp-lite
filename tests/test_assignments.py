import asyncio

import pytest

from canvas_mcp_lite.tools import assignments


@pytest.fixture(autouse=True)
def fake_course_id(monkeypatch):
    async def fake(identifier):
        return 100

    monkeypatch.setattr(assignments, "get_course_id", fake)


def test_submission_content_includes_comments(monkeypatch):
    async def fake_request(method, path, params=None, json_body=None, data=None):
        assert "submission_comments" in params["include[]"]
        return {
            "submission_type": "online_text_entry",
            "body": "My essay text.",
            "submitted_at": "2026-08-20T19:40:00Z",
            "attempt": 1,
            "submission_comments": [
                {
                    "author_name": "Chris Mintz",
                    "comment": "https://docs.google.com/document/d/abc/edit",
                    "created_at": "2026-08-20T19:41:00Z",
                }
            ],
        }

    monkeypatch.setattr(assignments, "canvas_request", fake_request)
    result = asyncio.run(assignments.get_submission_content(100, 1, 2))
    assert "My essay text." in result
    assert "Submission comments:" in result
    assert "docs.google.com" in result


def test_submission_content_no_submission_still_shows_comments(monkeypatch):
    async def fake_request(method, path, params=None, json_body=None, data=None):
        return {
            "submission_type": None,
            "submitted_at": None,
            "attempt": None,
            "submission_comments": [
                {"author_name": "Student", "comment": "I emailed you my draft",
                 "created_at": "2026-08-20T19:41:00Z"}
            ],
        }

    monkeypatch.setattr(assignments, "canvas_request", fake_request)
    result = asyncio.run(assignments.get_submission_content(100, 1, 2))
    assert "Nothing submitted yet." in result
    assert "I emailed you my draft" in result


def test_ungraded_queue_groups_by_assignment(monkeypatch):
    async def fake_paginated(path, params=None, max_pages=20):
        if path.endswith("/assignments"):
            return [
                {"id": 1, "name": "Essay 1", "needs_grading_count": 1, "due_at": None},
                {"id": 2, "name": "Essay 2", "needs_grading_count": 0, "due_at": None},
            ]
        return [
            {
                "workflow_state": "submitted",
                "user_id": 7,
                "user": {"name": "Chris Mintz"},
                "submitted_at": "2026-08-20T19:40:00Z",
                "late": False,
            }
        ]

    monkeypatch.setattr(assignments, "canvas_paginated", fake_paginated)
    result = asyncio.run(assignments.list_ungraded_submissions(100))
    assert "Essay 1" in result
    assert "Essay 2" not in result
    assert "Chris Mintz" in result


def test_missing_submissions_filters_and_groups(monkeypatch):
    async def fake_paginated(path, params=None, max_pages=20):
        return [
            {
                "missing": True,
                "assignment_id": 1,
                "assignment": {"name": "Essay 1"},
                "user": {"name": "A Student"},
                "user_id": 7,
            },
            {
                "missing": False,
                "assignment_id": 1,
                "assignment": {"name": "Essay 1"},
                "user": {"name": "B Student"},
                "user_id": 8,
            },
        ]

    monkeypatch.setattr(assignments, "canvas_paginated", fake_paginated)
    result = asyncio.run(assignments.list_missing_submissions(100))
    assert "A Student" in result
    assert "B Student" not in result


def _capture_put(monkeypatch):
    calls = []

    async def fake_request(method, path, params=None, json_body=None, data=None):
        calls.append((method, path, json_body))
        return {"name": "Exit Ticket", "due_at": "2026-09-25T20:00:00Z",
                "unlock_at": None, "lock_at": "2026-09-25T21:00:00Z", "published": True}

    monkeypatch.setattr(assignments, "canvas_request", fake_request)
    return calls


def test_update_assignment_sends_availability_window(monkeypatch):
    calls = _capture_put(monkeypatch)
    out = asyncio.run(assignments.update_assignment(
        100, 7, due_at="2026-09-25T20:00:00Z", lock_at="2026-09-25T21:00:00Z"))
    assert calls == [("PUT", "/courses/100/assignments/7",
                      {"assignment": {"due_at": "2026-09-25T20:00:00Z", "lock_at": "2026-09-25T21:00:00Z"}})]
    assert "available" in out and "2026-09-25 21:00 UTC" in out


def test_update_assignment_empty_string_clears_date(monkeypatch):
    calls = _capture_put(monkeypatch)
    asyncio.run(assignments.update_assignment(100, 7, lock_at="", unlock_at=" "))
    assert calls[0][2] == {"assignment": {"lock_at": None, "unlock_at": None}}


def test_create_assignment_sends_availability_window(monkeypatch):
    calls = _capture_put(monkeypatch)
    asyncio.run(assignments.create_assignment(
        100, "Quiz", unlock_at="2026-10-01T00:00:00Z", lock_at="2026-10-02T00:00:00Z"))
    body = calls[0][2]["assignment"]
    assert body["unlock_at"] == "2026-10-01T00:00:00Z" and body["lock_at"] == "2026-10-02T00:00:00Z"
    assert "due_at" not in body


def test_assignment_details_shows_availability(monkeypatch):
    _capture_put(monkeypatch)
    out = asyncio.run(assignments.get_assignment_details(100, 7))
    assert "Available: " in out and "→ 2026-09-25 21:00 UTC" in out
