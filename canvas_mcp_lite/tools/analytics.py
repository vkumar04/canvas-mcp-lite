from __future__ import annotations

from typing import Union

from ..client import canvas_paginated
from ..util import format_date, get_course_id


async def get_assignment_analytics(course_identifier: Union[str, int]) -> str:
    """Get score distribution and on-time/late/missing rates for every assignment in a course."""
    course_id = await get_course_id(course_identifier)
    rows = await canvas_paginated(f"/courses/{course_id}/analytics/assignments")
    if not rows:
        return "No assignment analytics available for this course."
    lines = []
    for a in rows:
        tb = a.get("tardiness_breakdown", {}) or {}
        total = tb.get("total", 0)
        lines.append(
            f"{a.get('title')} (due {format_date(a.get('due_at'))})\n"
            f"  Scores: min={a.get('min_score')} median={a.get('median')} max={a.get('max_score')}\n"
            f"  Of {total}: {tb.get('on_time', 0):.0%} on-time, {tb.get('late', 0):.0%} late, "
            f"{tb.get('missing', 0):.0%} missing"
        )
    return f"Assignment analytics for course {course_id}:\n\n" + "\n\n".join(lines)


async def get_student_analytics(course_identifier: Union[str, int]) -> str:
    """Get per-student engagement summary (page views, participations, on-time/late/missing) for a course."""
    course_id = await get_course_id(course_identifier)
    rows = await canvas_paginated(f"/courses/{course_id}/analytics/student_summaries")
    if not rows:
        return "No student analytics available for this course."
    lines = []
    for s in rows:
        tb = s.get("tardiness_breakdown", {}) or {}
        lines.append(
            f"user_id={s.get('id')}: page_views={s.get('page_views')} "
            f"(level {s.get('page_views_level')}/3), participations={s.get('participations')} "
            f"(level {s.get('participations_level')}/3) — "
            f"missing={tb.get('missing', 0)}, late={tb.get('late', 0)}, on_time={tb.get('on_time', 0)}"
        )
    return f"Student analytics for course {course_id}:\n\n" + "\n".join(lines)
