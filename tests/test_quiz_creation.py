import asyncio
import json

import pytest

from canvas_mcp_lite.client import CanvasAPIError
from canvas_mcp_lite.tools import new_quizzes, quiz_questions, quizzes
from canvas_mcp_lite.tools.quiz_questions import QuestionError, classic_question, new_quiz_item, parse_questions

QUESTIONS = [
    {
        "type": "multiple_choice",
        "text": "Which planet is closest to the sun?",
        "points": 2,
        "answers": [
            {"text": "Mercury", "correct": True, "feedback": "Yes."},
            {"text": "Venus"},
            {"text": "Mars", "feedback": "Too far out."},
        ],
        "correct_feedback": "Nice.",
    },
    {"type": "true_false", "text": "A square is a rectangle.", "answer": True},
    {
        "type": "multiple_answers",
        "text": "Which are primes?",
        "answers": [{"text": "2", "correct": True}, {"text": "3", "correct": True}, {"text": "4"}],
    },
    {"type": "short_answer", "text": "The capital of France is ___.", "answers": ["Paris", "paris"]},
    {"type": "essay", "text": "Argue for or against.", "points": 10, "grading_notes": "Look for a thesis."},
    {"type": "numerical", "text": "pi to two places", "answer": 3.14, "margin": 0.01},
    {"type": "numerical", "text": "Pick a number in the tens.", "range": [10, 19]},
    {
        "type": "matching",
        "text": "Match the formula.",
        "pairs": [{"left": "H2O", "right": "water"}, {"left": "NaCl", "right": "salt"}],
        "distractors": ["sugar"],
    },
    {"type": "file_upload", "text": "Upload your lab sheet."},
]


@pytest.fixture(autouse=True)
def fake_course_id(monkeypatch):
    async def fake(identifier):
        return 100

    monkeypatch.setattr(quizzes, "get_course_id", fake)
    monkeypatch.setattr(new_quizzes, "get_course_id", fake)
    monkeypatch.setattr(new_quizzes, "CANVAS_API_URL", "https://school.instructure.com/api/v1")


# ------------------------------------------------------------- parsing


def test_parse_normalizes_and_defaults():
    qs = parse_questions(json.dumps(QUESTIONS))
    assert [q["type"] for q in qs] == [
        "multiple_choice", "true_false", "multiple_answers", "short_answer", "essay",
        "numerical", "numerical", "matching", "file_upload",
    ]
    assert qs[0]["points"] == 2 and qs[1]["points"] == 1
    assert qs[0]["title"] == "Which planet is closest to the sun?"
    assert qs[3]["answers"] == ["Paris", "paris"]
    assert qs[5]["answer"] == 3.14 and qs[5]["margin"] == 0.01
    assert qs[6]["range"] == [10.0, 19.0]
    assert quiz_questions.total_points(qs) == 19


def test_parse_accepts_aliases_and_wrapper_object():
    qs = parse_questions(json.dumps({"questions": [
        {"type": "MC", "text": "?", "answers": [{"text": "a", "correct": True}, {"text": "b"}]},
        {"type": "True/False".replace("/", "_"), "text": "?", "answer": "false"},
        {"type": "fill-in-the-blank", "text": "?", "answer": "x"},
    ]}))
    assert [q["type"] for q in qs] == ["multiple_choice", "true_false", "short_answer"]
    assert qs[1]["answer"] is False
    assert qs[2]["answers"] == ["x"]


@pytest.mark.parametrize("bad,fragment", [
    ("not json", "not valid JSON"),
    ("[]", "non-empty"),
    ('[{"type": "haiku", "text": "?"}]', "unknown type"),
    ('[{"type": "essay"}]', "'text'"),
    ('[{"type": "multiple_choice", "text": "?", "answers": [{"text": "a"}, {"text": "b"}]}]', "exactly one"),
    ('[{"type": "multiple_choice", "text": "?", "answers": [{"text": "a", "correct": true}, {"text": "b", "correct": true}]}]', "exactly one"),
    ('[{"type": "multiple_answers", "text": "?", "answers": [{"text": "a"}, {"text": "b"}]}]', "at least one"),
    ('[{"type": "true_false", "text": "?"}]', "true or false"),
    ('[{"type": "numerical", "text": "?"}]', "'answer'"),
    ('[{"type": "matching", "text": "?", "pairs": [{"left": "a", "right": "b"}]}]', "at least 2"),
    ('[{"type": "essay", "text": "?", "points": -1}]', "negative"),
])
def test_parse_rejects_bad_questions(bad, fragment):
    with pytest.raises(QuestionError) as exc:
        parse_questions(bad)
    assert fragment in str(exc.value)
    # Errors name the offending question so the caller can fix it.
    if bad.startswith("["):
        assert "Question 1" in str(exc.value) or "non-empty" in str(exc.value)


# ---------------------------------------------------------- classic payload


def test_classic_payloads():
    qs = parse_questions(json.dumps(QUESTIONS))
    mc = classic_question(qs[0], 1)
    assert mc["question_type"] == "multiple_choice_question"
    assert mc["points_possible"] == 2 and mc["position"] == 1
    assert mc["correct_comments"] == "Nice."
    assert [a["answer_weight"] for a in mc["answers"]] == [100, 0, 0]
    # answer_comment (singular) is the field Canvas actually saves; the documented plural is ignored.
    assert mc["answers"][0]["answer_comment"] == "Yes." and mc["answers"][0]["answer_comments"] == "Yes."
    assert "answer_comment" not in mc["answers"][1]

    tf = classic_question(qs[1], 2)
    assert tf["question_type"] == "true_false_question"
    assert tf["answers"] == [{"answer_text": "True", "answer_weight": 100}, {"answer_text": "False", "answer_weight": 0}]

    ma = classic_question(qs[2], 3)
    assert [a["answer_weight"] for a in ma["answers"]] == [100, 100, 0]

    sa = classic_question(qs[3], 4)
    assert sa["question_type"] == "short_answer_question"
    assert [a["answer_text"] for a in sa["answers"]] == ["Paris", "paris"]

    essay = classic_question(qs[4], 5)
    assert essay["question_type"] == "essay_question" and "answers" not in essay

    exact = classic_question(qs[5], 6)["answers"][0]
    # Canvas's parser reads the answer_* names; the bare ones are what it returns.
    assert exact["numerical_answer_type"] == "exact_answer" and exact["answer_weight"] == 100
    assert exact["answer_exact"] == exact["exact"] == 3.14
    assert exact["answer_error_margin"] == exact["margin"] == 0.01
    rng = classic_question(qs[6], 7)["answers"][0]
    assert rng["numerical_answer_type"] == "range_answer"
    assert rng["answer_range_start"] == rng["start"] == 10.0 and rng["answer_range_end"] == rng["end"] == 19.0

    match = classic_question(qs[7], 8)
    assert match["question_type"] == "matching_question"
    assert match["answers"][0]["answer_match_left"] == "H2O"
    assert match["answers"][0]["answer_match_right"] == "water"
    assert match["answers"][0]["matching_answer_incorrect_matches"] == "sugar"

    assert classic_question(qs[8], 9)["question_type"] == "file_upload_question"


# -------------------------------------------------------- New Quiz payload


def test_new_quiz_item_payloads():
    qs = parse_questions(json.dumps(QUESTIONS))
    mc = new_quiz_item(qs[0], 1)
    assert mc["entry_type"] == "Item" and mc["position"] == 1 and mc["points_possible"] == 2
    e = mc["entry"]
    assert e["interaction_type_slug"] == "choice"
    assert e["item_body"] == "<p>Which planet is closest to the sun?</p>"
    choices = e["interaction_data"]["choices"]
    assert [c["position"] for c in choices] == [1, 2, 3]
    assert e["scoring_data"]["value"] == choices[0]["id"]
    assert e["scoring_algorithm"] == "Equivalence"
    assert e["answer_feedback"] == {choices[0]["id"]: "<p>Yes.</p>", choices[2]["id"]: "<p>Too far out.</p>"}
    assert e["feedback"] == {"correct": "<p>Nice.</p>"}
    assert e["properties"]["shuffle_rules"]["choices"]["shuffled"] is False

    tf = new_quiz_item(qs[1], 2)["entry"]
    assert tf["interaction_type_slug"] == "true-false"
    assert tf["scoring_data"] == {"value": True}

    ma = new_quiz_item(qs[2], 3)["entry"]
    assert ma["interaction_type_slug"] == "multi-answer"
    ids = [c["id"] for c in ma["interaction_data"]["choices"]]
    assert ma["scoring_data"]["value"] == ids[:2]
    assert ma["scoring_algorithm"] == "AllOrNothing"

    sa = new_quiz_item(qs[3], 4)["entry"]
    assert sa["interaction_type_slug"] == "rich-fill-blank"
    blank = sa["interaction_data"]["blanks"][0]
    assert blank["answer_type"] == "openEntry"
    rule = sa["scoring_data"]["value"][0]
    assert rule["id"] == blank["id"]
    assert rule["scoring_data"] == {"value": ["Paris", "paris"], "blank_text": "Paris", "ignore_case": True}
    assert rule["scoring_algorithm"] == "TextInChoices"
    assert sa["scoring_data"]["working_item_body"] == "<p>The capital of France is `Paris`.</p>"

    essay = new_quiz_item(qs[4], 5)
    assert essay["points_possible"] == 10
    assert essay["entry"]["interaction_type_slug"] == "essay"
    assert essay["entry"]["scoring_data"] == {"value": "Look for a thesis."}
    assert essay["entry"]["scoring_algorithm"] == "None"

    margin = new_quiz_item(qs[5], 6)["entry"]["scoring_data"]["value"][0]
    assert margin["type"] == "marginOfError" and margin["value"] == "3.14" and margin["margin"] == "0.01"
    rng = new_quiz_item(qs[6], 7)["entry"]["scoring_data"]["value"][0]
    assert rng == {"id": rng["id"], "type": "withinARange", "start": "10.0", "end": "19.0"}

    match = new_quiz_item(qs[7], 8)["entry"]
    assert match["interaction_type_slug"] == "matching"
    assert match["interaction_data"]["answers"] == ["water", "salt", "sugar"]
    q_ids = [q["id"] for q in match["interaction_data"]["questions"]]
    assert match["scoring_data"]["value"] == {q_ids[0]: "water", q_ids[1]: "salt"}
    assert match["scoring_data"]["edit_data"]["distractors"] == ["sugar"]
    assert match["scoring_algorithm"] == "DeepEquals"

    up = new_quiz_item(qs[8], 9)["entry"]
    assert up["interaction_type_slug"] == "file-upload" and up["scoring_algorithm"] == "None"


def test_blank_marker_falls_back_to_end_of_stem():
    q = parse_questions('[{"type":"short_answer","text":"Name the largest ocean.","answers":["Pacific"]}]')[0]
    body = new_quiz_item(q, 1)["entry"]["scoring_data"]["working_item_body"]
    assert body == "<p>Name the largest ocean. `Pacific`</p>"


def test_exact_numeric_without_margin():
    q = parse_questions('[{"type":"numerical","text":"2+2","answer":4}]')[0]
    rule = new_quiz_item(q, 1)["entry"]["scoring_data"]["value"][0]
    assert rule["type"] == "exactResponse" and rule["value"] == "4.0"


# ------------------------------------------------------------ classic tool


def _classic_fake(monkeypatch, calls, fail_on_question=None):
    state = {"count": 0, "published": False, "title": None}

    async def fake_paginated(path, params=None, max_pages=20):
        assert path.endswith("/questions")
        return [{"id": i} for i in range(state["count"])]

    monkeypatch.setattr(quizzes, "canvas_paginated", fake_paginated)

    async def fake_request(method, path, params=None, json_body=None, data=None):
        calls.append((method, path, json_body))
        if method == "POST" and path.endswith("/quizzes"):
            state["title"] = json_body["quiz"]["title"]
            return {"id": 555, "title": state["title"], "published": False}
        if method == "POST" and path.endswith("/questions"):
            n = json_body["question"]["position"]
            if fail_on_question == n:
                raise CanvasAPIError(400, '{"errors": "bad answers"}', path)
            state["count"] += 1
            return {"id": 9000 + n}
        if method == "PUT":
            state["published"] = bool(json_body["quiz"].get("published", state["published"]))
            return {
                "id": 555, "title": state["title"], "published": state["published"],
                "question_count": 0, "points_possible": float(state["count"]), "due_at": None,
                "html_url": "https://school/quizzes/555",
            }
        if method == "GET":
            return {"id": 555, "title": state["title"], "question_count": 0}
        if method == "DELETE":
            return {"id": 555, "title": "Gone"}
        raise AssertionError(f"unexpected {method} {path}")

    return fake_request


def test_create_quiz_adds_questions_then_publishes(monkeypatch):
    calls = []
    monkeypatch.setattr(quizzes, "canvas_request", _classic_fake(monkeypatch, calls))
    out = asyncio.run(quizzes.create_quiz(
        "ENG101", "Unit 1 Quiz", json.dumps(QUESTIONS[:3]), time_limit_minutes=20,
        allowed_attempts=2, due_at="2026-10-09T23:59:59-04:00", published=True,
    ))
    assert "quiz ID: 555" in out and "published: Yes" in out
    assert "Questions: 3" in out
    create = calls[0][2]["quiz"]
    assert create["published"] is False  # never publish an empty quiz
    assert create["time_limit"] == 20 and create["allowed_attempts"] == 2
    assert create["scoring_policy"] == "keep_highest" and create["due_at"] == "2026-10-09T23:59:59-04:00"
    posts = [c for c in calls if c[0] == "POST" and c[1].endswith("/questions")]
    assert [c[2]["question"]["position"] for c in posts] == [1, 2, 3]
    assert calls[-1][0] == "PUT" and calls[-1][2]["quiz"]["published"] is True
    assert "1. [multiple_choice, 2 pts]" in out


def test_create_quiz_stops_on_rejected_question_and_stays_unpublished(monkeypatch):
    calls = []
    monkeypatch.setattr(quizzes, "canvas_request", _classic_fake(monkeypatch, calls, fail_on_question=2))
    out = asyncio.run(quizzes.create_quiz("ENG101", "Q", json.dumps(QUESTIONS[:3]), published=True))
    assert "STOPPED" in out and "question 2" in out and "HTTP 400" in out
    assert "add_quiz_questions with quiz_id=555" in out
    posts = [c for c in calls if c[0] == "POST" and c[1].endswith("/questions")]
    assert len(posts) == 2  # stopped after the failure
    assert "published" not in calls[-1][2]["quiz"]


def test_create_quiz_rejects_bad_json_before_touching_canvas(monkeypatch):
    calls = []
    monkeypatch.setattr(quizzes, "canvas_request", _classic_fake(monkeypatch, calls))
    out = asyncio.run(quizzes.create_quiz("ENG101", "Q", '[{"type": "essay"}]'))
    assert out.startswith("Nothing created.") and calls == []


def test_add_quiz_questions_positions_after_existing(monkeypatch):
    calls = []
    monkeypatch.setattr(quizzes, "canvas_request", _classic_fake(monkeypatch, calls))
    asyncio.run(quizzes.create_quiz("ENG101", "Q", json.dumps(QUESTIONS[:2])))
    out = asyncio.run(quizzes.add_quiz_questions("ENG101", 555, json.dumps(QUESTIONS[2:4])))
    posts = [c for c in calls if c[0] == "POST" and c[1].endswith("/questions")]
    assert [c[2]["question"]["position"] for c in posts] == [1, 2, 3, 4]
    assert "Added 2 question(s)" in out and "Now 4 questions" in out


def test_delete_quiz(monkeypatch):
    calls = []
    monkeypatch.setattr(quizzes, "canvas_request", _classic_fake(monkeypatch, calls))
    out = asyncio.run(quizzes.delete_quiz("ENG101", 555))
    assert calls == [("DELETE", "/courses/100/quizzes/555", None)]
    assert "Deleted classic quiz 'Gone'" in out


# ----------------------------------------------------------- New Quiz tool


def _new_fake(calls, fail_on_item=None, create_status=None):
    state = {"items": 0, "points": 0.0}

    async def fake_request(method, path, params=None, json_body=None, data=None):
        calls.append((method, path, json_body))
        if method == "POST" and path.endswith("/quizzes"):
            if create_status:
                raise CanvasAPIError(create_status, "nope", path)
            state["points"] = float(json_body["quiz"].get("points_possible") or 0)
            return {"id": "777", "title": json_body["quiz"]["title"], "published": False,
                    "points_possible": state["points"], "due_at": json_body["quiz"].get("due_at")}
        if method == "POST" and path.endswith("/items"):
            n = json_body["item"]["position"]
            if fail_on_item == n:
                raise CanvasAPIError(422, "invalid scoring_data", path)
            state["items"] += 1
            return {"id": str(8000 + n)}
        if method == "PUT" and "/assignments/" in path:
            return {"id": 777, "published": True}
        if method == "GET":
            return {"id": "777", "title": "Q", "points_possible": state["points"]}
        if method == "PATCH":
            state["points"] = json_body["quiz"]["points_possible"]
            return {"id": "777", "title": "Q", "points_possible": state["points"]}
        if method == "DELETE":
            return {"id": "777", "title": "Gone"}
        raise AssertionError(f"unexpected {method} {path}")

    return fake_request


def test_create_new_quiz_uses_quiz_api_and_publishes_via_assignment(monkeypatch):
    calls = []
    monkeypatch.setattr(new_quizzes, "canvas_request", _new_fake(calls))
    out = asyncio.run(new_quizzes.create_new_quiz(
        "ENG101", "Unit 1", json.dumps(QUESTIONS[:3]), time_limit_minutes=30, allowed_attempts=-1,
        shuffle_answers=True, one_question_at_a_time=True, due_at="2026-10-09T23:59:59-04:00", published=True,
    ))
    assert "assignment ID: 777" in out and "published: Yes" in out and "Questions: 3" in out
    assert "Points: 4" in out
    assert "school.instructure.com/courses/100/assignments/777" in out
    create_call = calls[0]
    assert create_call[1] == "https://school.instructure.com/api/quiz/v1/courses/100/quizzes"
    q = create_call[2]["quiz"]
    assert q["points_possible"] == 4
    s = q["quiz_settings"]
    assert s["has_time_limit"] is True and s["session_time_limit_in_seconds"] == 1800
    assert s["shuffle_answers"] is True and s["one_at_a_time_type"] == "question"
    assert s["multiple_attempts"]["multiple_attempts_enabled"] is True
    assert s["multiple_attempts"]["attempt_limit"] is False
    items = [c for c in calls if c[0] == "POST" and c[1].endswith("/items")]
    assert items[0][1] == "https://school.instructure.com/api/quiz/v1/courses/100/quizzes/777/items"
    assert [c[2]["item"]["position"] for c in items] == [1, 2, 3]
    assert items[0][2]["item"]["entry"]["interaction_type_slug"] == "choice"
    publish = calls[-1]
    assert publish[0] == "PUT" and publish[1] == "/courses/100/assignments/777"
    assert publish[2] == {"assignment": {"published": True}}


def test_create_new_quiz_stops_on_rejected_item(monkeypatch):
    calls = []
    monkeypatch.setattr(new_quizzes, "canvas_request", _new_fake(calls, fail_on_item=2))
    out = asyncio.run(new_quizzes.create_new_quiz("ENG101", "Q", json.dumps(QUESTIONS[:3]), published=True))
    assert "STOPPED" in out and "question 2" in out and "HTTP 422" in out
    assert "add_new_quiz_items with assignment_id=777" in out
    assert not any(c[0] == "PUT" for c in calls)  # never published


def test_create_new_quiz_explains_when_engine_unavailable(monkeypatch):
    calls = []
    monkeypatch.setattr(new_quizzes, "canvas_request", _new_fake(calls, create_status=404))
    out = asyncio.run(new_quizzes.create_new_quiz("ENG101", "Q", json.dumps(QUESTIONS[:1])))
    assert "HTTP 404" in out and "create_quiz" in out


def test_add_new_quiz_items_positions_after_existing_and_bumps_points(monkeypatch):
    calls = []
    monkeypatch.setattr(new_quizzes, "canvas_request", _new_fake(calls))

    listings = iter([
        [{"id": "1"}, {"id": "2"}],  # before: positions continue after these
        [{"id": "1"}, {"id": "2"}, {"id": "3", "points_possible": 2.0}, {"id": "4", "points_possible": 1.0}],
    ])

    async def fake_paginated(path, params=None, max_pages=20):
        return next(listings)

    monkeypatch.setattr(new_quizzes, "canvas_paginated", fake_paginated)
    out = asyncio.run(new_quizzes.add_new_quiz_items("ENG101", 777, json.dumps(QUESTIONS[:2])))
    items = [c for c in calls if c[0] == "POST" and c[1].endswith("/items")]
    assert [c[2]["item"]["position"] for c in items] == [3, 4]
    patch = [c for c in calls if c[0] == "PATCH"][0]
    assert patch[2] == {"quiz": {"points_possible": 3.0}}
    assert "Added 2 question(s)" in out and "Now 4 questions, 3 points" in out


def test_delete_new_quiz(monkeypatch):
    calls = []
    monkeypatch.setattr(new_quizzes, "canvas_request", _new_fake(calls))
    out = asyncio.run(new_quizzes.delete_new_quiz("ENG101", 777))
    assert calls[0][:2] == ("DELETE", "https://school.instructure.com/api/quiz/v1/courses/100/quizzes/777")
    assert "Deleted New Quiz 'Gone'" in out
