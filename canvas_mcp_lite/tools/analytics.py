from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional, Union

from ..client import CanvasAPIError, canvas_paginated, canvas_request
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


def _pts(value) -> str:
    """Render a score/points value compactly (10.0 -> 10, 8.5 -> 8.5, None -> ?)."""
    if value is None:
        return "?"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _submission_status(sub: dict, now: datetime) -> tuple[str, str]:
    """(bucket, human status) for one submission row.
    Buckets: graded, zero, submitted, missing, excused, upcoming, none."""
    assignment = sub.get("assignment") or {}
    points = assignment.get("points_possible")
    score = sub.get("score")
    state = sub.get("workflow_state")
    late = " (late)" if sub.get("late") else ""

    if sub.get("excused"):
        return "excused", "excused"
    if state == "graded" and score is not None:
        detail = f"{_pts(score)}/{_pts(points)}{late}"
        if sub.get("missing"):
            detail += " — marked missing"
        elif not sub.get("submitted_at") and score == 0:
            detail += " — nothing submitted"
        return ("zero" if float(score) == 0 else "graded"), detail
    if state in ("submitted", "pending_review") or sub.get("submitted_at"):
        return "submitted", f"submitted{late}, not yet graded"
    if sub.get("missing"):
        return "missing", "MISSING"
    due = assignment.get("due_at")
    if due:
        try:
            due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
            if due_dt > now:
                return "upcoming", "not yet due"
        except ValueError:
            pass
        return "missing", "not submitted (past due)"
    return "none", "not submitted (no due date)"


async def get_student_grades(
    course_identifier: Union[str, int],
    user_id: Union[str, int],
    include_other_courses: bool = False,
) -> str:
    """One student's full grade record in a course: current and final grade, last
    activity, then every published assignment in due-date order with score and
    status (graded / zero / submitted but ungraded / MISSING / excused / not yet due).
    Use this before assigning a zero, writing to a student about their standing,
    or judging whether someone has "checked out" — it shows the whole pattern,
    not one assignment. Set include_other_courses=True to also list the student's
    current grade and last activity in every other course you teach that they're
    enrolled in (one lookup per course; Canvas won't show a student's courses you don't teach)."""
    course_id = await get_course_id(course_identifier)
    uid = str(user_id)
    now = datetime.now(timezone.utc)

    # Course-level standing comes from the enrollment record.
    student_name = f"user {uid}"
    standing = "Current grade: unavailable"
    try:
        enrollments = await canvas_paginated(
            f"/courses/{course_id}/enrollments",
            {"user_id": uid, "type[]": "StudentEnrollment", "include[]": "current_points"},
        )
    except CanvasAPIError as exc:
        enrollments = []
        standing = f"Current grade: unavailable (enrollment lookup failed: {exc.status_code})"
    if enrollments:
        enr = enrollments[0]
        student_name = (enr.get("user") or {}).get("name") or student_name
        g = enr.get("grades") or {}
        standing = (
            f"Current grade: {g.get('current_grade') or '—'} ({_pts(g.get('current_score'))}%) on graded work"
            f" | Final if missing work stays zero: {g.get('final_grade') or '—'} ({_pts(g.get('final_score'))}%)"
            f"\nLast activity in course: {format_date(enr.get('last_activity_at'))}"
            f" | Total activity time: {round((enr.get('total_activity_time') or 0) / 3600, 1)} h"
            f" | Enrollment: {enr.get('enrollment_state', '?')}"
        )
        if len(enrollments) > 1:
            standing += f" (in {len(enrollments)} sections)"

    subs = await canvas_paginated(
        f"/courses/{course_id}/students/submissions",
        {"student_ids[]": uid, "include[]": ["assignment"]},
    )
    rows = []
    hidden = 0
    for sub in subs:
        a = sub.get("assignment") or {}
        if a.get("published") is False:
            hidden += 1
            continue
        rows.append(sub)
    rows.sort(key=lambda s: ((s.get("assignment") or {}).get("due_at") is None, (s.get("assignment") or {}).get("due_at") or ""))

    counts: dict[str, int] = {}
    lines = []
    for sub in rows:
        a = sub.get("assignment") or {}
        bucket, status = _submission_status(sub, now)
        counts[bucket] = counts.get(bucket, 0) + 1
        extra = []
        if sub.get("submitted_at"):
            extra.append(f"submitted {format_date(sub['submitted_at'])}")
        if sub.get("graded_at") and bucket in ("graded", "zero"):
            extra.append(f"graded {format_date(sub['graded_at'])}")
        if (sub.get("attempt") or 0) > 1:
            extra.append(f"{sub['attempt']} attempts")
        lines.append(
            f"- {a.get('name', f'assignment {sub.get("assignment_id")}')} "
            f"(due {format_date(a.get('due_at'))}): {status}"
            + (f" [{'; '.join(extra)}]" if extra else "")
        )

    def n(bucket: str) -> int:
        return counts.get(bucket, 0)

    summary = (
        f"{len(rows)} published assignments: {n('graded')} graded above zero, {n('zero')} zero(s), "
        f"{n('submitted')} submitted awaiting grade, {n('missing')} missing, {n('excused')} excused, "
        f"{n('upcoming')} not yet due"
        + (f", {n('none')} with no due date" if n("none") else "")
        + (f". ({hidden} unpublished assignment(s) hidden.)" if hidden else ".")
    )

    out = [
        f"Grade record for {student_name} (user_id {uid}) in course {course_id}",
        standing,
        "",
        summary,
        "",
        *(lines or ["No assignments found."]),
    ]
    if getattr(subs, "truncated", False):
        out.append(f"\n(Note: assignment list truncated at {len(subs)} items.)")

    if include_other_courses:
        out.append("")
        out.append("Other courses you teach that this student is enrolled in:")
        # Canvas only lets admins read /users/:id/enrollments directly, so ask
        # each of the instructor's own courses whether the student is in it.
        try:
            my_courses = await canvas_paginated(
                "/courses", {"enrollment_type": "teacher", "state[]": "available"}
            )
        except CanvasAPIError as exc:
            my_courses = None
            out.append(f"- unavailable (could not list your courses: {exc.status_code})")
        if my_courses is not None:
            others = [c for c in my_courses if int(c.get("id", -1)) != int(course_id)]

            async def lookup(course: dict):
                try:
                    enr = await canvas_paginated(
                        f"/courses/{course['id']}/enrollments",
                        {"user_id": uid, "type[]": "StudentEnrollment", "include[]": "current_points"},
                    )
                except CanvasAPIError:
                    return course, None
                return course, (enr[0] if enr else None)

            results = await asyncio.gather(*(lookup(c) for c in others))
            found = 0
            for course, enr in results:
                if not enr:
                    continue
                found += 1
                label = f"{course.get('course_code') or ''} {course.get('name') or ''}".strip() or f"course {course['id']}"
                g = enr.get("grades") or {}
                out.append(
                    f"- {label} (id {course['id']}): current {g.get('current_grade') or '—'} "
                    f"({_pts(g.get('current_score'))}%), final-if-zeros {_pts(g.get('final_score'))}%, "
                    f"last activity {format_date(enr.get('last_activity_at'))}, "
                    f"enrollment {enr.get('enrollment_state', '?')}"
                )
            if not found:
                out.append("- none")
    return "\n".join(out)
