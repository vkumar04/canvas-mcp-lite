from __future__ import annotations

import json
from typing import Optional, Union

from ..client import CanvasAPIError, canvas_paginated, canvas_request
from ..util import format_date, get_course_id
from .quiz_questions import (
    QuestionError,
    _plain,
    classic_question,
    classic_to_neutral,
    merge_edit,
    parse_questions,
)

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
    """Get one classic quiz: its settings plus EVERY question with its question ID,
    printed in the questions_json format (correct answers marked). Call this before
    update_quiz_question or delete_quiz_question to get the question ID and the
    current content to change."""
    course_id = await get_course_id(course_identifier)
    q = await canvas_request("GET", f"/courses/{course_id}/quizzes/{quiz_id}")
    questions = await canvas_paginated(f"/courses/{course_id}/quizzes/{quiz_id}/questions")
    attempts = q.get("allowed_attempts")
    lines = [
        f"Title: {q.get('title')} (quiz ID: {q.get('id')}, classic)",
        f"Type: {q.get('quiz_type')}",
        f"Published: {'Yes' if q.get('published') else 'No'}",
        f"Due: {format_date(q.get('due_at'))}",
        f"Available: {format_date(q.get('unlock_at'))} → {format_date(q.get('lock_at'))}",
        f"Points: {_fmt_points(sum(float(x.get('points_possible') or 0) for x in questions))}",
        f"Time Limit: {str(q.get('time_limit')) + ' min' if q.get('time_limit') else 'none'}",
        f"Allowed Attempts: {'unlimited' if attempts == -1 else attempts}",
        f"Shuffle Answers: {'Yes' if q.get('shuffle_answers') else 'No'} | "
        f"One Question at a Time: {'Yes' if q.get('one_question_at_a_time') else 'No'} | "
        f"Show Correct Answers: {'Yes' if q.get('show_correct_answers') else 'No'}",
        f"Link: {q.get('html_url', '')}",
        f"\nDescription:\n{q.get('description') or '(none)'}",
        f"\nQuestions ({len(questions)}):",
    ]
    for number, question in enumerate(questions, start=1):
        group = f", in question group {question['quiz_group_id']}" if question.get("quiz_group_id") else ""
        neutral = classic_to_neutral(question)
        lines.append(
            f"\nQ{number} (question ID {question.get('id')}, {question.get('question_type')}, "
            f"{_fmt_points(question.get('points_possible'))} pts{group})"
        )
        if neutral:
            lines.append(json.dumps(neutral, ensure_ascii=False))
        else:
            lines.append(
                f"[This question type can't be edited with these tools — edit it in Canvas.] "
                f"{_plain(question.get('question_text') or '')[:200]}"
            )
    if not questions:
        lines.append("(none yet — add them with add_quiz_questions)")
    return "\n".join(lines)


def _fmt_points(value) -> str:
    return "?" if value is None else (str(int(value)) if float(value) == int(float(value)) else str(value))


async def _question_count(course_id: int, quiz_id) -> int:
    """Count questions directly. The quiz's own question_count is only
    refreshed when the quiz is published, so it lags on unpublished quizzes."""
    return len(await canvas_paginated(f"/courses/{course_id}/quizzes/{quiz_id}/questions"))


async def _totals(course_id: int, quiz_id) -> tuple[int, str]:
    """(question count, total points) from the questions themselves; the
    quiz's own points_possible/question_count only refresh on publish."""
    questions = await canvas_paginated(f"/courses/{course_id}/quizzes/{quiz_id}/questions")
    return len(questions), _fmt_points(sum(float(x.get("points_possible") or 0) for x in questions))


def _published_note(quiz: dict) -> str:
    """Classic serves students a snapshot taken when the quiz was published.
    Only a Canvas UI save (or an unpublish/publish cycle, which re-notifies
    students) refreshes it, and the UI route rejects API tokens (verified live)."""
    if not quiz.get("published"):
        return ""
    return (
        "\nNOTE: This quiz is published. Classic Quizzes keeps showing students the version from when it was "
        f"last saved, so the instructor must open {quiz.get('html_url', 'the quiz')}/edit and click Save for "
        "students to see question changes (the Canvas API can't do this step). Students who already submitted "
        "are not regraded automatically; Canvas offers regrade options on that Save if a correct answer changed."
    )


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
    create_quiz). questions_json uses the same format as create_quiz. On a quiz that
    is already published, students only see the new questions after the instructor
    re-saves the quiz in Canvas (the reply says so)."""
    course_id = await get_course_id(course_identifier)
    try:
        questions = parse_questions(questions_json)
    except QuestionError as exc:
        return f"Nothing added. {exc}"
    quiz = await canvas_request("GET", f"/courses/{course_id}/quizzes/{quiz_id}")
    start = await _question_count(course_id, quiz_id) + 1
    added, error = await _add_classic_questions(course_id, quiz_id, questions, start)
    count, points = await _totals(course_id, quiz_id)
    lines = [
        f"Added {len(added)} question(s) to classic quiz '{quiz.get('title')}' (ID: {quiz_id}). "
        f"Now {count} questions, {points} points."
    ]
    if added:
        lines.append("\n".join(added))
    if error:
        lines.append(f"STOPPED: {error}\nQuestions after that one were not added.")
    if added:
        lines.append(_published_note(quiz))
    return "\n".join(lines).rstrip()


async def update_quiz(
    course_identifier: Union[str, int],
    quiz_id: Union[str, int],
    title: Optional[str] = None,
    description: Optional[str] = None,
    quiz_type: Optional[str] = None,
    time_limit_minutes: Optional[int] = None,
    allowed_attempts: Optional[int] = None,
    shuffle_answers: Optional[bool] = None,
    show_correct_answers: Optional[bool] = None,
    one_question_at_a_time: Optional[bool] = None,
    due_at: Optional[str] = None,
    unlock_at: Optional[str] = None,
    lock_at: Optional[str] = None,
    published: Optional[bool] = None,
) -> str:
    """Change a CLASSIC quiz's settings: title, description, quiz_type (assignment,
    practice_quiz, graded_survey, survey), time limit (0 removes it), allowed_attempts
    (-1 = unlimited, highest score kept), shuffle/one-at-a-time/show-correct-answers,
    dates, and published. Only the arguments you pass change. Dates are ISO-8601; ""
    clears one. To edit questions use update_quiz_question / add_quiz_questions /
    delete_quiz_question. For a New Quiz use update_new_quiz.
    Canvas refuses to unpublish a quiz students have already submitted."""
    course_id = await get_course_id(course_identifier)
    fields: dict = {}
    for key, value in (
        ("title", title),
        ("description", description),
        ("shuffle_answers", shuffle_answers),
        ("show_correct_answers", show_correct_answers),
        ("one_question_at_a_time", one_question_at_a_time),
        ("published", published),
    ):
        if value is not None:
            fields[key] = value
    if quiz_type is not None:
        if quiz_type not in ("assignment", "practice_quiz", "graded_survey", "survey"):
            return "Nothing changed. quiz_type must be assignment, practice_quiz, graded_survey, or survey."
        fields["quiz_type"] = quiz_type
    if time_limit_minutes is not None:
        fields["time_limit"] = int(time_limit_minutes) if time_limit_minutes > 0 else None
    if allowed_attempts is not None:
        fields["allowed_attempts"] = allowed_attempts
        if allowed_attempts != 1:
            fields["scoring_policy"] = "keep_highest"
    for key, value in (("due_at", due_at), ("unlock_at", unlock_at), ("lock_at", lock_at)):
        if value is not None:
            fields[key] = value.strip() or None
    if not fields:
        return "Nothing to update — provide at least one setting."
    quiz = await canvas_request("PUT", f"/courses/{course_id}/quizzes/{quiz_id}", json_body={"quiz": fields})
    attempts = quiz.get("allowed_attempts")
    return (
        f"Updated classic quiz '{quiz.get('title')}' (ID: {quiz_id}).\n"
        f"Published: {'Yes' if quiz.get('published') else 'No'} | Type: {quiz.get('quiz_type')}\n"
        f"Due: {format_date(quiz.get('due_at'))} | Available: {format_date(quiz.get('unlock_at'))} → "
        f"{format_date(quiz.get('lock_at'))}\n"
        f"Time limit: {str(quiz.get('time_limit')) + ' min' if quiz.get('time_limit') else 'none'} | "
        f"Attempts: {'unlimited' if attempts == -1 else attempts}\n"
        f"Link: {quiz.get('html_url', '')}"
    )


async def update_quiz_question(
    course_identifier: Union[str, int],
    quiz_id: Union[str, int],
    question_id: Union[str, int],
    question_json: str,
) -> str:
    """Edit one question on a CLASSIC quiz in place. Get question_id and the current
    content from get_quiz_details, which prints each question in this same format.
    question_json is ONE question object; only the keys you give change, e.g.
      {"points": 2}
      {"text": "Reworded stem?"}
      {"answers": [{"text": "A"}, {"text": "B", "correct": true}]}   (replaces all answers)
      {"type": "true_false", "answer": false}   (changing type keeps stem/points/feedback)
    Format is the same as create_quiz's questions_json. Set a feedback key to "" to
    remove it. On a published quiz, students see the change only after the instructor
    re-saves the quiz in Canvas (the reply says so)."""
    course_id = await get_course_id(course_identifier)
    path = f"/courses/{course_id}/quizzes/{quiz_id}/questions/{question_id}"
    try:
        existing = await canvas_request("GET", path)
    except CanvasAPIError as exc:
        if exc.status_code == 404:
            return f"Question {question_id} isn't on classic quiz {quiz_id}. Use get_quiz_details for question IDs."
        raise
    try:
        question = merge_edit(classic_to_neutral(existing), question_json)
    except QuestionError as exc:
        return f"Nothing changed. {exc}"
    payload = classic_question(question, 0)
    payload.pop("position")  # Canvas reports null positions; sending one reorders
    for field in ("correct_comments", "incorrect_comments", "neutral_comments"):
        payload.setdefault(field, "")
        payload.setdefault(field + "_html", "")
    if question["type"] == "matching":
        payload.setdefault("matching_answer_incorrect_matches", "")
    updated = await canvas_request("PUT", path, json_body={"question": payload})
    quiz = await canvas_request("GET", f"/courses/{course_id}/quizzes/{quiz_id}")
    count, points = await _totals(course_id, quiz_id)
    return (
        f"Updated question {question_id} on classic quiz '{quiz.get('title')}' (ID: {quiz_id}).\n"
        f"Now: {json.dumps(classic_to_neutral(updated) or question, ensure_ascii=False)}\n"
        f"Quiz total: {count} questions, {points} points."
        + _published_note(quiz)
    )


async def delete_quiz_question(
    course_identifier: Union[str, int], quiz_id: Union[str, int], question_id: Union[str, int]
) -> str:
    """PERMANENTLY delete one question from a CLASSIC quiz (question_id from
    get_quiz_details). This cannot be undone; the rest of the quiz is untouched."""
    course_id = await get_course_id(course_identifier)
    path = f"/courses/{course_id}/quizzes/{quiz_id}/questions/{question_id}"
    try:
        existing = await canvas_request("GET", path)
    except CanvasAPIError as exc:
        if exc.status_code == 404:
            return f"Question {question_id} isn't on classic quiz {quiz_id}. Use get_quiz_details for question IDs."
        raise
    await canvas_request("DELETE", path)
    quiz = await canvas_request("GET", f"/courses/{course_id}/quizzes/{quiz_id}")
    count, points = await _totals(course_id, quiz_id)
    return (
        f"Deleted question {question_id} ({_plain(existing.get('question_text') or '')[:80]!r}) from classic quiz "
        f"'{quiz.get('title')}'. Now {count} questions, {points} points."
        + _published_note(quiz)
    )


async def delete_quiz(course_identifier: Union[str, int], quiz_id: Union[str, int]) -> str:
    """PERMANENTLY delete a classic quiz and all its questions and student
    submissions. This cannot be undone. For a New Quiz use delete_new_quiz."""
    course_id = await get_course_id(course_identifier)
    quiz = await canvas_request("DELETE", f"/courses/{course_id}/quizzes/{quiz_id}")
    title = (quiz or {}).get("title", quiz_id)
    return f"Deleted classic quiz '{title}' (ID: {quiz_id})."
