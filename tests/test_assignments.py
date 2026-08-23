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
