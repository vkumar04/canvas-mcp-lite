"""One question format for both Canvas quiz engines.

The create tools in quizzes.py (Classic Quizzes, /api/v1) and new_quizzes.py
(New Quizzes, /api/quiz/v1) take the same `questions_json` and hand it here.
`parse_questions` validates it into plain dicts; `classic_question` and
`new_quiz_item` turn one of those dicts into the payload each API wants. The
two APIs disagree about everything (question_type strings vs interaction
slugs, answer_weight vs scoring_data, plain text vs HTML), so the format below
is deliberately neutral and the translation is all in this file.

Question format (one object per question, in a JSON array):

  Common keys
    type                one of QUESTION_TYPES
    text                the question stem (plain text or HTML)
    points              default 1
    title               optional short name (defaults to the stem, truncated)
    correct_feedback / incorrect_feedback / general_feedback   optional

  multiple_choice   answers: [{"text": ..., "correct": true|false, "feedback": ...}, ...]
                    exactly one correct; optional "shuffle": true
  multiple_answers  same answers list, one or more correct; optional "partial_credit": true
  true_false        answer: true|false
  short_answer      answers: ["accepted", "spellings", ...]   (case-insensitive)
  essay             no answer keys; optional "grading_notes"
  numerical         answer: number, optional "margin": number
                    -- or --  range: [low, high]
  matching          pairs: [{"left": ..., "right": ...}, ...]; optional "distractors": [...]
                    optional "partial_credit": true
  file_upload       no answer keys
"""

from __future__ import annotations

import html
import json
import re
import uuid
from typing import Any

QUESTION_TYPES = (
    "multiple_choice",
    "multiple_answers",
    "true_false",
    "short_answer",
    "essay",
    "numerical",
    "matching",
    "file_upload",
)

_TAG = re.compile(r"<[^>]+>")


class QuestionError(ValueError):
    """A question in questions_json is malformed. The message names the
    question (1-based) and says what to fix, so it can go straight back to
    the caller as the tool's reply."""


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", text))).strip()


def _html(text: str) -> str:
    """New Quizzes bodies are rich content. Leave HTML alone; wrap plain text."""
    text = text.strip()
    if text.startswith("<"):
        return text
    return f"<p>{html.escape(text, quote=False)}</p>"


def _answers(q: dict, n: int, need_correct: str) -> list[dict]:
    raw = q.get("answers")
    if not isinstance(raw, list) or len(raw) < 2:
        raise QuestionError(f"Question {n} ({q['type']}): 'answers' must be a list of at least 2 answers.")
    answers = []
    for a in raw:
        if isinstance(a, str):
            a = {"text": a}
        if not isinstance(a, dict) or not str(a.get("text", "")).strip():
            raise QuestionError(f"Question {n}: every answer needs non-empty 'text'.")
        answers.append(
            {
                "text": str(a["text"]).strip(),
                "correct": bool(a.get("correct", False)),
                "feedback": str(a.get("feedback") or "").strip(),
            }
        )
    correct = sum(1 for a in answers if a["correct"])
    if need_correct == "one" and correct != 1:
        raise QuestionError(
            f"Question {n} (multiple_choice): mark exactly one answer \"correct\": true (found {correct})."
        )
    if need_correct == "some" and correct == 0:
        raise QuestionError(f"Question {n} (multiple_answers): mark at least one answer \"correct\": true.")
    return answers


def _number(value: Any, n: int, what: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise QuestionError(f"Question {n} (numerical): '{what}' must be a number, got {value!r}.")


def parse_questions(questions_json: str) -> list[dict]:
    """Validate questions_json and return normalized question dicts.
    Raises QuestionError with a message suitable for showing to the caller."""
    try:
        raw = json.loads(questions_json)
    except json.JSONDecodeError as exc:
        raise QuestionError(f"questions_json is not valid JSON: {exc}")
    if isinstance(raw, dict) and isinstance(raw.get("questions"), list):
        raw = raw["questions"]
    if not isinstance(raw, list) or not raw:
        raise QuestionError("questions_json must be a non-empty JSON array of question objects.")

    questions = []
    for i, q in enumerate(raw, start=1):
        if not isinstance(q, dict):
            raise QuestionError(f"Question {i} must be an object.")
        qtype = str(q.get("type", "")).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "mc": "multiple_choice",
            "multiple_choice_question": "multiple_choice",
            "multiple_answer": "multiple_answers",
            "multi_answer": "multiple_answers",
            "multiple_answers_question": "multiple_answers",
            "tf": "true_false",
            "true_false_question": "true_false",
            "fill_in_the_blank": "short_answer",
            "fill_blank": "short_answer",
            "short_answer_question": "short_answer",
            "essay_question": "essay",
            "numeric": "numerical",
            "numerical_question": "numerical",
            "matching_question": "matching",
            "file_upload_question": "file_upload",
        }
        qtype = aliases.get(qtype, qtype)
        if qtype not in QUESTION_TYPES:
            raise QuestionError(
                f"Question {i}: unknown type {q.get('type')!r}. Use one of: {', '.join(QUESTION_TYPES)}."
            )
        text = str(q.get("text") or q.get("question") or "").strip()
        if not text:
            raise QuestionError(f"Question {i}: 'text' (the question stem) is required.")
        points = q.get("points", 1)
        try:
            points = float(points)
        except (TypeError, ValueError):
            raise QuestionError(f"Question {i}: 'points' must be a number.")
        if points < 0:
            raise QuestionError(f"Question {i}: 'points' can't be negative.")
        if points == int(points):
            points = int(points)

        norm: dict[str, Any] = {
            "type": qtype,
            "text": text,
            "points": points,
            "title": str(q.get("title") or "").strip() or _plain(text)[:80],
            "correct_feedback": str(q.get("correct_feedback") or "").strip(),
            "incorrect_feedback": str(q.get("incorrect_feedback") or "").strip(),
            "general_feedback": str(q.get("general_feedback") or q.get("feedback") or "").strip(),
        }

        if qtype == "multiple_choice":
            norm["answers"] = _answers(q, i, "one")
            norm["shuffle"] = bool(q.get("shuffle", False))
        elif qtype == "multiple_answers":
            norm["answers"] = _answers(q, i, "some")
            norm["shuffle"] = bool(q.get("shuffle", False))
            norm["partial_credit"] = bool(q.get("partial_credit", False))
        elif qtype == "true_false":
            answer = q.get("answer", q.get("correct"))
            if isinstance(answer, str):
                answer = {"true": True, "false": False, "t": True, "f": False}.get(answer.strip().lower())
            if not isinstance(answer, bool):
                raise QuestionError(f"Question {i} (true_false): 'answer' must be true or false.")
            norm["answer"] = answer
        elif qtype == "short_answer":
            raw_answers = q.get("answers", q.get("answer"))
            if isinstance(raw_answers, str):
                raw_answers = [raw_answers]
            if not isinstance(raw_answers, list) or not raw_answers:
                raise QuestionError(f"Question {i} (short_answer): 'answers' must list at least one accepted answer.")
            accepted = []
            for a in raw_answers:
                if isinstance(a, dict):
                    a = a.get("text", "")
                a = str(a).strip()
                if a:
                    accepted.append(a)
            if not accepted:
                raise QuestionError(f"Question {i} (short_answer): accepted answers can't be blank.")
            norm["answers"] = accepted
        elif qtype == "essay":
            norm["grading_notes"] = str(q.get("grading_notes") or "").strip()
        elif qtype == "numerical":
            rng = q.get("range")
            if rng is not None:
                if not isinstance(rng, (list, tuple)) or len(rng) != 2:
                    raise QuestionError(f"Question {i} (numerical): 'range' must be [low, high].")
                low, high = _number(rng[0], i, "range"), _number(rng[1], i, "range")
                if low > high:
                    low, high = high, low
                norm["range"] = [low, high]
            elif q.get("answer") is not None:
                norm["answer"] = _number(q["answer"], i, "answer")
                norm["margin"] = _number(q.get("margin", 0), i, "margin")
                if norm["margin"] < 0:
                    raise QuestionError(f"Question {i} (numerical): 'margin' can't be negative.")
            else:
                raise QuestionError(f"Question {i} (numerical): give 'answer' (with optional 'margin') or 'range': [low, high].")
        elif qtype == "matching":
            pairs = q.get("pairs") or q.get("matches")
            if not isinstance(pairs, list) or len(pairs) < 2:
                raise QuestionError(f"Question {i} (matching): 'pairs' must list at least 2 {{\"left\", \"right\"}} objects.")
            norm_pairs = []
            for p in pairs:
                if isinstance(p, (list, tuple)) and len(p) == 2:
                    p = {"left": p[0], "right": p[1]}
                if not isinstance(p, dict) or not str(p.get("left", "")).strip() or not str(p.get("right", "")).strip():
                    raise QuestionError(f"Question {i} (matching): every pair needs non-empty 'left' and 'right'.")
                norm_pairs.append({"left": str(p["left"]).strip(), "right": str(p["right"]).strip()})
            norm["pairs"] = norm_pairs
            distractors = q.get("distractors") or []
            if isinstance(distractors, str):
                distractors = [distractors]
            norm["distractors"] = [str(d).strip() for d in distractors if str(d).strip()]
            norm["partial_credit"] = bool(q.get("partial_credit", False))
        questions.append(norm)
    return questions


def total_points(questions: list[dict]) -> float | int:
    total = sum(q["points"] for q in questions)
    return int(total) if total == int(total) else total


# ---------------------------------------------------------------- Classic


_CLASSIC_TYPES = {
    "multiple_choice": "multiple_choice_question",
    "multiple_answers": "multiple_answers_question",
    "true_false": "true_false_question",
    "short_answer": "short_answer_question",
    "essay": "essay_question",
    "numerical": "numerical_question",
    "matching": "matching_question",
    "file_upload": "file_upload_question",
}


def classic_question(q: dict, position: int) -> dict:
    """The `question` object for POST /courses/:id/quizzes/:id/questions."""
    qtype = q["type"]
    payload: dict[str, Any] = {
        "question_name": q["title"],
        "question_text": q["text"],
        "question_type": _CLASSIC_TYPES[qtype],
        "points_possible": q["points"],
        "position": position,
    }
    if q["correct_feedback"]:
        payload["correct_comments"] = q["correct_feedback"]
    if q["incorrect_feedback"]:
        payload["incorrect_comments"] = q["incorrect_feedback"]
    if q["general_feedback"]:
        payload["neutral_comments"] = q["general_feedback"]

    answers: list[dict] = []
    if qtype in ("multiple_choice", "multiple_answers"):
        for a in q["answers"]:
            entry = {"answer_text": a["text"], "answer_weight": 100 if a["correct"] else 0}
            if a["feedback"]:
                # Canvas's parser reads answer_comment (singular); the plural
                # the API docs list is silently dropped (verified live).
                entry["answer_comment"] = a["feedback"]
                entry["answer_comments"] = a["feedback"]
            answers.append(entry)
    elif qtype == "true_false":
        answers = [
            {"answer_text": "True", "answer_weight": 100 if q["answer"] else 0},
            {"answer_text": "False", "answer_weight": 0 if q["answer"] else 100},
        ]
    elif qtype == "short_answer":
        answers = [{"answer_text": a, "answer_weight": 100} for a in q["answers"]]
    elif qtype == "numerical":
        # Canvas's answer parser reads the form-style names (answer_exact,
        # answer_error_margin, answer_range_start/end); the bare names the API
        # docs list are what it *returns*. Sending only the bare names saves
        # zeros (verified live), so send both.
        if "range" in q:
            low, high = q["range"]
            answers = [
                {
                    "numerical_answer_type": "range_answer",
                    "answer_range_start": low,
                    "answer_range_end": high,
                    "start": low,
                    "end": high,
                    "answer_weight": 100,
                }
            ]
        else:
            answers = [
                {
                    "numerical_answer_type": "exact_answer",
                    "answer_exact": q["answer"],
                    "answer_error_margin": q["margin"],
                    "exact": q["answer"],
                    "margin": q["margin"],
                    "answer_weight": 100,
                }
            ]
    elif qtype == "matching":
        distractors = "\n".join(q["distractors"])
        for p in q["pairs"]:
            entry = {"answer_match_left": p["left"], "answer_match_right": p["right"], "answer_weight": 100}
            if distractors:
                entry["matching_answer_incorrect_matches"] = distractors
            answers.append(entry)
        if distractors:
            payload["matching_answer_incorrect_matches"] = distractors
    if answers:
        payload["answers"] = answers
    return payload


# ------------------------------------------------------------ New Quizzes


def _uuid() -> str:
    return str(uuid.uuid4())


def _blank_body(text: str, answer: str) -> str:
    """working_item_body for a rich-fill-blank item: the stem with the blank
    marked in backticks. A `___` in the stem becomes the blank; otherwise
    the blank goes at the end of the stem."""
    marker = f"`{html.escape(answer, quote=False)}`"
    body = _html(text)
    if "___" in body:
        return re.sub(r"_{3,}", marker, body, count=1)
    if body.endswith("</p>"):
        return body[: -len("</p>")].rstrip() + f" {marker}</p>"
    return body + f"<p>{marker}</p>"


def new_quiz_item(q: dict, position: int) -> dict:
    """The `item` object for POST /api/quiz/v1/courses/:id/quizzes/:id/items."""
    qtype = q["type"]
    entry: dict[str, Any] = {
        "title": q["title"],
        "item_body": _html(q["text"]),
        "calculator_type": "none",
    }
    feedback = {}
    if q["correct_feedback"]:
        feedback["correct"] = _html(q["correct_feedback"])
    if q["incorrect_feedback"]:
        feedback["incorrect"] = _html(q["incorrect_feedback"])
    if q["general_feedback"]:
        feedback["neutral"] = _html(q["general_feedback"])
    if feedback:
        entry["feedback"] = feedback

    if qtype in ("multiple_choice", "multiple_answers"):
        choices = []
        correct_ids = []
        answer_feedback = {}
        for pos, a in enumerate(q["answers"], start=1):
            cid = _uuid()
            choices.append({"id": cid, "position": pos, "item_body": _html(a["text"])})
            if a["correct"]:
                correct_ids.append(cid)
            if a["feedback"]:
                answer_feedback[cid] = _html(a["feedback"])
        entry["interaction_data"] = {"choices": choices}
        shuffle = {"choices": {"to_lock": [], "shuffled": q["shuffle"]}}
        if qtype == "multiple_choice":
            entry["interaction_type_slug"] = "choice"
            entry["properties"] = {"shuffle_rules": shuffle, "vary_points_by_answer": False}
            entry["scoring_data"] = {"value": correct_ids[0]}
            entry["scoring_algorithm"] = "Equivalence"
            if answer_feedback:
                entry["answer_feedback"] = answer_feedback
        else:
            entry["interaction_type_slug"] = "multi-answer"
            entry["properties"] = {"shuffle_rules": shuffle}
            entry["scoring_data"] = {"value": correct_ids}
            entry["scoring_algorithm"] = "PartialScore" if q["partial_credit"] else "AllOrNothing"
    elif qtype == "true_false":
        entry["interaction_type_slug"] = "true-false"
        entry["interaction_data"] = {"true_choice": "True", "false_choice": "False"}
        entry["properties"] = {}
        entry["scoring_data"] = {"value": q["answer"]}
        entry["scoring_algorithm"] = "Equivalence"
    elif qtype == "short_answer":
        # One open-entry blank after the stem. New Quizzes marks blanks in
        # working_item_body with backticks; the stem text itself stays in item_body.
        blank_id = _uuid()
        entry["interaction_type_slug"] = "rich-fill-blank"
        entry["interaction_data"] = {"blanks": [{"id": blank_id, "answer_type": "openEntry"}]}
        entry["properties"] = {"shuffle_rules": {"blanks": {"children": {"0": {"children": None}}}}}
        entry["scoring_data"] = {
            "value": [
                {
                    "id": blank_id,
                    "scoring_data": {
                        "value": list(q["answers"]),
                        "blank_text": q["answers"][0],
                        "ignore_case": True,
                    },
                    "scoring_algorithm": "TextInChoices",
                }
            ],
            "working_item_body": _blank_body(q["text"], q["answers"][0]),
        }
        entry["scoring_algorithm"] = "MultipleMethods"
    elif qtype == "essay":
        entry["interaction_type_slug"] = "essay"
        entry["interaction_data"] = {
            "rce": True,
            "essay": None,
            "word_count": False,
            "file_upload": False,
            "spell_check": False,
            "word_limit_enabled": False,
        }
        entry["properties"] = {}
        entry["scoring_data"] = {"value": q["grading_notes"]}
        entry["scoring_algorithm"] = "None"
    elif qtype == "numerical":
        if "range" in q:
            rule = {
                "id": _uuid(),
                "type": "withinARange",
                "start": str(q["range"][0]),
                "end": str(q["range"][1]),
            }
        elif q["margin"]:
            rule = {
                "id": _uuid(),
                "type": "marginOfError",
                "value": str(q["answer"]),
                "margin": str(q["margin"]),
                "margin_type": "absolute",
            }
        else:
            rule = {"id": _uuid(), "type": "exactResponse", "value": str(q["answer"])}
        entry["interaction_type_slug"] = "numeric"
        entry["interaction_data"] = {}
        entry["properties"] = {}
        entry["scoring_data"] = {"value": [rule]}
        entry["scoring_algorithm"] = "Numeric"
    elif qtype == "matching":
        questions = []
        value = {}
        matches = []
        for p in q["pairs"]:
            qid = _uuid()
            questions.append({"id": qid, "item_body": p["left"]})
            value[qid] = p["right"]
            matches.append({"answer_body": p["right"], "question_id": qid, "question_body": p["left"]})
        entry["interaction_type_slug"] = "matching"
        entry["interaction_data"] = {
            "answers": [p["right"] for p in q["pairs"]] + list(q["distractors"]),
            "questions": questions,
        }
        entry["properties"] = {"shuffle_rules": {"questions": {"shuffled": False}}}
        entry["scoring_data"] = {
            "value": value,
            "edit_data": {"matches": matches, "distractors": list(q["distractors"])},
        }
        entry["scoring_algorithm"] = "PartialDeep" if q["partial_credit"] else "DeepEquals"
    elif qtype == "file_upload":
        entry["interaction_type_slug"] = "file-upload"
        entry["interaction_data"] = {"files_count": "1", "restrict_count": False}
        entry["properties"] = {"allowed_types": "", "restrict_types": False}
        entry["scoring_data"] = {"value": ""}
        entry["scoring_algorithm"] = "None"

    return {
        "entry_type": "Item",
        "position": position,
        "points_possible": q["points"],
        "entry": entry,
    }


def describe(questions: list[dict]) -> str:
    """Short per-question summary for tool replies."""
    lines = []
    for i, q in enumerate(questions, start=1):
        pts = f"{q['points']} pt" + ("" if q["points"] == 1 else "s")
        lines.append(f"  {i}. [{q['type']}, {pts}] {_plain(q['text'])[:90]}")
    return "\n".join(lines)
