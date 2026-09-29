from __future__ import annotations

from typing import Optional, Union

from ..client import CanvasAPIError, canvas_paginated, canvas_request
from ..util import format_date, get_course_id
from .quiz_questions import QuestionError, classic_question, parse_questions

# Classic Quizzes only (/quizzes). New Quizzes lives at a separate /api/quiz/v1
# endpoint — see new_quizzes.py.


async def list_quizzes(course_identifier: Union[str, int]) -> str:
    """List classic quizzes in a course with due dates and points. Does not include New Quizzes — use list_new_quizzes for those."""
    course_id = await get_course_id(course_identifier)
    quizzes = await canvas_paginated(f"/courses/{course_id}/quizzes")
    if not quizzes:
        return "No classic quizzes found — try list_new_quizzes (most current courses use New Quizzes)."
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


def _fmt_points(value) -> str:
    return "?" if value is None else (str(int(value)) if float(value) == int(float(value)) else str(value))


async def _question_count(course_id: int, quiz_id) -> int:
    """Count questions directly. The quiz's own question_count is only
    refreshed when the quiz is published, so it lags on unpublished quizzes."""
    return len(await canvas_paginated(f"/courses/{course_id}/quizzes/{quiz_id}/questions"))


async def _add_classic_questions(course_id: int, quiz_id, questions: list[dict], start_position: int) -> tuple[list[str], Optional[str]]:
    """POST each question; stop at the first Canvas rejection and report it."""
    added: list[str] = []
    for offset, q in enumerate(questions):
        payload = classic_question(q, start_position + offset)
        try:
            created = await canvas_request(
                "POST", f"/courses/{course_id}/quizzes/{quiz_id}/questions", json_body={"question": payload}
            )
        except CanvasAPIError as exc:
            return added, (
                f"Canvas rejected question {offset + 1} ({q['type']}: {q['title'][:60]!r}) "
                f"with HTTP {exc.status_code}: {str(exc)[:300]}"
            )
        added.append(f"  {start_position + offset}. [{q['type']}, {_fmt_points(q['points'])} pts] "
                     f"{q['title'][:80]} (question ID {created.get('id')})")
    return added, None


async def create_quiz(
    course_identifier: Union[str, int],
    title: str,
    questions_json: str = "",
    description: str = "",
    quiz_type: str = "assignment",
    time_limit_minutes: Optional[int] = None,
    allowed_attempts: int = 1,
    shuffle_answers: bool = False,
    show_correct_answers: bool = True,
    one_question_at_a_time: bool = False,
    due_at: Optional[str] = None,
    unlock_at: Optional[str] = None,
    lock_at: Optional[str] = None,
    published: bool = False,
) -> str:
    """Create a CLASSIC quiz (the older Canvas quiz engine) with its questions in one
    call. If the course uses New Quizzes (check list_new_quizzes / list_quizzes to see
    which engine the instructor already uses), use create_new_quiz instead — the two
    engines are separate and a classic quiz will look out of place in a New Quizzes course.

    questions_json is a JSON array; the SAME format works for create_new_quiz. Types:
      multiple_choice  {"type":"multiple_choice","text":"...","points":1,
                        "answers":[{"text":"A","correct":true,"feedback":"optional"},{"text":"B"}]}
      multiple_answers {"type":"multiple_answers","text":"...","answers":[{"text":"A","correct":true},
                        {"text":"B","correct":true},{"text":"C"}]}
      true_false       {"type":"true_false","text":"...","answer":true}
      short_answer     {"type":"short_answer","text":"...","answers":["Paris","paris"]}
      essay            {"type":"essay","text":"..."}
      numerical        {"type":"numerical","text":"...","answer":3.14,"margin":0.01}
                       or {"type":"numerical","text":"...","range":[10,20]}
      matching         {"type":"matching","text":"...","pairs":[{"left":"H2O","right":"water"},
                        {"left":"NaCl","right":"salt"}],"distractors":["sugar"]}
      file_upload      {"type":"file_upload","text":"..."}
    Every question also accepts "points" (default 1), "title", "correct_feedback",
    "incorrect_feedback", "general_feedback".

    quiz_type: assignment (graded, default), practice_quiz, graded_survey, survey.
    allowed_attempts: -1 for unlimited (highest score kept). Dates are ISO-8601
    (e.g. 2026-10-09T23:59:59-04:00). Created UNPUBLISHED unless published=True;
    questions are added before publishing so students never see an empty quiz.
    Returns the quiz ID (for add_quiz_questions / get_quiz_details) and a per-question list."""
    course_id = await get_course_id(course_identifier)
    questions: list[dict] = []
    if questions_json and questions_json.strip():
        try:
            questions = parse_questions(questions_json)
        except QuestionError as exc:
            return f"Nothing created. {exc}"

    if quiz_type not in ("assignment", "practice_quiz", "graded_survey", "survey"):
        return "Nothing created. quiz_type must be assignment, practice_quiz, graded_survey, or survey."

    fields: dict = {
        "title": title,
        "description": description,
        "quiz_type": quiz_type,
        "shuffle_answers": shuffle_answers,
        "show_correct_answers": show_correct_answers,
        "one_question_at_a_time": one_question_at_a_time,
        "allowed_attempts": allowed_attempts,
        "published": False,
    }
    if allowed_attempts != 1:
        fields["scoring_policy"] = "keep_highest"
    if time_limit_minutes:
        fields["time_limit"] = int(time_limit_minutes)
    for key, value in (("due_at", due_at), ("unlock_at", unlock_at), ("lock_at", lock_at)):
        if value:
            fields[key] = value

    quiz = await canvas_request("POST", f"/courses/{course_id}/quizzes", json_body={"quiz": fields})
    quiz_id = quiz.get("id")

    added, error = await _add_classic_questions(course_id, quiz_id, questions, 1)

    # Re-save so Canvas recomputes question_count/points_possible, and publish
    # only now that the questions are in place.
    final_fields: dict = {"title": title}
    if published and not error:
        final_fields["published"] = True
    quiz = await canvas_request("PUT", f"/courses/{course_id}/quizzes/{quiz_id}", json_body={"quiz": final_fields})
    count = await _question_count(course_id, quiz_id)

    lines = [
        f"Created classic quiz '{quiz.get('title')}' (quiz ID: {quiz_id}, type: {quiz_type}, "
        f"published: {'Yes' if quiz.get('published') else 'No'})",
        f"Questions: {count}  Points: {_fmt_points(quiz.get('points_possible'))}",
        f"Due: {format_date(quiz.get('due_at'))}",
        f"Link: {quiz.get('html_url', '')}",
    ]
    if added:
        lines.append("\n" + "\n".join(added))
    if error:
        lines.append(
            f"\nSTOPPED: {error}\nThe quiz was left unpublished with the {len(added)} question(s) above. "
            f"Fix that question and pass the remaining ones to add_quiz_questions with quiz_id={quiz_id}."
        )
    elif not questions:
        lines.append(f"\nNo questions yet — add them with add_quiz_questions (quiz_id={quiz_id}).")
    return "\n".join(lines)


async def add_quiz_questions(
    course_identifier: Union[str, int], quiz_id: Union[str, int], questions_json: str
) -> str:
    """Append questions to an existing CLASSIC quiz (quiz_id from list_quizzes or
    create_quiz). questions_json uses the same format as create_quiz. If the quiz is
    already published, Canvas will show the new questions to students immediately."""
    course_id = await get_course_id(course_identifier)
    try:
        questions = parse_questions(questions_json)
    except QuestionError as exc:
        return f"Nothing added. {exc}"
    quiz = await canvas_request("GET", f"/courses/{course_id}/quizzes/{quiz_id}")
    start = await _question_count(course_id, quiz_id) + 1
    added, error = await _add_classic_questions(course_id, quiz_id, questions, start)
    quiz = await canvas_request(
        "PUT", f"/courses/{course_id}/quizzes/{quiz_id}", json_body={"quiz": {"title": quiz.get("title")}}
    )
    lines = [
        f"Added {len(added)} question(s) to classic quiz '{quiz.get('title')}' (ID: {quiz_id}). "
        f"Now {start - 1 + len(added)} questions, {_fmt_points(quiz.get('points_possible'))} points."
    ]
    if added:
        lines.append("\n".join(added))
    if error:
        lines.append(f"STOPPED: {error}\nQuestions after that one were not added.")
    return "\n".join(lines)


async def delete_quiz(course_identifier: Union[str, int], quiz_id: Union[str, int]) -> str:
    """PERMANENTLY delete a classic quiz and all its questions and student
    submissions. This cannot be undone. For a New Quiz use delete_new_quiz."""
    course_id = await get_course_id(course_identifier)
    quiz = await canvas_request("DELETE", f"/courses/{course_id}/quizzes/{quiz_id}")
    title = (quiz or {}).get("title", quiz_id)
    return f"Deleted classic quiz '{title}' (ID: {quiz_id})."
