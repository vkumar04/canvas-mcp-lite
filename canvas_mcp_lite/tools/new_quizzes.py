"""New Quizzes (Quizzes.Next). These live on a separate API — /api/quiz/v1 at
the site root, not /api/v1 — and their reports are generated asynchronously.
Classic quizzes are in quizzes.py."""

from __future__ import annotations

import asyncio
import html
import re
from typing import Optional, Union

import httpx

from ..client import CANVAS_API_URL, CanvasAPIError, canvas_paginated, canvas_request
from ..util import format_date, get_course_id


def _quiz_api(path: str) -> str:
    """Absolute URL on the New Quizzes API. canvas_request's httpx client has
    base_url=/api/v1; an absolute URL bypasses that while keeping the auth
    header, retries, and error handling."""
    return CANVAS_API_URL.rsplit("/api/", 1)[0] + "/api/quiz/v1" + path


_TAG = re.compile(r"<[^>]+>")


def _text(fragment: Optional[str]) -> str:
    """Item bodies and answer choices are HTML fragments; flatten to one line."""
    if not fragment:
        return ""
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", fragment))).strip()


async def list_new_quizzes(course_identifier: Union[str, int]) -> str:
    """List New Quizzes in a course (the quiz engine most current courses use).
    The id shown is the quiz's assignment_id — pass it to get_new_quiz_item_analysis
    or to the assignment/submission tools. Classic quizzes are in list_quizzes."""
    course_id = await get_course_id(course_identifier)
    try:
        quizzes = await canvas_paginated(_quiz_api(f"/courses/{course_id}/quizzes"))
    except CanvasAPIError as exc:
        if exc.status_code in (401, 403):
            return (
                f"Canvas refused access to New Quizzes for course {course_id} (HTTP {exc.status_code}). "
                "Old courses may predate New Quizzes, or the token's user isn't a teacher there."
            )
        raise
    if not quizzes:
        return f"No New Quizzes in course {course_id} (classic quizzes: list_quizzes)."
    lines = []
    for q in quizzes:
        settings = q.get("quiz_settings") or {}
        attempts = settings.get("multiple_attempts") or {}
        attempt_note = ""
        if attempts.get("multiple_attempts_enabled"):
            cap = attempts.get("max_attempts") if attempts.get("attempt_limit") else "unlimited"
            attempt_note = f"\nAttempts: {cap} (keeps {attempts.get('score_to_keep', 'highest')})"
        lines.append(
            f"ID (assignment_id): {q.get('id')}\nTitle: {q.get('title')}\n"
            f"Due: {format_date(q.get('due_at'))}\nPoints: {q.get('points_possible')}\n"
            f"Published: {'Yes' if q.get('published') else 'No'}{attempt_note}"
        )
    return f"New Quizzes in course {course_id}:\n\n" + "\n\n".join(lines)


async def _generate_report(course_id: int, assignment_id, report_type: str, wait_seconds: int) -> dict | list:
    """Ask Canvas to build a report, poll its Progress until done, then fetch
    the JSON. The download URL is a signed, short-lived link on Instructure's
    file store — fetched without the Canvas bearer token, which mustn't be
    sent to another host."""
    try:
        created = await canvas_request(
            "POST",
            _quiz_api(f"/courses/{course_id}/quizzes/{assignment_id}/reports"),
            json_body={"quiz_report": {"report_type": report_type, "format": "json"}},
        )
    except CanvasAPIError as exc:
        if exc.status_code == 409:
            raise RuntimeError(
                "Canvas is already generating this report (another request is in flight). "
                "Try again in a minute."
            ) from exc
        raise
    progress = (created or {}).get("progress") or created or {}
    progress_id = progress.get("id")
    if not progress_id:
        raise RuntimeError(f"Canvas didn't return a report job: {created}")

    deadline = asyncio.get_event_loop().time() + max(10, wait_seconds)
    state = progress.get("workflow_state")
    while state not in ("completed", "failed"):
        if asyncio.get_event_loop().time() > deadline:
            raise RuntimeError(
                f"The report is still {state} after {wait_seconds}s — Canvas queues these "
                "behind other jobs. Call again shortly; it'll pick up the finished report."
            )
        await asyncio.sleep(2)
        progress = await canvas_request("GET", f"/progress/{progress_id}")
        state = progress.get("workflow_state")
    if state == "failed":
        raise RuntimeError(f"Canvas failed to generate the report: {progress.get('message') or 'no details'}")

    url = (progress.get("results") or {}).get("url")
    if not url:
        raise RuntimeError(f"Report completed but Canvas returned no download URL: {progress.get('results')}")
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(url)
    if response.status_code >= 400:
        raise RuntimeError(f"Couldn't download the report (HTTP {response.status_code}) from {url}")
    return response.json()


def _difficulty_label(index: Optional[float]) -> str:
    """difficulty_index is the proportion who got it right (so higher = easier)."""
    if index is None:
        return ""
    if index < 0.3:
        return "HARD"
    if index > 0.9:
        return "easy"
    return "moderate"


def _discrimination_label(index: Optional[float]) -> str:
    """How well the item separates high from low scorers. Below 0.2 the item
    isn't telling you much; negative means strong students got it wrong more."""
    if index is None:
        return ""
    if index < 0:
        return "NEGATIVE — review this item"
    if index < 0.2:
        return "weak"
    if index < 0.4:
        return "fair"
    return "good"


def format_item_analysis(report: list, items: list, title: str, assignment_id) -> str:
    """Render Canvas's item_analysis JSON: most-missed ranking first, then
    per-question stats and answer distributions. `items` (from the items
    endpoint) supplies question order; the report itself is unordered."""
    position_of = {}
    for entry in items or []:
        inner = entry.get("entry") or {}
        if inner.get("id") is not None:
            try:
                position_of[int(inner["id"])] = entry.get("position") or 0
            except (TypeError, ValueError):
                pass

    def key(indexed):
        original_index, item = indexed
        item_id = (item.get("metadata") or {}).get("item_id")
        # Unknown position: keep the report's own order after the known ones.
        return position_of.get(item_id, 10**6 + original_index)

    ordered = [item for _, item in sorted(enumerate(report or []), key=key)]
    if not ordered:
        return f"Item analysis for {title} (assignment_id={assignment_id}): no items — has anyone taken it yet?"

    rows = []
    for number, item in enumerate(ordered, start=1):
        meta = item.get("metadata") or {}
        scores = item.get("scores") or {}
        summary = item.get("answer_summary") or {}
        aggregate = summary.get("aggregate") or {}
        correct = aggregate.get("correct") or {}
        incorrect = aggregate.get("incorrect") or {}
        no_answer = summary.get("no_answer") or {}
        rows.append(
            {
                "number": position_of.get(meta.get("item_id"), number),
                "item_id": meta.get("item_id"),
                "type": meta.get("interaction_type", "?"),
                "stem": _text(meta.get("item_title")) or _text(meta.get("item_body")),
                "scores": scores,
                "correct": correct.get("count") or 0,
                "correct_pct": correct.get("percentage"),
                "incorrect": incorrect.get("count") or 0,
                "incorrect_pct": incorrect.get("percentage") or 0,
                "no_answer": no_answer.get("count") or 0,
                "summary": summary,
            }
        )

    respondents = max((r["correct"] + r["incorrect"] + r["no_answer"]) for r in rows)
    out = [
        f"Item analysis: {title} (assignment_id={assignment_id})",
        f"Students analyzed: {respondents} | Questions: {len(rows)}",
        "",
        "MOST MISSED (ranked by % incorrect):",
    ]
    ranked = sorted(rows, key=lambda r: (-(r["incorrect_pct"] or 0), -r["incorrect"], r["number"]))
    for r in ranked:
        stem = r["stem"] if len(r["stem"]) <= 90 else r["stem"][:87] + "..."
        out.append(f"  Q{r['number']}: {r['incorrect_pct']}% incorrect ({r['incorrect']}/{r['correct'] + r['incorrect']}) — {stem}")

    out += ["", "PER-QUESTION STATISTICS:"]
    for r in rows:
        s = r["scores"]
        diff = s.get("difficulty_index")
        disc = s.get("discrimination_index")
        block = [
            f"Q{r['number']} [{r['type']}] item_id={r['item_id']}",
            f"  {r['stem']}",
            f"  Correct: {r['correct']} ({r['correct_pct']}%) | Incorrect: {r['incorrect']} ({r['incorrect_pct']}%)"
            + (f" | No answer: {r['no_answer']}" if r["no_answer"] else ""),
            f"  Mean score: {s.get('mean')}/{s.get('points_possible')} | Median: {s.get('median')}",
            f"  Difficulty index: {diff} ({_difficulty_label(diff)}) | "
            f"Discrimination index: {disc} ({_discrimination_label(disc)}) | "
            f"Point-biserial r: {s.get('pearson_correlation')}",
        ]
        choices = r["summary"].get("choices")
        if choices:
            block.append("  Answer distribution:")
            for choice in choices:
                marker = "✓" if choice.get("correct") else " "
                block.append(
                    f"    {marker} {choice.get('count', 0):>3} ({choice.get('percentage', 0)}%)  {_text(choice.get('body'))}"
                )
            if r["type"] == "multi-answer":
                block.append("    (multi-answer: counts are per option selected; the item is only 'correct' when every option matches)")
        questions = r["summary"].get("questions")
        if questions:
            block.append("  Per-prompt results:")
            for prompt in questions.values():
                answers = list((prompt.get("answers") or {}).values())
                right = next((a for a in answers if a.get("correct")), None)
                wrong = sorted((a for a in answers if not a.get("correct") and (a.get("count") or 0) > 0), key=lambda a: -(a.get("count") or 0))
                line = f"    {_text(prompt.get('body'))}: "
                line += f"{right.get('count', 0)} chose the correct match" if right else "no correct answer recorded"
                if wrong:
                    line += "; most common wrong pick: " + ", ".join(
                        f"\"{_text(a.get('body'))[:50]}\" ({a.get('count')})" for a in wrong[:2]
                    )
                block.append(line)
        out.append("\n".join(block))
        out.append("")

    out.append(
        "Reading the indices: difficulty index = share who answered correctly (below 0.3 is hard, above 0.9 easy). "
        "Discrimination index compares top vs bottom scorers (below 0.2 the question isn't separating them; negative means "
        "stronger students missed it more — check the key)."
    )
    return "\n".join(out)


async def get_new_quiz_item_analysis(
    course_identifier: Union[str, int], assignment_id: Union[str, int], wait_seconds: int = 90
) -> str:
    """Question-by-question statistics for a New Quiz after students have taken it:
    the most-missed questions ranked, and for every question the correct/incorrect
    counts, mean score, difficulty and discrimination indices, and how many students
    picked each answer choice (per prompt for matching items). Get assignment_id from
    list_new_quizzes. Canvas builds the report on demand, so this takes a few seconds;
    if it times out, call again. Reflects each student's kept score attempt."""
    course_id = await get_course_id(course_identifier)
    try:
        quiz = await canvas_request("GET", _quiz_api(f"/courses/{course_id}/quizzes/{assignment_id}"))
    except CanvasAPIError as exc:
        if exc.status_code == 404:
            return (
                f"assignment_id {assignment_id} isn't a New Quiz in course {course_id}. "
                "Use list_new_quizzes for ids (classic quizzes have no item analysis here)."
            )
        raise
    title = quiz.get("title") or f"quiz {assignment_id}"

    try:
        items = await canvas_paginated(_quiz_api(f"/courses/{course_id}/quizzes/{assignment_id}/items"))
    except CanvasAPIError:
        items = []
    try:
        report = await _generate_report(course_id, assignment_id, "item_analysis", wait_seconds)
    except RuntimeError as exc:
        return f"Couldn't get item analysis for {title}: {exc}"
    if not isinstance(report, list):
        return f"Unexpected report format for {title}: {str(report)[:300]}"
    return format_item_analysis(report, items, title, assignment_id)
