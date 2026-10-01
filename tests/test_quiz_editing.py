import asyncio
import json

import pytest

from canvas_mcp_lite.client import CanvasAPIError
from canvas_mcp_lite.tools import new_quizzes, quizzes
from canvas_mcp_lite.tools.quiz_questions import (
    QuestionError,
    _readable,
    classic_to_neutral,
    merge_edit,
    new_quiz_item,
    new_quiz_to_neutral,
    parse_questions,
)

from test_quiz_creation import QUESTIONS

DP = (
    '<link rel="stylesheet" href="https://x/dp_app.css">{}'
    '<script src="https://x/dp_app.js"></script>'
)


@pytest.fixture(autouse=True)
def fake_course_id(monkeypatch):
    async def fake(identifier):
        return 100

    monkeypatch.setattr(quizzes, "get_course_id", fake)
    monkeypatch.setattr(new_quizzes, "get_course_id", fake)
    monkeypatch.setattr(new_quizzes, "CANVAS_API_URL", "https://school.instructure.com/api/v1")


def _same(neutral, original):
    return parse_questions(json.dumps([neutral]))[0] == parse_questions(json.dumps([original]))[0]


# ------------------------------------------------------- reading back


def test_readable_strips_injected_tags_and_unwraps_paragraphs():
    assert _readable(DP.format("Fish &amp; chips?")) == "Fish & chips?"
    assert _readable("<p>A &lt; B</p>") == "A < B"
    assert _readable("<p>one</p><p>two</p>") == "<p>one</p><p>two</p>"
    assert _readable(None) == ""


# Shapes as Canvas returns them from GET /quizzes/:id/questions (verified live).
CLASSIC_READ = [
    {"question_type": "multiple_choice_question", "question_name": "Which planet is closest to the sun?",
     "question_text": DP.format("Which planet is closest to the sun?"), "points_possible": 2.0,
     "correct_comments": "Nice.", "correct_comments_html": "",
     "answers": [{"text": "Mercury", "html": "", "comments": "Yes.", "weight": 100.0},
                 {"text": "Venus", "html": "", "comments": "", "weight": 0.0},
                 {"text": "Mars", "html": "", "comments": "Too far out.", "weight": 0.0}]},
    {"question_type": "true_false_question", "question_text": "A square is a rectangle.", "points_possible": 1.0,
     "answers": [{"text": "True", "weight": 100}, {"text": "False", "weight": 0}]},
    {"question_type": "multiple_answers_question", "question_text": "Which are primes?", "points_possible": 1.0,
     "answers": [{"text": "2", "weight": 100}, {"text": "3", "weight": 100}, {"text": "4", "weight": 0}]},
    {"question_type": "short_answer_question", "question_text": "The capital of France is ___.", "points_possible": 1,
     "answers": [{"text": "Paris", "weight": 100}, {"text": "paris", "weight": 100}]},
    {"question_type": "essay_question", "question_text": "Argue for or against.", "points_possible": 10.0, "answers": []},
    {"question_type": "numerical_question", "question_text": "pi to two places", "points_possible": 1.0,
     "answers": [{"numerical_answer_type": "exact_answer", "exact": 3.14, "margin": 0.01, "weight": 100}]},
    {"question_type": "numerical_question", "question_text": "Pick a number in the tens.", "points_possible": 1.0,
     "answers": [{"numerical_answer_type": "range_answer", "start": 10, "end": 19, "weight": 100}]},
    {"question_type": "matching_question", "question_text": "Match the formula.", "points_possible": 1.0,
     "matching_answer_incorrect_matches": "sugar",
     "answers": [{"left": "H2O", "right": "water"}, {"left": "NaCl", "right": "salt"}]},
    {"question_type": "file_upload_question", "question_text": "Upload your lab sheet.", "points_possible": 1.0},
]


def test_classic_readback_round_trips_every_type():
    for read, original in zip(CLASSIC_READ, QUESTIONS):
        neutral = classic_to_neutral(read)
        expected = {k: v for k, v in original.items() if k != "grading_notes"}  # Classic has no grading notes
        assert _same(neutral, expected), (neutral, original)
    assert "title" not in classic_to_neutral(CLASSIC_READ[0])  # echo of the stem is dropped


def test_classic_readback_gives_up_on_types_the_format_cannot_express():
    assert classic_to_neutral({"question_type": "calculated_question", "question_text": "x"}) is None
    assert classic_to_neutral({"question_type": "numerical_question", "question_text": "x",
                               "answers": [{"numerical_answer_type": "precision_answer"}]}) is None


def test_new_quiz_readback_round_trips_every_type():
    for n, original in enumerate(QUESTIONS, start=1):
        item = new_quiz_item(parse_questions(json.dumps([original]))[0], n)
        item["entry_type"] = "Item"
        if original["type"] == "short_answer":
            # Canvas stores the blank as a span in item_body (verified live).
            item["entry"]["item_body"] = '<p>The capital of France is <span id="blank_abc"></span>.</p>'
        assert _same(new_quiz_to_neutral(item), original), (new_quiz_to_neutral(item), original)


def test_new_quiz_readback_skips_stimulus_and_unknown_items():
    assert new_quiz_to_neutral({"entry_type": "Stimulus", "entry": {}}) is None
    assert new_quiz_to_neutral({"entry_type": "Item", "entry": {"interaction_type_slug": "ordering"}}) is None


def test_merge_edit_patches_only_given_keys():
    current = classic_to_neutral(CLASSIC_READ[0])
    q = merge_edit(current, '{"points": 3}')
    assert q["points"] == 3 and len(q["answers"]) == 3 and q["correct_feedback"] == "Nice."
    q = merge_edit(current, '{"correct_feedback": ""}')
    assert q["correct_feedback"] == ""
    q = merge_edit(current, '[{"type": "true_false", "answer": false}]')
    assert q["type"] == "true_false" and q["answer"] is False and q["points"] == 2
    assert q["text"] == "Which planet is closest to the sun?"


@pytest.mark.parametrize("current,patch,fragment", [
    ({"type": "essay", "text": "x"}, "not json", "not valid JSON"),
    ({"type": "essay", "text": "x"}, "[]", "ONE question"),
    (None, '{"points": 2}', "WHOLE question"),
    ({"type": "multiple_choice", "text": "x", "answers": [{"text": "a", "correct": True}, {"text": "b"}]},
     '{"answers": [{"text": "a"}, {"text": "b"}]}', "The edited question"),
])
def test_merge_edit_errors(current, patch, fragment):
    with pytest.raises(QuestionError, match=fragment):
        merge_edit(current, patch)


# ---------------------------------------------------------- classic tools


def _classic_fake(monkeypatch, calls, published=False, question=None, status=None):
    questions = [{"id": 1, "points_possible": 2.0}, {"id": 2, "points_possible": 1.0}]

    async def fake_paginated(path, params=None, max_pages=20):
        return questions

    monkeypatch.setattr(quizzes, "canvas_paginated", fake_paginated)

    async def fake_request(method, path, params=None, json_body=None, data=None):
        calls.append((method, path, json_body))
        if status and "/questions/" in path:
            raise CanvasAPIError(status, "nope", path)
        if "/questions/" in path:
            if method == "GET":
                return question or CLASSIC_READ[0]
            if method == "PUT":
                return {**json_body["question"], "answers": []}
            return None
        if method == "PUT":
            return {"id": 555, "title": "Q", "published": True, "allowed_attempts": -1,
                    "html_url": "https://school/quizzes/555", **json_body["quiz"]}
        return {"id": 555, "title": "Q", "published": published, "html_url": "https://school/quizzes/555"}

    return fake_request


def test_update_quiz_question_merges_and_clears_feedback(monkeypatch):
    calls = []
    monkeypatch.setattr(quizzes, "canvas_request", _classic_fake(monkeypatch, calls))
    out = asyncio.run(quizzes.update_quiz_question("ENG101", 555, 1, '{"points": 4, "correct_feedback": ""}'))
    put = next(c for c in calls if c[0] == "PUT")
    assert put[1] == "/courses/100/quizzes/555/questions/1"
    q = put[2]["question"]
    assert "position" not in q  # Canvas reports null positions; sending one reorders
    assert q["points_possible"] == 4 and q["correct_comments"] == "" and q["correct_comments_html"] == ""
    assert [a["answer_weight"] for a in q["answers"]] == [100, 0, 0]
    assert q["answers"][0]["answer_comment"] == "Yes."
    assert "Updated question 1" in out and "2 questions, 3 points" in out
    assert "click Save" not in out


def test_update_quiz_question_on_published_quiz_says_to_save_in_canvas(monkeypatch):
    calls = []
    monkeypatch.setattr(quizzes, "canvas_request", _classic_fake(monkeypatch, calls, published=True))
    out = asyncio.run(quizzes.update_quiz_question("ENG101", 555, 1, '{"text": "New stem?"}'))
    assert "https://school/quizzes/555/edit" in out and "click Save" in out


def test_update_quiz_question_validates_before_writing(monkeypatch):
    calls = []
    monkeypatch.setattr(quizzes, "canvas_request", _classic_fake(monkeypatch, calls))
    out = asyncio.run(quizzes.update_quiz_question("ENG101", 555, 1, '{"answers": [{"text": "a"}, {"text": "b"}]}'))
    assert out.startswith("Nothing changed.") and not any(c[0] == "PUT" for c in calls)


def test_update_quiz_question_unknown_id(monkeypatch):
    calls = []
    monkeypatch.setattr(quizzes, "canvas_request", _classic_fake(monkeypatch, calls, status=404))
    out = asyncio.run(quizzes.update_quiz_question("ENG101", 555, 99, '{"points": 1}'))
    assert "get_quiz_details" in out


def test_delete_quiz_question(monkeypatch):
    calls = []
    monkeypatch.setattr(quizzes, "canvas_request", _classic_fake(monkeypatch, calls))
    out = asyncio.run(quizzes.delete_quiz_question("ENG101", 555, 1))
    assert ("DELETE", "/courses/100/quizzes/555/questions/1", None) in calls
    assert "Which planet is closest to the sun?" in out and "dp_app" not in out


def test_update_quiz_sends_only_given_fields(monkeypatch):
    calls = []
    monkeypatch.setattr(quizzes, "canvas_request", _classic_fake(monkeypatch, calls))
    out = asyncio.run(quizzes.update_quiz("ENG101", 555, time_limit_minutes=0, allowed_attempts=-1, due_at=""))
    assert calls[0][2] == {"quiz": {"time_limit": None, "allowed_attempts": -1, "scoring_policy": "keep_highest",
                                    "due_at": None}}
    assert "Attempts: unlimited" in out
    assert asyncio.run(quizzes.update_quiz("ENG101", 555)).startswith("Nothing to update")
    assert asyncio.run(quizzes.update_quiz("ENG101", 555, quiz_type="exam")).startswith("Nothing changed")


def test_get_quiz_details_prints_editable_json(monkeypatch):
    async def fake_paginated(path, params=None, max_pages=20):
        return [{"id": 10 + i, **q} for i, q in enumerate(CLASSIC_READ)] + [
            {"id": 99, "question_type": "calculated_question", "question_text": "2x?", "points_possible": 1}
        ]

    async def fake_request(method, path, params=None, json_body=None, data=None):
        return {"id": 555, "title": "Q", "quiz_type": "assignment", "allowed_attempts": 1}

    monkeypatch.setattr(quizzes, "canvas_paginated", fake_paginated)
    monkeypatch.setattr(quizzes, "canvas_request", fake_request)
    out = asyncio.run(quizzes.get_quiz_details("ENG101", 555))
    assert "Questions (10):" in out and "Points: 20" in out
    first = out.split("Q1 (question ID 10, multiple_choice_question, 2 pts)\n")[1].split("\n")[0]
    assert json.loads(first)["answers"][0] == {"text": "Mercury", "correct": True, "feedback": "Yes."}
    assert "can't be edited with these tools" in out and "dp_app" not in out


# --------------------------------------------------------- New Quiz tools


def _new_fake(calls, item, items_points=(5.0, 1.0), quiz_points=19.0, patch_status=None):
    state = {"points": quiz_points}

    async def fake_paginated(path, params=None, max_pages=20):
        return [{"id": str(i), "points_possible": p} for i, p in enumerate(items_points)]

    async def fake_request(method, path, params=None, json_body=None, data=None):
        calls.append((method, path, json_body))
        if "/items/" in path:
            if method == "GET":
                return item
            if method == "PATCH":
                if patch_status:
                    raise CanvasAPIError(patch_status, '{"errors":"locked"}', path)
                return {**item, "points_possible": json_body["item"]["points_possible"], "entry": json_body["item"]["entry"]}
            return None
        if method == "PATCH":
            state["points"] = json_body["quiz"].get("points_possible", state["points"])
            return {"id": "777", "title": "Q", "points_possible": state["points"]}
        if method == "PUT":
            return {"id": 777, "published": True}
        return {"id": "777", "title": "Q", "points_possible": state["points"], "published": False, "quiz_settings": {}}

    return fake_paginated, fake_request


def _stored_item(question):
    item = new_quiz_item(parse_questions(json.dumps([question]))[0], 1)
    return {"id": "42", "entry_type": "Item", **item}


def test_update_new_quiz_item_patches_and_syncs_points(monkeypatch):
    calls = []
    paginated, request = _new_fake(calls, _stored_item(QUESTIONS[0]))
    monkeypatch.setattr(new_quizzes, "canvas_paginated", paginated)
    monkeypatch.setattr(new_quizzes, "canvas_request", request)
    out = asyncio.run(new_quizzes.update_new_quiz_item("ENG101", 777, 42, '{"points": 5, "correct_feedback": ""}'))
    patch = next(c for c in calls if c[0] == "PATCH" and "/items/42" in c[1])
    assert patch[1] == "https://school.instructure.com/api/quiz/v1/courses/100/quizzes/777/items/42"
    sent = patch[2]["item"]
    assert "position" not in sent and sent["points_possible"] == 5
    assert sent["entry"]["feedback"] == {}  # explicit, so PATCH's merge drops the old feedback
    assert sent["entry"]["interaction_type_slug"] == "choice"
    quiz_patch = [c for c in calls if c[0] == "PATCH" and c[1].endswith("/quizzes/777")]
    assert quiz_patch[-1][2] == {"quiz": {"points_possible": 6.0}}
    assert "Quiz total: 2 questions, 6 points" in out


def test_update_new_quiz_item_refuses_type_change(monkeypatch):
    calls = []
    paginated, request = _new_fake(calls, _stored_item(QUESTIONS[0]))
    monkeypatch.setattr(new_quizzes, "canvas_paginated", paginated)
    monkeypatch.setattr(new_quizzes, "canvas_request", request)
    out = asyncio.run(new_quizzes.update_new_quiz_item("ENG101", 777, 42, '{"type": "true_false", "answer": true}'))
    assert out.startswith("Nothing changed") and "delete_new_quiz_item" in out
    assert not any(c[0] == "PATCH" for c in calls)


def test_update_new_quiz_item_reports_canvas_rejection(monkeypatch):
    calls = []
    paginated, request = _new_fake(calls, _stored_item(QUESTIONS[1]), patch_status=422)
    monkeypatch.setattr(new_quizzes, "canvas_paginated", paginated)
    monkeypatch.setattr(new_quizzes, "canvas_request", request)
    out = asyncio.run(new_quizzes.update_new_quiz_item("ENG101", 777, 42, '{"answer": false}'))
    assert "Canvas rejected the edit (HTTP 422)" in out


def test_delete_new_quiz_item_syncs_points(monkeypatch):
    calls = []
    paginated, request = _new_fake(calls, _stored_item(QUESTIONS[4]), items_points=(2.0,), quiz_points=12.0)
    monkeypatch.setattr(new_quizzes, "canvas_paginated", paginated)
    monkeypatch.setattr(new_quizzes, "canvas_request", request)
    out = asyncio.run(new_quizzes.delete_new_quiz_item("ENG101", 777, 42))
    assert ("DELETE", "https://school.instructure.com/api/quiz/v1/courses/100/quizzes/777/items/42", None) in calls
    assert "Now 1 questions, 2 points" in out and "Argue for or against." in out


def test_update_new_quiz_settings_and_publish(monkeypatch):
    calls = []
    paginated, request = _new_fake(calls, {})
    monkeypatch.setattr(new_quizzes, "canvas_paginated", paginated)
    monkeypatch.setattr(new_quizzes, "canvas_request", request)
    asyncio.run(new_quizzes.update_new_quiz(
        "ENG101", 777, title="T", time_limit_minutes=0, allowed_attempts=1, shuffle_questions=True,
        lock_at="", published=True,
    ))
    quiz_patch = calls[0][2]["quiz"]
    assert quiz_patch["title"] == "T" and quiz_patch["lock_at"] is None
    assert quiz_patch["quiz_settings"] == {
        "has_time_limit": False, "session_time_limit_in_seconds": 0,
        "multiple_attempts": {"multiple_attempts_enabled": False}, "shuffle_questions": True,
    }
    assert ("PUT", "/courses/100/assignments/777", {"assignment": {"published": True}}) in calls
    assert asyncio.run(new_quizzes.update_new_quiz("ENG101", 777)).startswith("Nothing to update")


def test_get_new_quiz_details_prints_editable_json(monkeypatch):
    items = [{**_stored_item(q), "id": str(50 + n), "position": n + 1} for n, q in enumerate(QUESTIONS[:2])]
    items.append({"id": "60", "position": 3, "entry_type": "Stimulus", "points_possible": 0, "entry": {"title": "Passage"}})

    async def fake_paginated(path, params=None, max_pages=20):
        return list(reversed(items))  # out of order on purpose

    async def fake_request(method, path, params=None, json_body=None, data=None):
        return {"id": "777", "title": "Q", "points_possible": 10.0, "quiz_settings": {
            "has_time_limit": True, "session_time_limit_in_seconds": 1200,
            "multiple_attempts": {"multiple_attempts_enabled": True, "attempt_limit": False}}}

    monkeypatch.setattr(new_quizzes, "canvas_paginated", fake_paginated)
    monkeypatch.setattr(new_quizzes, "canvas_request", fake_request)
    out = asyncio.run(new_quizzes.get_new_quiz_details("ENG101", 777))
    assert out.index("item ID 50") < out.index("item ID 51") < out.index("item ID 60")
    assert "Time Limit: 20 min" in out and "Allowed Attempts: unlimited" in out
    assert "items add up to 3" in out  # quiz total out of sync gets flagged
    first = out.split("Q1 (item ID 50, choice, 2 pts)\n")[1].split("\n")[0]
    assert json.loads(first)["answers"][0]["correct"] is True
    assert "can't be edited with these tools" in out
