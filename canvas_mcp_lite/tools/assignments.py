from __future__ import annotations

from typing import Optional, Union

from ..client import canvas_paginated, canvas_request
from ..util import format_date, get_course_id
from .files import download_and_extract_text


async def list_assignments(course_identifier: Union[str, int]) -> str:
    """List assignments in a course with due dates and points."""
    course_id = await get_course_id(course_identifier)
    assignments = await canvas_paginated(f"/courses/{course_id}/assignments")
    if not assignments:
        return "No assignments found."
    lines = []
    for a in assignments:
        lines.append(
            f"ID: {a.get('id')}\n"
            f"Name: {a.get('name')}\n"
            f"Due: {format_date(a.get('due_at'))}\n"
            f"Points: {a.get('points_possible')}\n"
            f"Published: {'Yes' if a.get('published') else 'No'}"
        )
    return f"Assignments in course {course_id}:\n\n" + "\n\n".join(lines)


async def get_assignment_details(course_identifier: Union[str, int], assignment_id: Union[str, int]) -> str:
    """Get full details for one assignment, including description."""
    course_id = await get_course_id(course_identifier)
    a = await canvas_request("GET", f"/courses/{course_id}/assignments/{assignment_id}")
    return (
        f"Name: {a.get('name')}\n"
        f"ID: {a.get('id')}\n"
        f"Due: {format_date(a.get('due_at'))}\n"
        f"Points: {a.get('points_possible')}\n"
        f"Submission Types: {', '.join(a.get('submission_types', []))}\n"
        f"Published: {'Yes' if a.get('published') else 'No'}\n\n"
        f"Description:\n{a.get('description', '(none)')}"
    )


async def list_submissions(course_identifier: Union[str, int], assignment_id: Union[str, int]) -> str:
    """List submissions for an assignment with grade/submission status."""
    course_id = await get_course_id(course_identifier)
    subs = await canvas_paginated(
        f"/courses/{course_id}/assignments/{assignment_id}/submissions",
        {"include[]": "user"},
    )
    if not subs:
        return "No submissions found."
    lines = []
    for s in subs:
        user = s.get("user", {}) or {}
        flags = []
        if s.get("missing"):
            flags.append("MISSING")
        if s.get("late"):
            flags.append("LATE")
        if s.get("excused"):
            flags.append("EXCUSED")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(
            f"- {user.get('name', 'Unknown')} (user_id={s.get('user_id')}): "
            f"{s.get('workflow_state')}, score={s.get('score')}, "
            f"submitted_at={format_date(s.get('submitted_at'))}{flag_str}"
        )
    return f"Submissions for assignment {assignment_id}:\n\n" + "\n".join(lines)


async def get_submission_content(
    course_identifier: Union[str, int], assignment_id: Union[str, int], user_id: Union[str, int]
) -> str:
    """Pull what a student actually submitted for an assignment — extracts readable text from
    uploaded files (PDF/DOCX/text), or returns the typed text / URL for other submission types.
    Use this before grade_submission or grade_with_rubric so you're grading the real content."""
    course_id = await get_course_id(course_identifier)
    sub = await canvas_request(
        "GET",
        f"/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}",
        params={"include[]": "submission_history"},
    )

    submission_type = sub.get("submission_type")
    header = (
        f"Student user_id={user_id} | type={submission_type} | "
        f"submitted_at={format_date(sub.get('submitted_at'))} | "
        f"attempt={sub.get('attempt')}\n"
    )

    if submission_type is None:
        return header + "\nNothing submitted yet."

    if submission_type == "online_text_entry":
        return header + f"\n{sub.get('body', '(empty)')}"

    if submission_type == "online_url":
        return header + f"\nSubmitted URL: {sub.get('url')}"

    if submission_type == "discussion_topic":
        entries = sub.get("discussion_entries", []) or []
        if not entries:
            return header + "\nNo discussion entries found on this submission."
        parts = [header]
        for e in entries:
            parts.append(f"[{format_date(e.get('created_at'))}] {e.get('user_name', '?')}:\n{e.get('message', '')}")
        return "\n\n".join(parts)

    if submission_type == "online_upload":
        attachments = sub.get("attachments", []) or []
        if not attachments:
            return header + "\nNo attachments found on this submission."
        parts = [header]
        for att in attachments:
            text = await download_and_extract_text(
                att["url"], att.get("content-type", ""), att.get("display_name", att.get("filename", "file"))
            )
            parts.append(text)
        return "\n\n---\n\n".join(parts)

    return header + (
        f"\nSubmission type '{submission_type}' isn't supported for content extraction "
        f"(only online_text_entry, online_url, and online_upload are). Raw data: "
        f"{ {k: sub.get(k) for k in ['url', 'media_comment_id', 'body']} }"
    )


async def create_assignment(
    course_identifier: Union[str, int],
    name: str,
    description: str = "",
    points_possible: Optional[float] = None,
    due_at: Optional[str] = None,
    published: bool = False,
    submission_types: Optional[str] = None,
) -> str:
    """Create an assignment. due_at is ISO-8601 (e.g. 2026-10-09T23:59:59-04:00). Defaults to unpublished.
    submission_types is a comma-separated string, e.g. "online_upload,online_text_entry" or "on_paper"."""
    course_id = await get_course_id(course_identifier)
    fields: dict = {"name": name, "description": description, "published": published}
    if points_possible is not None:
        fields["points_possible"] = points_possible
    if due_at is not None:
        fields["due_at"] = due_at
    if submission_types is not None:
        fields["submission_types"] = [t.strip() for t in submission_types.split(",") if t.strip()]
    a = await canvas_request(
        "POST", f"/courses/{course_id}/assignments", json_body={"assignment": fields}
    )
    return f"Created assignment '{a.get('name')}' (ID: {a.get('id')}, published: {a.get('published')})"


async def update_assignment(
    course_identifier: Union[str, int],
    assignment_id: Union[str, int],
    name: Optional[str] = None,
    description: Optional[str] = None,
    points_possible: Optional[float] = None,
    due_at: Optional[str] = None,
    published: Optional[bool] = None,
    peer_reviews: Optional[bool] = None,
    automatic_peer_reviews: Optional[bool] = None,
) -> str:
    """Update an assignment's name, description, points, due date, published state, and/or peer review settings.
    peer_reviews=True must be set before assign_peer_review will work on this assignment."""
    course_id = await get_course_id(course_identifier)
    fields: dict = {}
    if name is not None:
        fields["name"] = name
    if description is not None:
        fields["description"] = description
    if points_possible is not None:
        fields["points_possible"] = points_possible
    if due_at is not None:
        fields["due_at"] = due_at
    if published is not None:
        fields["published"] = published
    if peer_reviews is not None:
        fields["peer_reviews"] = peer_reviews
    if automatic_peer_reviews is not None:
        fields["automatic_peer_reviews"] = automatic_peer_reviews
    if not fields:
        return "Nothing to update — provide at least one field."
    a = await canvas_request(
        "PUT",
        f"/courses/{course_id}/assignments/{assignment_id}",
        json_body={"assignment": fields},
    )
    return f"Updated assignment '{a.get('name')}' (due: {format_date(a.get('due_at'))}, published: {a.get('published')})"


async def delete_assignment(course_identifier: Union[str, int], assignment_id: Union[str, int]) -> str:
    """Permanently delete an assignment and its submissions/grades. This cannot be undone."""
    course_id = await get_course_id(course_identifier)
    a = await canvas_request("DELETE", f"/courses/{course_id}/assignments/{assignment_id}")
    return f"Deleted assignment '{a.get('name', assignment_id)}'."
