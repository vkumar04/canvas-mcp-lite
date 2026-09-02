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


def test_random_assignment_needs_two_submitters(monkeypatch):
    async def fake_course_id(identifier):
        return 264948

    async def fake_paginated(path, params=None):
        return _fake_submissions()[:1]

    monkeypatch.setattr(peer_review, "get_course_id", fake_course_id)
    monkeypatch.setattr(peer_review, "canvas_paginated", fake_paginated)
    result = asyncio.run(peer_review.randomly_assign_peer_reviews(264948, 999))
    assert "need at least 2" in result
