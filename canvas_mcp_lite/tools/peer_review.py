from __future__ import annotations

import asyncio
import json
import random
from typing import Union

from ..canvadocs import fetch_attachment_annotations, local_user_id
from ..client import CanvasAPIError, canvas_paginated, canvas_request
from ..util import format_date, get_course_id


# DocViewer annotation types that carry reviewer commentary vs. pure markup.
_ANNOTATION_LABELS = {
    "highlight": "highlight",
    "strikeout": "strikeout",
    "freetext": "text box",
    "point": "point comment",
    "area": "area comment",
    "commentReply": "reply",
    "ink": "drawing",
    "square": "box",
}


def _annotation_text(ann: dict) -> str:
    """The reviewer's words on an annotation (its comment, plus any quoted text)."""
    contents = (ann.get("contents") or "").strip()
    quoted = (ann.get("text") or "").strip()
    if contents and quoted:
        return f'{contents}  [on: "{quoted[:80]}"]'
    return contents or (f'(marked: "{quoted[:80]}")' if quoted else "")


async def _submission_annotations(course_id: int, assignment_id, user_id) -> list[dict]:
    """All DocViewer annotations across a student's submitted file(s)."""
    sub = await canvas_request(
        "GET", f"/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}"
    )
    out: list[dict] = []
    for att in sub.get("attachments") or []:
        anns = await fetch_attachment_annotations(att.get("preview_url", ""))
        for a in anns:
            a["_file"] = att.get("display_name", att.get("filename", "file"))
        out.extend(anns)
    return out


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


async def _enable_peer_reviews(course_id: int, assignment_id) -> str:
    assignment = await canvas_request("GET", f"/courses/{course_id}/assignments/{assignment_id}")
    if assignment.get("peer_reviews"):
        return ""
    await canvas_request(
        "PUT",
        f"/courses/{course_id}/assignments/{assignment_id}",
        json_body={"assignment": {"peer_reviews": True}},
    )
    return "\n(Enabled peer_reviews on the assignment first.)"


def _parse_pairs(pairs_json: str) -> list[tuple]:
    """Accept '[[reviewer, reviewee], ...]' or
    '[{"reviewer": r, "reviewee": re}, ...]' -> [(reviewer, reviewee), ...]."""
    data = json.loads(pairs_json)
    pairs = []
    for item in data:
        if isinstance(item, dict):
            pairs.append((item["reviewer"], item["reviewee"]))
        else:
            pairs.append((item[0], item[1]))
    return pairs


async def assign_peer_reviews_manual(
    course_identifier: Union[str, int],
    assignment_id: Union[str, int],
    pairs_json: str,
    dry_run: bool = False,
) -> str:
    """Manually assign specific peer-review pairs (e.g. from groups students
    formed), enforcing that BOTH people submitted their own draft. pairs_json is
    a JSON array of [reviewer_user_id, reviewee_user_id] pairs (or objects with
    "reviewer"/"reviewee" keys). A pair is SKIPPED if the reviewer didn't submit
    (they don't get credit for reviewing when they didn't turn in their own
    draft), if the reviewee didn't submit (nothing to review), or if it's a
    self-review. Enables peer_reviews on the assignment if needed. Use
    dry_run=True to preview which pairs would be assigned vs. skipped and why.
    Get user_ids from list_submissions or list_users."""
    course_id = await get_course_id(course_identifier)
    try:
        pairs = _parse_pairs(pairs_json)
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        return (
            f"Couldn't parse pairs_json: {exc}. Expected a JSON array like "
            '[[reviewer_id, reviewee_id], ...] or [{"reviewer": id, "reviewee": id}, ...].'
        )
    if not pairs:
        return "No pairs provided."

    subs = await canvas_paginated(
        f"/courses/{course_id}/assignments/{assignment_id}/submissions",
        {"include[]": "user"},
    )
    all_names = {str(s["user_id"]): (s.get("user") or {}).get("name", f"user {s['user_id']}") for s in subs}
    submitted_ids = {
        str(s["user_id"]) for s in subs
        if s.get("submitted_at") and s.get("id")
        and (s.get("user") or {}).get("name") != "Test Student"
    }

    def name(uid):
        return all_names.get(str(uid), f"user {uid}")

    valid, skipped = [], []
    seen = set()
    for reviewer, reviewee in pairs:
        rk, ek = str(reviewer), str(reviewee)
        if rk == ek:
            skipped.append(f"- {name(reviewer)} → {name(reviewee)}: self-review, skipped")
            continue
        if (rk, ek) in seen:
            skipped.append(f"- {name(reviewer)} → {name(reviewee)}: duplicate pair, skipped")
            continue
        seen.add((rk, ek))
        reasons = []
        if rk not in submitted_ids:
            reasons.append("reviewer didn't submit a draft (no credit for reviewing)")
        if ek not in submitted_ids:
            reasons.append("reviewee didn't submit a draft (nothing to review)")
        if reasons:
            skipped.append(f"- {name(reviewer)} → {name(reviewee)}: {'; '.join(reasons)}")
        else:
            valid.append((reviewer, reviewee))

    valid_lines = [f"- {name(rv)} → reviews {name(re)}" for rv, re in valid]
    skipped_block = ("\n\nSkipped (" + str(len(skipped)) + "):\n" + "\n".join(skipped)) if skipped else ""

    if dry_run:
        return (
            f"DRY RUN — nothing assigned. Would assign {len(valid)} of {len(pairs)} pairs "
            f"(both submitted):\n\n" + ("\n".join(valid_lines) or "(none)") + skipped_block
        )
    if not valid:
        return f"No pairs to assign — all {len(pairs)} were skipped.{skipped_block}"

    enabled_note = await _enable_peer_reviews(course_id, assignment_id)
    submission_ids = {}
    failures = []
    for reviewer, reviewee in valid:
        try:
            sid = submission_ids.get(str(reviewee))
            if sid is None:
                sid = await _submission_id_for_user(course_id, assignment_id, reviewee)
                submission_ids[str(reviewee)] = sid
            await canvas_request(
                "POST",
                f"/courses/{course_id}/assignments/{assignment_id}/submissions/{sid}/peer_reviews",
                json_body={"user_id": reviewer},
            )
        except (CanvasAPIError, RuntimeError) as exc:
            failures.append(f"- {name(reviewer)} → {name(reviewee)}: {exc}")

    result = (
        f"Assigned {len(valid) - len(failures)} of {len(pairs)} requested pairs "
        f"(both submitted their draft):{enabled_note}\n\n" + "\n".join(valid_lines) + skipped_block
    )
    if failures:
        result += "\n\nFAILED to assign:\n" + "\n".join(failures)
    return result


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


async def get_submission_annotations(
    course_identifier: Union[str, int],
    assignment_id: Union[str, int],
    user_id: Union[str, int],
    reviewers_only: bool = True,
) -> str:
    """Show the DocViewer annotations left ON one student's submitted file —
    the highlights, point/area comments, and margin notes peer reviewers add
    directly on the document in Canvas (these do NOT appear in the normal
    submission-comment thread). Grouped by annotator with their comment text.
    reviewers_only=True (default) shows only student peer-reviewer annotations
    and hides the instructor's own; set False to include everyone."""
    course_id = await get_course_id(course_identifier)
    annotations = await _submission_annotations(course_id, assignment_id, user_id)
    if reviewers_only:
        annotations = [a for a in annotations if a.get("user_role") == "student"]
    if not annotations:
        return (
            "No DocViewer annotations found on this submission"
            + (" from peer reviewers." if reviewers_only else ".")
            + " (Reviewers may have used the comment box instead, left nothing yet, "
            "or the submission isn't a DocViewer-rendered file.)"
        )

    by_author: dict = {}
    for a in annotations:
        key = (a.get("user_name", "?"), local_user_id(a.get("user_id")))
        by_author.setdefault(key, []).append(a)

    blocks = []
    for (name, uid), anns in sorted(by_author.items(), key=lambda kv: -len(kv[1])):
        counts: dict = {}
        for a in anns:
            label = _ANNOTATION_LABELS.get(a.get("type"), a.get("type", "annotation"))
            counts[label] = counts.get(label, 0) + 1
        breakdown = ", ".join(f"{n} {lbl}{'s' if n > 1 else ''}" for lbl, n in counts.items())
        lines = [f"{name} (user_id={uid}) — {len(anns)} annotation(s): {breakdown}"]
        for a in sorted(anns, key=lambda x: (x.get("page", 0), x.get("created_at", ""))):
            text = _annotation_text(a)
            if text:
                lines.append(f"  p{(a.get('page', 0) + 1)}: {text}")
        blocks.append("\n".join(lines))

    return (
        f"DocViewer annotations on submission (assignment {assignment_id}, user {user_id}):\n\n"
        + "\n\n".join(blocks)
    )


async def summarize_reviewer_annotations(
    course_identifier: Union[str, int],
    assignment_id: Union[str, int],
    reviewer_user_id: Union[str, int],
) -> str:
    """Grade-the-reviewer view: gather everything ONE student did as a peer
    reviewer on an assignment — the DocViewer annotations they left across every
    submission they were assigned to review — so you can assess the quality and
    quantity of their peer feedback. Pairs with grade_submission on the peer-
    review gradebook column. (Reviews left only in the comment box aren't
    DocViewer annotations; use list_submissions/get_submission_content for those.)"""
    course_id = await get_course_id(course_identifier)
    reviewer_local = local_user_id(reviewer_user_id) or int(reviewer_user_id)

    reviews = await canvas_paginated(
        f"/courses/{course_id}/assignments/{assignment_id}/peer_reviews",
        {"include[]": "user"},
    )
    assigned = [r for r in reviews if str(r.get("assessor_id")) == str(reviewer_user_id)]
    if not assigned:
        return (
            f"No peer reviews are assigned to reviewer user_id={reviewer_user_id} on "
            f"assignment {assignment_id}. (Check list_peer_reviews.)"
        )

    sections = []
    total_annotations = 0
    total_with_text = 0
    completed = 0
    for r in assigned:
        reviewee = r.get("user", {}) or {}
        reviewee_name = reviewee.get("display_name", reviewee.get("name", f"user {r.get('user_id')}"))
        anns = await _submission_annotations(course_id, assignment_id, r.get("user_id"))
        mine = [a for a in anns if local_user_id(a.get("user_id")) == reviewer_local]
        with_text = [a for a in mine if _annotation_text(a)]
        total_annotations += len(mine)
        total_with_text += len(with_text)
        if mine:
            completed += 1
        state = r.get("workflow_state", "?")
        header = (
            f"On {reviewee_name}'s draft — {len(mine)} annotation(s), "
            f"{len(with_text)} with comments [Canvas state: {state}]"
        )
        lines = [header]
        for a in sorted(with_text, key=lambda x: (x.get("page", 0), x.get("created_at", ""))):
            lines.append(f"  p{(a.get('page', 0) + 1)}: {_annotation_text(a)}")
        sections.append("\n".join(lines))

    summary = (
        f"Peer-review work by user_id={reviewer_user_id} on assignment {assignment_id}:\n"
        f"Assigned {len(assigned)} review(s); left annotations on {completed}. "
        f"Total: {total_annotations} annotation(s), {total_with_text} carrying written feedback.\n\n"
    )
    return summary + "\n\n".join(sections)


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
