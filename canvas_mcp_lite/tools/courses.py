from __future__ import annotations

from typing import Union

from ..client import canvas_paginated, canvas_request
from ..util import format_date, get_course_id


async def list_courses() -> str:
    """List all courses the authenticated user is enrolled in or teaches."""
    courses = await canvas_paginated(
        "/courses", {"enrollment_state": "active", "include[]": "term"}
    )
    if not courses:
        return "No courses found."
    lines = []
    for c in courses:
        lines.append(
            f"Code: {c.get('course_code')}\n"
            f"Name: {c.get('name')}\n"
            f"ID: {c.get('id')}\n"
            f"Your role: {c.get('enrollments', [{}])[0].get('type', 'unknown') if c.get('enrollments') else 'unknown'}\n"
        )
    return "Courses:\n\n" + "\n".join(lines)


async def get_course_details(course_identifier: Union[str, int]) -> str:
    """Get details for one course (code, name, dates, time zone, role)."""
    course_id = await get_course_id(course_identifier)
    c = await canvas_request("GET", f"/courses/{course_id}")
    return (
        f"Code: {c.get('course_code')}\n"
        f"Name: {c.get('name')}\n"
        f"ID: {c.get('id')}\n"
        f"Start Date: {format_date(c.get('start_at'))}\n"
        f"End Date: {format_date(c.get('end_at'))}\n"
        f"Time Zone: {c.get('time_zone')}\n"
        f"Default View: {c.get('default_view')}\n"
        f"Workflow State: {c.get('workflow_state')}\n"
    )


async def get_syllabus(course_identifier: Union[str, int]) -> str:
    """Get the course's Syllabus body (Settings > Course Details > Syllabus)."""
    course_id = await get_course_id(course_identifier)
    c = await canvas_request(
        "GET", f"/courses/{course_id}", params={"include[]": "syllabus_body"}
    )
    body = c.get("syllabus_body")
    return body if body else "No syllabus body set for this course."


async def get_front_page(course_identifier: Union[str, int]) -> str:
    """Get the course's front page (homepage) title and body."""
    course_id = await get_course_id(course_identifier)
    page = await canvas_request("GET", f"/courses/{course_id}/front_page")
    return (
        f"Title: {page.get('title')}\n"
        f"Updated: {format_date(page.get('updated_at'))}\n"
        f"Published: {page.get('published')}\n\n"
        f"{page.get('body', '')}"
    )


async def list_users(course_identifier: Union[str, int], enrollment_type: str = "student") -> str:
    """List users in a course by enrollment type (student, teacher, ta, observer, designer)."""
    course_id = await get_course_id(course_identifier)
    users = await canvas_paginated(
        f"/courses/{course_id}/users",
        {"enrollment_type[]": enrollment_type, "include[]": "enrollments"},
    )
    if not users:
        return f"No users found with enrollment type '{enrollment_type}'."
    lines = [f"- {u.get('name')} (ID: {u.get('id')}, login: {u.get('login_id', 'N/A')})" for u in users]
    return f"{len(users)} {enrollment_type}(s):\n\n" + "\n".join(lines)


async def list_sections(course_identifier: Union[str, int]) -> str:
    """List sections in a course with student counts."""
    course_id = await get_course_id(course_identifier)
    sections = await canvas_paginated(
        f"/courses/{course_id}/sections", {"include[]": "total_students"}
    )
    if not sections:
        return "No sections found."
    lines = [
        f"- {s.get('name')} (ID: {s.get('id')}, {s.get('total_students', 'N/A')} students)"
        for s in sections
    ]
    return f"Sections in course {course_id}:\n\n" + "\n".join(lines)
