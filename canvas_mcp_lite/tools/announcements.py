from __future__ import annotations

from typing import Optional, Union

from ..client import canvas_paginated, canvas_request
from ..util import announcement_posting_status, get_course_id


async def list_announcements(course_identifier: Union[str, int]) -> str:
    """List announcements in a course, correctly distinguishing posted vs delay-scheduled."""
    course_id = await get_course_id(course_identifier)
    announcements = await canvas_paginated(
        f"/courses/{course_id}/discussion_topics", {"only_announcements": True}
    )
    if not announcements:
        return "No announcements found."
    lines = []
    for a in announcements:
        lines.append(
            f"ID: {a.get('id')}\n"
            f"Title: {a.get('title')}\n"
            f"{announcement_posting_status(a)}"
        )
    return f"Announcements in course {course_id}:\n\n" + "\n\n".join(lines)


async def create_announcement(
    course_identifier: Union[str, int],
    title: str,
    message: str,
    delayed_post_at: Optional[str] = None,
) -> str:
    """Create an announcement. Pass delayed_post_at (ISO-8601) to schedule it instead of posting now."""
    course_id = await get_course_id(course_identifier)
    payload: dict = {
        "title": title,
        "message": message,
        "is_announcement": True,
    }
    if delayed_post_at:
        payload["delayed_post_at"] = delayed_post_at
    result = await canvas_request(
        "POST", f"/courses/{course_id}/discussion_topics", json_body=payload
    )
    status = announcement_posting_status(result)
    return f"Created announcement '{result.get('title')}' (ID: {result.get('id')}). {status}"


async def update_announcement(
    course_identifier: Union[str, int],
    announcement_id: Union[str, int],
    title: Optional[str] = None,
    message: Optional[str] = None,
    delayed_post_at: Optional[str] = None,
) -> str:
    """Update an existing announcement's title, message, and/or scheduled post date."""
    course_id = await get_course_id(course_identifier)
    payload: dict = {}
    if title is not None:
        payload["title"] = title
    if message is not None:
        payload["message"] = message
    if delayed_post_at is not None:
        payload["delayed_post_at"] = delayed_post_at
    if not payload:
        return "Nothing to update — provide title, message, or delayed_post_at."
    result = await canvas_request(
        "PUT",
        f"/courses/{course_id}/discussion_topics/{announcement_id}",
        json_body=payload,
    )
    status = announcement_posting_status(result)
    return f"Updated announcement '{result.get('title')}' (ID: {result.get('id')}). {status}"


async def delete_announcement(course_identifier: Union[str, int], announcement_id: Union[str, int]) -> str:
    """Permanently delete an announcement. This cannot be undone."""
    course_id = await get_course_id(course_identifier)
    result = await canvas_request(
        "DELETE", f"/courses/{course_id}/discussion_topics/{announcement_id}"
    )
    title = result.get("title") or (result.get("discussion_topic") or {}).get("title", announcement_id)
    return f"Deleted announcement '{title}'."


async def bulk_delete_announcements(course_identifier: Union[str, int], announcement_ids: str) -> str:
    """Delete multiple announcements at once. announcement_ids is a comma-separated list of IDs
    (get them from list_announcements first). This cannot be undone."""
    course_id = await get_course_id(course_identifier)
    ids = [i.strip() for i in announcement_ids.split(",") if i.strip()]
    results = []
    for aid in ids:
        try:
            result = await canvas_request("DELETE", f"/courses/{course_id}/discussion_topics/{aid}")
            title = result.get("title") or (result.get("discussion_topic") or {}).get("title", aid)
            results.append(f"  Deleted '{title}' (ID {aid})")
        except Exception as exc:
            results.append(f"  FAILED to delete ID {aid}: {exc}")
    return f"Bulk delete of {len(ids)} announcement(s):\n" + "\n".join(results)
