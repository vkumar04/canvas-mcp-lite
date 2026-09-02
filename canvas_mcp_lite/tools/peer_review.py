from __future__ import annotations

import asyncio
import random
from typing import Union

from ..client import CanvasAPIError, canvas_paginated, canvas_request
from ..util import get_course_id


def _rotation_pairs(user_ids: list, reviews_each: int) -> list[tuple]:
    """Shuffle, then have each student review the next `reviews_each` students in
    the circle. Guarantees: no self-review, no duplicate pair, everyone gives and
    receives exactly `reviews_each` reviews (requires reviews_each < len(user_ids))."""
    order = list(user_ids)
    random.shuffle(order)
    n = len(order)
    return [
        (order[i], order[(i + j) % n])  # (reviewer, reviewee)
        for i in range(n)
        for j in range(1, reviews_each + 1)
    ]


async def _submission_id_for_user(course_id: int, assignment_id: Union[str, int], user_id: Union[str, int]) -> int:
    """The peer_reviews endpoints need the submission's own numeric id, not the student's user_id.
    On a brand-new assignment Canvas can lazily materialize the submission stub, briefly
    returning id=null — retry a few times rather than surfacing that as a hard failure."""
    for attempt in range(5):
        sub = await canvas_request("GET", f"/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}")
        if sub.get("id") is not None:
            return sub["id"]
        await asyncio.sleep(1)
    raise RuntimeError(
        f"Canvas hasn't created a submission record for user {user_id} on assignment {assignment_id} yet — try again shortly."
    )


async def list_peer_reviews(course_identifier: Union[str, int], assignment_id: Union[str, int]) -> str:
    """List peer review assignments (who's reviewing whom) for an assignment."""
    course_id = await get_course_id(course_identifier)
    # Canvas's field names here are easy to get backwards: user_id/user is the
    # REVIEWEE (whose submission is being reviewed), assessor_id/assessor is the REVIEWER.
    reviews = await canvas_paginated(
        f"/courses/{course_id}/assignments/{assignment_id}/peer_reviews",
        {"include[]": "user"},
    )
    if not reviews:
        return "No peer reviews assigned yet."
    lines = []
    for r in reviews:
        reviewee = r.get("user", {}) or {}
        lines.append(
            f"Reviewer user_id={r.get('assessor_id')} -> "
            f"reviewing user_id={r.get('user_id')} ({reviewee.get('display_name', reviewee.get('name', '?'))})'s "
            f"submission_id={r.get('asset_id')}: {r.get('workflow_state')}"
        )
    return f"Peer reviews for assignment {assignment_id}:\n\n" + "\n".join(lines)


async def assign_peer_review(
    course_identifier: Union[str, int],
    assignment_id: Union[str, int],
    reviewee_user_id: Union[str, int],
    reviewer_user_id: Union[str, int],
) -> str:
    """Assign reviewer_user_id to peer-review reviewee_user_id's submission on this assignment.
    The assignment must have peer_reviews=True set first (see update_assignment)."""
    course_id = await get_course_id(course_identifier)
    submission_id = await _submission_id_for_user(course_id, assignment_id, reviewee_user_id)
    result = await canvas_request(
        "POST",
        f"/courses/{course_id}/assignments/{assignment_id}/submissions/{submission_id}/peer_reviews",
        json_body={"user_id": reviewer_user_id},
    )
    return f"Assigned user {reviewer_user_id} to review user {reviewee_user_id}'s submission (workflow_state: {result.get('workflow_state')})."


async def randomly_assign_peer_reviews(
    course_identifier: Union[str, int],
    assignment_id: Union[str, int],
    reviews_per_student: int = 1,
    dry_run: bool = False,
) -> str:
    """Randomly assign peer reviews for an assignment among students WHO HAVE
    SUBMITTED (unsubmitted students are left out entirely — they neither review
    nor get reviewed). Uses a shuffled rotation, so nobody reviews their own work,
    no pair repeats, and every included student gives and receives exactly
    reviews_per_student reviews. Enables peer_reviews on the assignment if needed.
    Use dry_run=True to preview the pairings without assigning anything; note the
    real run reshuffles, so pairings will differ from the preview."""
    course_id = await get_course_id(course_identifier)

    subs = await canvas_paginated(
        f"/courses/{course_id}/assignments/{assignment_id}/submissions",
        {"include[]": "user"},
    )
    submitted = [
        s for s in subs
        if s.get("submitted_at") and s.get("id")
        and (s.get("user") or {}).get("name") != "Test Student"
    ]
    if len(submitted) < 2:
        return (
            f"Only {len(submitted)} student(s) have submitted — need at least 2 "
            "to assign peer reviews."
        )

    names = {s["user_id"]: (s.get("user") or {}).get("name", f"user {s['user_id']}") for s in submitted}
    submission_ids = {s["user_id"]: s["id"] for s in submitted}
    reviews_each = max(1, min(reviews_per_student, len(submitted) - 1))
    pairs = _rotation_pairs(list(names), reviews_each)

    clamp_note = (
        f" (reviews_per_student clamped to {reviews_each} — only {len(submitted)} students submitted)"
        if reviews_each != reviews_per_student
        else ""
    )
    pair_lines = [f"- {names[rv]} → reviews {names[re]}" for rv, re in pairs]

    if dry_run:
        return (
            f"DRY RUN — nothing assigned. {len(pairs)} peer reviews across "
            f"{len(submitted)} submitted students{clamp_note}:\n\n" + "\n".join(pair_lines)
            + "\n\n(The real run reshuffles, so actual pairings will differ.)"
        )

    assignment = await canvas_request("GET", f"/courses/{course_id}/assignments/{assignment_id}")
    enabled_note = ""
    if not assignment.get("peer_reviews"):
        await canvas_request(
            "PUT",
            f"/courses/{course_id}/assignments/{assignment_id}",
            json_body={"assignment": {"peer_reviews": True}},
        )
        enabled_note = "\n(Enabled peer_reviews on the assignment first.)"

    failures = []
    for reviewer, reviewee in pairs:
        try:
            await canvas_request(
                "POST",
                f"/courses/{course_id}/assignments/{assignment_id}/submissions/{submission_ids[reviewee]}/peer_reviews",
                json_body={"user_id": reviewer},
            )
        except CanvasAPIError as exc:
            failures.append(f"- {names[reviewer]} → {names[reviewee]}: {exc}")

    result = (
        f"Assigned {len(pairs) - len(failures)} of {len(pairs)} peer reviews across "
        f"{len(submitted)} submitted students ({reviews_each} each){clamp_note}:{enabled_note}\n\n"
        + "\n".join(pair_lines)
    )
    if failures:
        result += "\n\nFAILED (retry these individually with assign_peer_review):\n" + "\n".join(failures)
    return result


async def delete_peer_review(
    course_identifier: Union[str, int],
    assignment_id: Union[str, int],
    reviewee_user_id: Union[str, int],
    reviewer_user_id: Union[str, int],
) -> str:
    """Remove a previously assigned peer review."""
    course_id = await get_course_id(course_identifier)
    submission_id = await _submission_id_for_user(course_id, assignment_id, reviewee_user_id)
    await canvas_request(
        "DELETE",
        f"/courses/{course_id}/assignments/{assignment_id}/submissions/{submission_id}/peer_reviews",
        params={"user_id": reviewer_user_id},
    )
    return f"Removed peer review: user {reviewer_user_id} reviewing user {reviewee_user_id}."
