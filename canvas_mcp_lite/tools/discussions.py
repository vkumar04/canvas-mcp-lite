from __future__ import annotations

from typing import Union

from ..client import canvas_paginated, canvas_request
from ..util import format_date, get_course_id


async def list_discussion_topics(course_identifier: Union[str, int]) -> str:
    """List discussion topics (excluding announcements) in a course."""
    course_id = await get_course_id(course_identifier)
    topics = await canvas_paginated(
        f"/courses/{course_id}/discussion_topics", {"only_announcements": False}
    )
    topics = [t for t in topics if not t.get("is_announcement")]
    if not topics:
        return "No discussion topics found."
    lines = []
    for t in topics:
        lines.append(
            f"ID: {t.get('id')}\n"
            f"Title: {t.get('title')}\n"
            f"Published: {'Yes' if t.get('published') else 'No'}\n"
            f"Reply Count: {t.get('discussion_subentry_count', 0)}"
        )
    return f"Discussion topics in course {course_id}:\n\n" + "\n\n".join(lines)


async def get_discussion_topic_details(course_identifier: Union[str, int], topic_id: Union[str, int]) -> str:
    """Get full details and message body for one discussion topic."""
    course_id = await get_course_id(course_identifier)
    t = await canvas_request("GET", f"/courses/{course_id}/discussion_topics/{topic_id}")
    return (
        f"Title: {t.get('title')}\n"
        f"Published: {'Yes' if t.get('published') else 'No'}\n"
        f"Posted: {format_date(t.get('posted_at'))}\n\n"
        f"{t.get('message', '')}"
    )


async def create_discussion_topic(
    course_identifier: Union[str, int], title: str, message: str, published: bool = True
) -> str:
    """Create a new (non-announcement) discussion topic."""
    course_id = await get_course_id(course_identifier)
    result = await canvas_request(
        "POST",
        f"/courses/{course_id}/discussion_topics",
        json_body={"title": title, "message": message, "published": published},
    )
    return f"Created discussion topic '{result.get('title')}' (ID: {result.get('id')})"


async def list_discussion_entries(course_identifier: Union[str, int], topic_id: Union[str, int]) -> str:
    """List top-level entries (posts) in a discussion topic, with reply counts."""
    course_id = await get_course_id(course_identifier)
    entries = await canvas_paginated(f"/courses/{course_id}/discussion_topics/{topic_id}/entries")
    if not entries:
        return "No entries found."
    lines = []
    for e in entries:
        lines.append(
            f"ID: {e.get('id')} | user_id={e.get('user_id')} | "
            f"{e.get('recent_replies', []).__len__()} recent replies | "
            f"posted {format_date(e.get('created_at'))}\n"
            f"{e.get('message', '')}"
        )
    return f"Entries in topic {topic_id}:\n\n" + "\n\n".join(lines)


async def post_discussion_entry(course_identifier: Union[str, int], topic_id: Union[str, int], message: str) -> str:
    """Post a new top-level entry in a discussion topic."""
    course_id = await get_course_id(course_identifier)
    entry = await canvas_request(
        "POST",
        f"/courses/{course_id}/discussion_topics/{topic_id}/entries",
        json_body={"message": message},
    )
    return f"Posted entry (ID: {entry.get('id')}) to topic {topic_id}."


async def reply_to_discussion_entry(
    course_identifier: Union[str, int], topic_id: Union[str, int], entry_id: Union[str, int], message: str
) -> str:
    """Reply to a specific entry in a discussion topic."""
    course_id = await get_course_id(course_identifier)
    reply = await canvas_request(
        "POST",
        f"/courses/{course_id}/discussion_topics/{topic_id}/entries/{entry_id}/replies",
        json_body={"message": message},
    )
    return f"Posted reply (ID: {reply.get('id')}) to entry {entry_id}."


async def delete_discussion_topic(course_identifier: Union[str, int], topic_id: Union[str, int]) -> str:
    """Permanently delete a discussion topic (or announcement — they share the same endpoint). This cannot be undone."""
    course_id = await get_course_id(course_identifier)
    result = await canvas_request("DELETE", f"/courses/{course_id}/discussion_topics/{topic_id}")
    title = result.get("title") or (result.get("discussion_topic") or {}).get("title", topic_id)
    return f"Deleted topic '{title}'."
