import asyncio
from collections import Counter

from canvas_mcp_lite.tools import peer_review


def test_rotation_pairs_properties():
    users = list(range(10))
    for k in (1, 2, 3):
        pairs = peer_review._rotation_pairs(users, k)
        assert len(pairs) == len(users) * k
        assert all(reviewer != reviewee for reviewer, reviewee in pairs)
        assert len(set(pairs)) == len(pairs)  # no duplicate pair
        gives = Counter(reviewer for reviewer, _ in pairs)
        gets = Counter(reviewee for _, reviewee in pairs)
        assert all(gives[u] == k and gets[u] == k for u in users)


def test_rotation_pairs_two_students():
    pairs = peer_review._rotation_pairs(["a", "b"], 1)
    assert sorted(pairs) == [("a", "b"), ("b", "a")]


def _fake_submissions():
    return [
        {"id": 11, "user_id": 1, "submitted_at": "2026-09-01T00:00:00Z", "user": {"name": "Alice"}},
        {"id": 12, "user_id": 2, "submitted_at": "2026-09-01T00:00:00Z", "user": {"name": "Bob"}},
        {"id": 13, "user_id": 3, "submitted_at": "2026-09-01T00:00:00Z", "user": {"name": "Cara"}},
        {"id": 14, "user_id": 4, "submitted_at": None, "user": {"name": "Norm NoSubmit"}},
        {"id": 15, "user_id": 5, "submitted_at": "2026-09-01T00:00:00Z", "user": {"name": "Test Student"}},
    ]


def test_random_assignment_only_includes_submitters(monkeypatch):
    posted = []

    async def fake_course_id(identifier):
        return 264948

    async def fake_paginated(path, params=None):
        return _fake_submissions()

    async def fake_request(method, path, params=None, json_body=None, data=None):
        if method == "GET":
            return {"peer_reviews": False}
        if method == "PUT":
            return {}
        posted.append((path, json_body["user_id"]))
        return {"workflow_state": "assigned"}

    monkeypatch.setattr(peer_review, "get_course_id", fake_course_id)
    monkeypatch.setattr(peer_review, "canvas_paginated", fake_paginated)
    monkeypatch.setattr(peer_review, "canvas_request", fake_request)

    result = asyncio.run(peer_review.randomly_assign_peer_reviews(264948, 999))
    assert "Assigned 3 of 3 peer reviews across 3 submitted students" in result
    assert "Enabled peer_reviews" in result
    assert "Norm NoSubmit" not in result
    assert "Test Student" not in result
    # posts target the reviewees' submission ids, never the unsubmitted ones
    assert len(posted) == 3
    assert all("/submissions/1" in p and p.split("/submissions/")[1].split("/")[0] in {"11", "12", "13"} for p, _ in posted)


def test_random_assignment_dry_run_posts_nothing(monkeypatch):
    calls = {"writes": 0}

    async def fake_course_id(identifier):
        return 264948

    async def fake_paginated(path, params=None):
        return _fake_submissions()

    async def fake_request(method, path, **kwargs):
        calls["writes"] += 1
        raise AssertionError("dry_run must not call canvas_request")

    monkeypatch.setattr(peer_review, "get_course_id", fake_course_id)
    monkeypatch.setattr(peer_review, "canvas_paginated", fake_paginated)
    monkeypatch.setattr(peer_review, "canvas_request", fake_request)

    result = asyncio.run(peer_review.randomly_assign_peer_reviews(264948, 999, dry_run=True))
    assert "DRY RUN" in result
    assert calls["writes"] == 0


def test_annotation_text_combines_comment_and_quote():
    assert peer_review._annotation_text({"contents": "Expand this", "text": "my thesis"}) == \
        'Expand this  [on: "my thesis"]'
    assert peer_review._annotation_text({"contents": "Nice", "text": ""}) == "Nice"
    assert peer_review._annotation_text({"contents": "", "text": "sentence"}) == '(marked: "sentence")'
    assert peer_review._annotation_text({"contents": "", "text": ""}) == ""


def test_local_user_id_from_global():
    from canvas_mcp_lite.canvadocs import local_user_id
    assert local_user_id("73010000000416654") == 416654
    assert local_user_id(416654) == 416654
    assert local_user_id(None) is None


def test_get_submission_annotations_groups_by_reviewer(monkeypatch):
    async def fake_course_id(identifier):
        return 264948

    async def fake_annotations(course_id, assignment_id, user_id):
        return [
            {"type": "highlight", "page": 0, "user_name": "Jaimie Eason",
             "user_id": "73010000000416654", "user_role": "student",
             "contents": "Expand on your mom's influence.", "text": None},
            {"type": "point", "page": 1, "user_name": "Jaimie Eason",
             "user_id": "73010000000416654", "user_role": "student",
             "contents": "Strong goals here.", "text": None},
            {"type": "highlight", "page": 0, "user_name": "Hayley Lawson",
             "user_id": "73010000000056571", "user_role": "teacher",
             "contents": "instructor note", "text": None},
        ]

    monkeypatch.setattr(peer_review, "get_course_id", fake_course_id)
    monkeypatch.setattr(peer_review, "_submission_annotations", fake_annotations)

    result = asyncio.run(peer_review.get_submission_annotations(264948, 2971223, 404519))
    assert "Jaimie Eason (user_id=416654) — 2 annotation(s): 1 highlight, 1 point comment" in result
    assert "Expand on your mom's influence." in result
    assert "Hayley Lawson" not in result  # teacher hidden by default

    with_teacher = asyncio.run(
        peer_review.get_submission_annotations(264948, 2971223, 404519, reviewers_only=False)
    )
    assert "Hayley Lawson" in with_teacher


def test_summarize_reviewer_aggregates_across_assigned(monkeypatch):
    async def fake_course_id(identifier):
        return 264948

    async def fake_paginated(path, params=None):
        return [
            {"assessor_id": 416654, "user_id": 404519, "workflow_state": "completed",
             "user": {"display_name": "Dilara Thompson"}},
            {"assessor_id": 416654, "user_id": 405082, "workflow_state": "assigned",
             "user": {"display_name": "Cass Garcia"}},
            {"assessor_id": 999, "user_id": 404519, "workflow_state": "completed",
             "user": {"display_name": "Dilara Thompson"}},  # different reviewer
        ]

    async def fake_annotations(course_id, assignment_id, user_id):
        if user_id == 404519:
            return [{"type": "highlight", "page": 0, "user_name": "Jaimie Eason",
                     "user_id": "73010000000416654", "user_role": "student",
                     "contents": "Good point.", "text": None}]
        return []  # left nothing on Cass's draft

    monkeypatch.setattr(peer_review, "get_course_id", fake_course_id)
    monkeypatch.setattr(peer_review, "canvas_paginated", fake_paginated)
    monkeypatch.setattr(peer_review, "_submission_annotations", fake_annotations)

    result = asyncio.run(peer_review.summarize_reviewer_annotations(264948, 2971223, 416654))
    assert "Assigned 2 review(s); left annotations on 1" in result
    assert "Total: 1 annotation(s), 1 carrying written feedback" in result
    assert "On Dilara Thompson's draft — 1 annotation(s)" in result
    assert "On Cass Garcia's draft — 0 annotation(s)" in result


def test_random_assignment_needs_two_submitters(monkeypatch):
    async def fake_course_id(identifier):
        return 264948

    async def fake_paginated(path, params=None):
        return _fake_submissions()[:1]

    monkeypatch.setattr(peer_review, "get_course_id", fake_course_id)
    monkeypatch.setattr(peer_review, "canvas_paginated", fake_paginated)
    result = asyncio.run(peer_review.randomly_assign_peer_reviews(264948, 999))
    assert "need at least 2" in result
