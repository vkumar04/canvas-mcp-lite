from __future__ import annotations

import asyncio
from typing import Union

from ..client import canvas_paginated, canvas_request
from ..util import get_course_id


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
