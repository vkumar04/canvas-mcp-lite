from __future__ import annotations

from typing import Union

from ..client import canvas_paginated, canvas_request
from ..util import format_date, get_course_id

# Classic Quizzes only (/quizzes). New Quizzes lives at a separate /api/quiz/v1
# endpoint and isn't covered here.


async def list_quizzes(course_identifier: Union[str, int]) -> str:
    """List classic quizzes in a course with due dates and points. Does not include New Quizzes."""
    course_id = await get_course_id(course_identifier)
    quizzes = await canvas_paginated(f"/courses/{course_id}/quizzes")
    if not quizzes:
        return "No classic quizzes found (course may use New Quizzes, which this tool doesn't cover)."
    lines = [
        f"ID: {q.get('id')}\nTitle: {q.get('title')}\nDue: {format_date(q.get('due_at'))}\n"
        f"Points: {q.get('points_possible')}\nPublished: {'Yes' if q.get('published') else 'No'}"
        for q in quizzes
    ]
    return f"Quizzes in course {course_id}:\n\n" + "\n\n".join(lines)


async def list_quiz_submissions(course_identifier: Union[str, int], quiz_id: Union[str, int]) -> str:
    """List student submissions for a classic quiz with scores and completion status."""
    course_id = await get_course_id(course_identifier)
    # This endpoint wraps each page in {"quiz_submissions": [...]}.
    pages = await canvas_paginated(f"/courses/{course_id}/quizzes/{quiz_id}/submissions")
    subs = [s for page in pages for s in (page.get("quiz_submissions") or [])]
    if not subs:
        return "No quiz submissions found."
    lines = [
        f"- user_id={s.get('user_id')}: {s.get('workflow_state')}, "
        f"score={s.get('score')}/{s.get('quiz_points_possible')}, "
        f"attempt={s.get('attempt')}, finished={format_date(s.get('finished_at'))}"
        for s in subs
    ]
    return f"Submissions for quiz {quiz_id}:\n\n" + "\n".join(lines)


async def get_quiz_details(course_identifier: Union[str, int], quiz_id: Union[str, int]) -> str:
    """Get details for one classic quiz, including question count and time limit."""
    course_id = await get_course_id(course_identifier)
    q = await canvas_request("GET", f"/courses/{course_id}/quizzes/{quiz_id}")
    return (
        f"Title: {q.get('title')}\n"
        f"Due: {format_date(q.get('due_at'))}\n"
        f"Points: {q.get('points_possible')}\n"
        f"Question Count: {q.get('question_count')}\n"
        f"Time Limit: {q.get('time_limit')} min\n"
        f"Allowed Attempts: {q.get('allowed_attempts')}\n"
        f"Published: {'Yes' if q.get('published') else 'No'}\n\n"
        f"Description:\n{q.get('description', '(none)')}"
    )
