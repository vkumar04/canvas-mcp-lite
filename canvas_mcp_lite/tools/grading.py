from __future__ import annotations

import asyncio
import json
from typing import Optional, Union

from ..client import canvas_paginated, canvas_request
from ..util import get_course_id


async def grade_submission(
    course_identifier: Union[str, int],
    assignment_id: Union[str, int],
    user_id: Union[str, int],
    score: Optional[float] = None,
    comment: Optional[str] = None,
    excuse: bool = False,
) -> str:
    """Post a grade and/or comment for one student's submission on one assignment.
    Set excuse=True to mark the assignment excused for this student instead of scoring it."""
    course_id = await get_course_id(course_identifier)
    payload: dict = {}
    if excuse:
        payload["submission"] = {"excuse": True}
    elif score is not None:
        payload["submission"] = {"posted_grade": score}
    if comment:
        payload["comment"] = {"text_comment": comment}
    if not payload:
        return "Nothing to do — provide score, comment, and/or excuse=True."

    result = await canvas_request(
        "PUT",
        f"/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}",
        json_body=payload,
    )
    return (
        f"Graded user {user_id}: score={result.get('score')}, "
        f"grade={result.get('grade')}, excused={result.get('excused')}, "
        f"workflow_state={result.get('workflow_state')}"
    )


async def bulk_grade_submissions(
    course_identifier: Union[str, int],
    assignment_id: Union[str, int],
    grades: dict[str, float],
    comment: Optional[str] = None,
) -> str:
    """Post grades for many students at once on one assignment.
    grades: {user_id: score, ...}. Same optional comment applied to every submission.
    This is a single high-blast-radius call — double check the grades dict before calling."""
    course_id = await get_course_id(course_identifier)
    grade_data: dict = {}
    for user_id, score in grades.items():
        entry: dict = {"posted_grade": score}
        if comment:
            entry["text_comment"] = comment
        grade_data[str(user_id)] = entry

    progress = await canvas_request(
        "POST",
        f"/courses/{course_id}/assignments/{assignment_id}/submissions/update_grades",
        json_body={"grade_data": grade_data},
    )

    progress_url = progress.get("url")
    if not progress_url:
        return f"Submitted bulk grade update for {len(grades)} student(s); no progress URL returned."

    for _ in range(15):
        await asyncio.sleep(1)
        # progress_url is already absolute (includes /api/v1); httpx uses it as-is over base_url.
        status = await canvas_request("GET", progress_url)
        state = status.get("workflow_state")
        if state in ("completed", "failed"):
            return (
                f"Bulk grade update for {len(grades)} student(s): {state} "
                f"({status.get('message', '')})"
            )
    return f"Bulk grade update for {len(grades)} student(s) still running (workflow_state pending)."


async def list_rubrics(course_identifier: Union[str, int]) -> str:
    """List rubrics available in a course."""
    course_id = await get_course_id(course_identifier)
    rubrics = await canvas_paginated(f"/courses/{course_id}/rubrics")
    if not rubrics:
        return "No rubrics found."
    lines = [
        f"- {r.get('title')} (ID: {r.get('id')}, {r.get('points_possible')} pts)"
        for r in rubrics
    ]
    return f"Rubrics in course {course_id}:\n\n" + "\n".join(lines)


async def get_rubric(course_identifier: Union[str, int], rubric_id: Union[str, int]) -> str:
    """Get a rubric's criteria, point values, and rating levels — needed to build a grade_with_rubric call."""
    course_id = await get_course_id(course_identifier)
    rubric = await canvas_request("GET", f"/courses/{course_id}/rubrics/{rubric_id}")
    lines = [f"Rubric: {rubric.get('title')} ({rubric.get('points_possible')} pts total)\n"]
    for criterion in rubric.get("data", []):
        lines.append(f"Criterion id={criterion.get('id')!r}: {criterion.get('description')} (max {criterion.get('points')} pts)")
        for rating in criterion.get("ratings", []):
            lines.append(f"    {rating.get('points')} pts — {rating.get('description')}")
    return "\n".join(lines)


async def grade_with_rubric(
    course_identifier: Union[str, int],
    assignment_id: Union[str, int],
    user_id: Union[str, int],
    criterion_scores: dict[str, float],
    criterion_comments: Optional[dict[str, str]] = None,
    comment: Optional[str] = None,
) -> str:
    """Grade a submission using a rubric already associated with the assignment.
    criterion_scores: {criterion_id: points, ...} — get criterion_ids from get_rubric.
    criterion_comments: optional {criterion_id: comment text, ...}."""
    course_id = await get_course_id(course_identifier)
    rubric_assessment: dict = {}
    for criterion_id, points in criterion_scores.items():
        entry: dict = {"points": points}
        if criterion_comments and criterion_id in criterion_comments:
            entry["comments"] = criterion_comments[criterion_id]
        rubric_assessment[criterion_id] = entry

    payload: dict = {"rubric_assessment": rubric_assessment}
    if comment:
        payload["comment"] = {"text_comment": comment}

    result = await canvas_request(
        "PUT",
        f"/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}",
        json_body=payload,
    )
    return (
        f"Graded user {user_id} with rubric: total score={result.get('score')}, "
        f"grade={result.get('grade')}"
    )


async def create_rubric(
    course_identifier: Union[str, int],
    title: str,
    criteria_json: str,
    associate_with_assignment_id: Optional[Union[str, int]] = None,
    free_form_comments: bool = False,
) -> str:
    """Create a rubric. criteria_json is a JSON array string, each item:
    {"description": str, "points": number, "ratings": [{"description": str, "points": number}, ...]}
    Example: '[{"description": "Content", "points": 30, "ratings": [{"description": "Excellent", "points": 30}, {"description": "Weak", "points": 10}]}]'
    Pass associate_with_assignment_id to attach it to an assignment for grading immediately."""
    course_id = await get_course_id(course_identifier)
    try:
        criteria = json.loads(criteria_json)
    except json.JSONDecodeError as exc:
        return f"criteria_json is not valid JSON: {exc}"

    rubric_criteria = {}
    for i, criterion in enumerate(criteria):
        ratings = {
            str(j): {"description": r["description"], "points": r["points"]}
            for j, r in enumerate(criterion.get("ratings", []))
        }
        rubric_criteria[str(i)] = {
            "description": criterion["description"],
            "points": criterion["points"],
            "ratings": ratings,
        }

    payload: dict = {
        "rubric": {
            "title": title,
            "free_form_criterion_comments": free_form_comments,
            "criteria": rubric_criteria,
        },
        "rubric_association": {
            "association_type": "Course",
            "association_id": course_id,
            "purpose": "bookmark",
        },
    }
    if associate_with_assignment_id is not None:
        payload["rubric_association"] = {
            "association_type": "Assignment",
            "association_id": associate_with_assignment_id,
            "purpose": "grading",
            "use_for_grading": True,
        }

    result = await canvas_request("POST", f"/courses/{course_id}/rubrics", json_body=payload)
    rubric = result.get("rubric", result)
    return (
        f"Created rubric '{rubric.get('title')}' (ID: {rubric.get('id')}, "
        f"{rubric.get('points_possible')} pts total)"
        + (f", associated with assignment {associate_with_assignment_id} for grading" if associate_with_assignment_id else "")
    )


async def delete_rubric(course_identifier: Union[str, int], rubric_id: Union[str, int]) -> str:
    """Permanently delete a rubric. This cannot be undone."""
    course_id = await get_course_id(course_identifier)
    result = await canvas_request("DELETE", f"/courses/{course_id}/rubrics/{rubric_id}")
    rubric = result.get("rubric", result) if isinstance(result, dict) else {}
    return f"Deleted rubric '{rubric.get('title', rubric_id)}'."
