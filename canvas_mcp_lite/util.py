"""Course-identifier resolution and date formatting.

Course code -> ID lookups are cached with a short TTL (not forever) to avoid
the staleness class of bug where a code silently keeps resolving to an old
course after a rebuild/copy. See canvas-mcp issue discussion 2026-08-17.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional, Union

from .client import canvas_paginated

_CACHE_TTL_SECONDS = 300

_code_to_id: dict[str, int] = {}
_cache_loaded_at: float = 0.0


async def _refresh_course_cache() -> None:
    global _cache_loaded_at
    courses = await canvas_paginated("/courses", {"per_page": 100})
    _code_to_id.clear()
    for course in courses:
        code = course.get("course_code")
        course_id = course.get("id")
        if code and course_id:
            _code_to_id[code] = course_id
    _cache_loaded_at = time.monotonic()


async def get_course_id(course_identifier: Union[str, int]) -> int:
    """Resolve a numeric course ID or a course_code string to a numeric ID."""
    if isinstance(course_identifier, int):
        return course_identifier

    course_str = str(course_identifier).strip()
    if course_str.isdigit():
        return int(course_str)

    cache_is_stale = (time.monotonic() - _cache_loaded_at) > _CACHE_TTL_SECONDS
    if course_str not in _code_to_id or cache_is_stale:
        await _refresh_course_cache()

    if course_str in _code_to_id:
        return _code_to_id[course_str]

    raise ValueError(f"Could not resolve course identifier: {course_identifier!r}")


def format_date(value: Optional[str]) -> str:
    if not value:
        return "N/A"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return value


def announcement_posting_status(topic: dict) -> str:
    """Correctly distinguish 'posted' from 'delay-scheduled, not live yet'.

    Canvas returns both `posted_at` and `delayed_post_at` on announcements;
    the field to trust for "is this actually visible to students" is
    `delayed_post_at` when it's set and in the future.
    """
    delayed_post_at = topic.get("delayed_post_at")
    posted_at = topic.get("posted_at")

    if delayed_post_at:
        try:
            delay_dt = datetime.fromisoformat(delayed_post_at.replace("Z", "+00:00"))
            if delay_dt > datetime.now(delay_dt.tzinfo):
                return f"Delayed until: {format_date(delayed_post_at)} (not yet visible to students)"
        except ValueError:
            pass

    if posted_at:
        return f"Posted: {format_date(posted_at)}"

    return "Not posted (draft)"
