"""One question format for both Canvas quiz engines.

The create tools in quizzes.py (Classic Quizzes, /api/v1) and new_quizzes.py
(New Quizzes, /api/quiz/v1) take the same `questions_json` and hand it here.
`parse_questions` validates it into plain dicts; `classic_question` and
`new_quiz_item` turn one of those dicts into the payload each API wants. The
two APIs disagree about everything (question_type strings vs interaction
slugs, answer_weight vs scoring_data, plain text vs HTML), so the format below
is deliberately neutral and the translation is all in this file. The edit
tools go the other way too: `classic_to_neutral` / `new_quiz_to_neutral`
print existing questions in this format and `merge_edit` applies a partial
edit on top of one.

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
    for key, field in (
        ("correct_feedback", "correct_comments"),
        ("incorrect_feedback", "incorrect_comments"),
        ("general_feedback", "neutral_comments"),
    ):
        if q[key]:
            # HTML feedback (e.g. read back from a question built in the
            # Canvas editor) belongs in the *_html field, or it shows as tags.
            payload[field] = _plain(q[key]) if q[key].startswith("<") else q[key]
            if q[key].startswith("<"):
                payload[field + "_html"] = q[key]

    answers: list[dict] = []
    if qtype in ("multiple_choice", "multiple_answers"):
        for a in q["answers"]:
            entry = {"answer_text": a["text"], "answer_weight": 100 if a["correct"] else 0}
            if a["text"].startswith("<"):
                entry["answer_text"] = _plain(a["text"])
                entry["answer_html"] = a["text"]
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


# ------------------------------------------------- reading back (for edits)
#
# The read tools print each existing question in the questions_json format,
# so the caller can copy it, change a field, and pass it to an update tool.
# Types the format can't express (Classic calculated/fill-in-multiple-blanks,
# New Quizzes ordering/hot-spot/stimulus, multi-blank fill-ins, ...) come
# back as None and are reported as "edit in Canvas".

# Some Canvas accounts (Charlotte's DesignPlus) inject a stylesheet <link>
# and a <script> into every Classic question_text; strip them on the way out.
_INJECTED = re.compile(r"<link\b[^>]*>|<script\b[^>]*>.*?</script>", re.I | re.S)
_ONE_PARAGRAPH = re.compile(r"<p>([^<]*)</p>")
_BLANK_SPAN = re.compile(r"<span\b[^>]*\bid=\"blank_[^\"]*\"[^>]*>\s*</span>")


def _readable(fragment: Any) -> str:
    """Canvas HTML back to what a caller would have written: injected tags
    dropped, a lone <p>…</p> unwrapped to plain text, real HTML kept."""
    text = _INJECTED.sub("", str(fragment or "")).strip()
    match = _ONE_PARAGRAPH.fullmatch(text)
    if match:
        return html.unescape(match.group(1)).strip()
    if "<" not in text:
        return html.unescape(text).strip()
    return text


def _num(value: Any) -> float | int:
    value = float(value or 0)
    return int(value) if value == int(value) else value


def _with_common(out: dict, title: str, feedback: dict) -> dict:
    if title and title not in (_plain(out["text"])[:80], "Question"):
        out["title"] = title
    for key in ("correct_feedback", "incorrect_feedback", "general_feedback"):
        value = _readable(feedback.get(key))
        if value:
            out[key] = value
    return out


_CLASSIC_BACK = {v: k for k, v in _CLASSIC_TYPES.items()}


def classic_to_neutral(qd: dict) -> dict | None:
    """A Classic quiz question (GET .../questions) in questions_json form."""
    qtype = _CLASSIC_BACK.get(qd.get("question_type"))
    if not qtype:
        return None
    out: dict[str, Any] = {"type": qtype, "text": _readable(qd.get("question_text")), "points": _num(qd.get("points_possible"))}
    answers = qd.get("answers") or []
    if qtype in ("multiple_choice", "multiple_answers"):
        out["answers"] = []
        for a in answers:
            entry: dict[str, Any] = {"text": _readable(a.get("html") or a.get("text"))}
            if (a.get("weight") or 0) > 0:
                entry["correct"] = True
            feedback = _readable(a.get("comments_html") or a.get("comments"))
            if feedback:
                entry["feedback"] = feedback
            out["answers"].append(entry)
    elif qtype == "true_false":
        true = next((a for a in answers if str(a.get("text", "")).strip().lower() == "true"), None)
        if true is None:
            return None
        out["answer"] = (true.get("weight") or 0) > 0
    elif qtype == "short_answer":
        out["answers"] = [str(a.get("text", "")).strip() for a in answers if str(a.get("text", "")).strip()]
    elif qtype == "numerical":
        if len(answers) != 1:
            return None
        a = answers[0]
        kind = a.get("numerical_answer_type")
        if kind == "range_answer":
            out["range"] = [_num(a.get("start")), _num(a.get("end"))]
        elif kind == "exact_answer":
            out["answer"] = _num(a.get("exact"))
            if a.get("margin"):
                out["margin"] = _num(a.get("margin"))
        else:  # precision_answer has no equivalent in the shared format
            return None
    elif qtype == "matching":
        out["pairs"] = [{"left": str(a.get("left", "")), "right": str(a.get("right", ""))} for a in answers]
        distractors = [d.strip() for d in str(qd.get("matching_answer_incorrect_matches") or "").split("\n") if d.strip()]
        if distractors:
            out["distractors"] = distractors
    feedback = {
        "correct_feedback": qd.get("correct_comments_html") or qd.get("correct_comments"),
        "incorrect_feedback": qd.get("incorrect_comments_html") or qd.get("incorrect_comments"),
        "general_feedback": qd.get("neutral_comments_html") or qd.get("neutral_comments"),
    }
    return _with_common(out, str(qd.get("question_name") or "").strip(), feedback)


_NEW_QUIZ_BACK = {
    "choice": "multiple_choice",
    "multi-answer": "multiple_answers",
    "true-false": "true_false",
    "rich-fill-blank": "short_answer",
    "essay": "essay",
    "numeric": "numerical",
    "matching": "matching",
    "file-upload": "file_upload",
}


def new_quiz_slug(qtype: str) -> str:
    """The interaction_type_slug new_quiz_item produces for a question type."""
    return {v: k for k, v in _NEW_QUIZ_BACK.items()}[qtype]


def new_quiz_to_neutral(item: dict) -> dict | None:
    """A New Quizzes item (GET .../items) in questions_json form."""
    if item.get("entry_type") != "Item":
        return None
    e = item.get("entry") or {}
    qtype = _NEW_QUIZ_BACK.get(e.get("interaction_type_slug"))
    if not qtype:
        return None
    data = e.get("interaction_data") or {}
    scoring = e.get("scoring_data") or {}
    body = e.get("item_body") or ""
    if qtype == "short_answer":
        body = _BLANK_SPAN.sub("___", body)
    out: dict[str, Any] = {"type": qtype, "text": _readable(body), "points": _num(item.get("points_possible"))}

    if qtype in ("multiple_choice", "multiple_answers"):
        value = scoring.get("value")
        correct = set(value if isinstance(value, list) else [value])
        answer_feedback = e.get("answer_feedback") or {}
        out["answers"] = []
        for choice in sorted(data.get("choices") or [], key=lambda c: c.get("position") or 0):
            entry: dict[str, Any] = {"text": _readable(choice.get("item_body"))}
            if choice.get("id") in correct:
                entry["correct"] = True
            feedback = _readable(answer_feedback.get(choice.get("id")))
            if feedback:
                entry["feedback"] = feedback
            out["answers"].append(entry)
        shuffle = (((e.get("properties") or {}).get("shuffle_rules") or {}).get("choices") or {}).get("shuffled")
        if shuffle:
            out["shuffle"] = True
        if qtype == "multiple_answers" and e.get("scoring_algorithm") == "PartialScore":
            out["partial_credit"] = True
    elif qtype == "true_false":
        out["answer"] = bool(scoring.get("value"))
    elif qtype == "short_answer":
        blanks = data.get("blanks") or []
        rules = scoring.get("value") or []
        if len(blanks) != 1 or len(rules) != 1 or blanks[0].get("answer_type") != "openEntry":
            return None
        accepted = (rules[0].get("scoring_data") or {}).get("value")
        out["answers"] = [str(a) for a in (accepted if isinstance(accepted, list) else [accepted]) if str(a or "").strip()]
    elif qtype == "essay":
        notes = scoring.get("value")
        if isinstance(notes, str) and notes.strip():
            out["grading_notes"] = notes.strip()
    elif qtype == "numerical":
        rules = scoring.get("value") or []
        if len(rules) != 1:
            return None
        rule = rules[0]
        if rule.get("type") == "exactResponse":
            out["answer"] = _num(rule.get("value"))
        elif rule.get("type") == "marginOfError":
            value, margin = float(rule.get("value") or 0), float(rule.get("margin") or 0)
            if rule.get("margin_type") == "percent":
                margin = abs(value) * margin / 100
            out["answer"], out["margin"] = _num(value), _num(margin)
        elif rule.get("type") == "withinARange":
            out["range"] = [_num(rule.get("start")), _num(rule.get("end"))]
        else:
            return None
    elif qtype == "matching":
        rights = scoring.get("value") or {}
        out["pairs"] = [
            {"left": _readable(p.get("item_body")), "right": str(rights.get(p.get("id"), ""))}
            for p in data.get("questions") or []
        ]
        used = set(rights.values())
        distractors = [a for a in data.get("answers") or [] if a not in used]
        if distractors:
            out["distractors"] = distractors
        if e.get("scoring_algorithm") == "PartialDeep":
            out["partial_credit"] = True
    feedback = e.get("feedback") or {}
    return _with_common(
        out,
        str(e.get("title") or "").strip(),
        {
            "correct_feedback": feedback.get("correct"),
            "incorrect_feedback": feedback.get("incorrect"),
            "general_feedback": feedback.get("neutral"),
        },
    )


# Read-only answer keys for the types above that come back as None, so a
# caller can still check what Canvas accepts (e.g. after an edit in the UI).


def _quoted(values: list) -> str:
    return ", ".join(f'"{v}"' for v in values) or "(none)"


def classic_answer_key(qd: dict) -> list[str]:
    """One line per blank (fill-in-multiple-blanks, multiple dropdowns) or
    one line of correct answers for any other Classic type with answers."""
    answers = qd.get("answers") or []
    qtype = qd.get("question_type")
    if qtype in ("fill_in_multiple_blanks_question", "multiple_dropdowns_question"):
        blanks: dict[str, list[dict]] = {}
        for a in answers:
            blanks.setdefault(str(a.get("blank_id") or "?"), []).append(a)
        lines = []
        for blank, options in blanks.items():
            correct = [str(a.get("text", "")).strip() for a in options if (a.get("weight") or 0) > 0]
            if qtype == "fill_in_multiple_blanks_question":
                lines.append(f"[{blank}] accepts: {_quoted(correct)}")
            else:
                others = [str(a.get("text", "")).strip() for a in options if not (a.get("weight") or 0) > 0]
                lines.append(f"[{blank}] correct: {_quoted(correct)}; other options: {_quoted(others)}")
        return lines
    correct = [str(a.get("text", "")).strip() for a in answers if (a.get("weight") or 0) > 0 and str(a.get("text", "")).strip()]
    return [f"Correct: {_quoted(correct)}"] if correct else []


_MATCH_RULES = {
    "TextContainsAnswer": " (response must contain it)",
    "TextCloseEnough": " (close spelling accepted)",
    "TextRegex": " (regular expression)",
}


def new_quiz_answer_key(item: dict) -> list[str]:
    """One line per blank of a fill-in-the-blank item (open entry, dropdown,
    or word bank). Other item types return []."""
    e = item.get("entry") or {}
    if e.get("interaction_type_slug") != "rich-fill-blank":
        return []
    rules = {r.get("id"): r for r in (e.get("scoring_data") or {}).get("value") or [] if isinstance(r, dict)}
    lines = []
    for n, blank in enumerate((e.get("interaction_data") or {}).get("blanks") or [], start=1):
        rule = rules.get(blank.get("id")) or {}
        value = (rule.get("scoring_data") or {}).get("value")
        kind = blank.get("answer_type") or "?"
        if kind == "openEntry":
            accepted = [str(v) for v in (value if isinstance(value, list) else [value]) if str(v or "").strip()]
            lines.append(f"Blank {n} (typed) accepts: {_quoted(accepted)}{_MATCH_RULES.get(rule.get('scoring_algorithm'), '')}")
        else:
            choices = {c.get("id"): _plain(str(c.get("item_body") or "")) for c in blank.get("choices") or []}
            correct = choices.get(value, (rule.get("scoring_data") or {}).get("blank_text") or "?")
            others = [text for cid, text in choices.items() if cid != value]
            lines.append(f"Blank {n} ({kind}) correct: \"{correct}\"; other options: {_quoted(others)}")
    return lines


_COMMON_KEYS = ("text", "points", "title", "correct_feedback", "incorrect_feedback", "general_feedback")


def merge_edit(current: dict | None, question_json: str) -> dict:
    """Apply an edit to an existing question and validate the result.

    question_json is one question object. Keys it gives replace the current
    ones, so {"points": 2} alone re-weights a question. Changing "type"
    keeps only the stem, points, title, and feedback from the old question."""
    try:
        patch = json.loads(question_json)
    except json.JSONDecodeError as exc:
        raise QuestionError(f"question_json is not valid JSON: {exc}")
    if isinstance(patch, list) and len(patch) == 1:
        patch = patch[0]
    if not isinstance(patch, dict) or not patch:
        raise QuestionError("question_json must be ONE question object (the fields to change, or the whole question).")
    base = dict(current or {})
    if "type" in patch and current and str(patch["type"]).strip().lower() != current.get("type"):
        base = {k: v for k, v in current.items() if k in _COMMON_KEYS}
    if not current and "type" not in patch:
        raise QuestionError("This question isn't in a format these tools can read, so give the WHOLE question (including \"type\").")
    try:
        return parse_questions(json.dumps([{**base, **patch}]))[0]
    except QuestionError as exc:
        raise QuestionError(str(exc).replace("Question 1", "The edited question", 1))


def describe(questions: list[dict]) -> str:
    """Short per-question summary for tool replies."""
    lines = []
    for i, q in enumerate(questions, start=1):
        pts = f"{q['points']} pt" + ("" if q["points"] == 1 else "s")
        lines.append(f"  {i}. [{q['type']}, {pts}] {_plain(q['text'])[:90]}")
    return "\n".join(lines)
