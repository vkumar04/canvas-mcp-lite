import asyncio
import json

import pytest

from canvas_mcp_lite import google_client
from canvas_mcp_lite.tools import google_forms
from canvas_mcp_lite.tools.google_forms import FormSpecError, item_to_spec, parse_questions

FORM_ID = "1FaIpQLsAbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
FORM_URL = f"https://docs.google.com/forms/d/{FORM_ID}/edit"

QUIZ = [
    {"type": "multiple_choice", "title": "Best thesis?", "options": ["A", "B", "C"], "correct": ["B"],
     "points": 2, "required": True, "correct_feedback": "Yes.", "incorrect_feedback": "Re-read p. 4."},
    {"type": "checkboxes", "title": "Pick all claims", "options": ["X", "Y", "Z"], "correct": ["X", "Z"],
     "points": 3, "shuffle": True},
    {"type": "short_answer", "title": "Capital of France?", "correct": ["Paris", "paris"], "points": 1},
    {"type": "paragraph", "title": "Reflect", "general_feedback": "Graded by hand.", "points": 5},
    {"type": "section", "title": "Part 2", "description": "Opinions"},
    {"type": "scale", "title": "Confidence", "low": 1, "high": 5, "low_label": "low", "high_label": "high"},
    {"type": "text", "title": "Thanks!", "description": "You're done."},
]


def _stored(items):
    """What GET /forms returns: our items with server-assigned IDs."""
    out = []
    for n, item in enumerate(items):
        item = json.loads(json.dumps(item))
        item["itemId"] = f"i{n}"
        if "questionItem" in item:
            item["questionItem"]["question"]["questionId"] = f"q{n}"
        out.append(item)
    return out


def _form(items=None, is_quiz=True, published=False):
    return {
        "formId": FORM_ID,
        "info": {"title": "Exit ticket", "description": "Week 3"},
        "settings": {"quizSettings": {"isQuiz": is_quiz}},
        "items": _stored(items if items is not None else parse_questions(json.dumps(QUIZ), True)),
        "responderUri": f"https://docs.google.com/forms/d/e/xyz/viewform",
        "publishSettings": {"publishState": {"isPublished": published, "isAcceptingResponses": published}},
    }


def _fake(monkeypatch, form=None, responses=None):
    calls = []
    state = {"form": form or _form()}

    async def fake_request(method, path, params=None, json_body=None, raw=False):
        calls.append((method, path, params, json_body))
        if method == "POST" and path.endswith("/v1/forms"):
            return {"formId": FORM_ID, "info": json_body["info"]}
        if path.endswith(":batchUpdate"):
            return {"replies": [], "form": state["form"]}
        if path.endswith(":setPublishSettings"):
            return {"formId": FORM_ID, "publishSettings": json_body["publishSettings"]}
        if path.endswith("/responses"):
            return responses or {}
        if method == "GET" and path.endswith(f"/forms/{FORM_ID}"):
            return state["form"]
        return {}

    monkeypatch.setattr(google_forms, "google_request", fake_request)
    return calls


def _requests(calls):
    return [r for c in calls if c[1].endswith(":batchUpdate") for r in c[3]["requests"]]


def test_extract_id():
    assert google_forms._extract_id(FORM_URL) == FORM_ID
    assert google_forms._extract_id(f"https://docs.google.com/forms/u/1/d/{FORM_ID}/viewform") == FORM_ID
    assert google_forms._extract_id(FORM_ID) == FORM_ID
    with pytest.raises(ValueError, match="responder link"):
        google_forms._extract_id("https://docs.google.com/forms/d/e/1FAIpQLSf_abcdefghijk/viewform")


def test_spec_to_item_shapes():
    mc, cb, sa, para, section, scale, text = parse_questions(json.dumps(QUIZ), True)
    q = mc["questionItem"]["question"]
    assert q["required"] is True
    assert q["choiceQuestion"] == {"type": "RADIO", "options": [{"value": "A"}, {"value": "B"}, {"value": "C"}]}
    assert q["grading"] == {"pointValue": 2, "correctAnswers": {"answers": [{"value": "B"}]},
                            "whenRight": {"text": "Yes."}, "whenWrong": {"text": "Re-read p. 4."}}
    assert cb["questionItem"]["question"]["choiceQuestion"]["shuffle"] is True
    assert sa["questionItem"]["question"]["textQuestion"] == {"paragraph": False}
    assert para["questionItem"]["question"]["grading"] == {"pointValue": 5, "generalFeedback": {"text": "Graded by hand."}}
    assert section == {"title": "Part 2", "description": "Opinions", "pageBreakItem": {}}
    assert scale["questionItem"]["question"]["scaleQuestion"] == {"low": 1, "high": 5, "lowLabel": "low", "highLabel": "high"}
    assert text == {"title": "Thanks!", "description": "You're done.", "textItem": {}}


@pytest.mark.parametrize("spec, message", [
    ({"type": "essay", "title": "x"}, "unknown type"),
    ({"type": "multiple_choice", "title": "x", "options": []}, "at least one"),
    ({"type": "multiple_choice", "title": "x", "options": ["a", "a"]}, "unique"),
    ({"type": "multiple_choice", "title": "x", "options": ["a", "b"], "correct": ["c"]}, "aren't among"),
    ({"type": "multiple_choice", "title": "x", "options": ["a", "b"], "correct": ["a", "b"]}, "only one correct"),
    ({"type": "dropdown", "title": "x", "options": ["a"], "other": True}, "Other"),
    ({"type": "scale", "title": "x", "high": 11}, "2-10"),
    ({"type": "paragraph", "title": "x", "correct": ["y"]}, "only apply"),
    ({"type": "short_answer"}, "'title' is required"),
])
def test_spec_validation(spec, message):
    with pytest.raises(FormSpecError, match=message):
        parse_questions(json.dumps([spec]), True)


def test_points_need_a_quiz():
    with pytest.raises(FormSpecError, match="is_quiz"):
        parse_questions(json.dumps([{"type": "short_answer", "title": "x", "points": 1}]), False)


def test_read_back_round_trips():
    for spec, item in zip(QUIZ, _stored(parse_questions(json.dumps(QUIZ), True))):
        assert item_to_spec(item) == spec


def test_create_quiz_sets_quiz_mode_before_items_and_stays_unpublished(monkeypatch):
    calls = _fake(monkeypatch)
    out = asyncio.run(google_forms.create_google_form("Exit ticket", "Week 3", json.dumps(QUIZ), is_quiz=True))
    assert calls[0][3] == {"info": {"title": "Exit ticket", "documentTitle": "Exit ticket"}}
    requests = _requests(calls)
    assert requests[0] == {"updateSettings": {"settings": {"quizSettings": {"isQuiz": True}}, "updateMask": "quizSettings.isQuiz"}}
    assert requests[1] == {"updateFormInfo": {"info": {"description": "Week 3"}, "updateMask": "description"}}
    assert [r["createItem"]["location"]["index"] for r in requests[2:]] == list(range(len(QUIZ)))
    (pub,) = [c[3]["publishSettings"]["publishState"] for c in calls if c[1].endswith(":setPublishSettings")]
    assert pub == {"isPublished": False, "isAcceptingResponses": False}
    assert "NOT published" in out and "Type: quiz (11 pts)" in out


def test_create_with_publish(monkeypatch):
    calls = _fake(monkeypatch, form=_form(published=True))
    out = asyncio.run(google_forms.create_google_form("Survey", questions_json="[]", publish=True))
    (pub,) = [c[3] for c in calls if c[1].endswith(":setPublishSettings")]
    assert pub == {"publishSettings": {"publishState": {"isPublished": True, "isAcceptingResponses": True}},
                   "updateMask": "publishState"}
    assert "accepting responses" in out


def test_create_rejects_bad_spec_before_calling_google(monkeypatch):
    calls = _fake(monkeypatch)
    out = asyncio.run(google_forms.create_google_form("X", questions_json='[{"type": "nope", "title": "x"}]'))
    assert out.startswith("Nothing created") and calls == []


def test_add_questions_appends_or_inserts(monkeypatch):
    calls = _fake(monkeypatch)
    new = json.dumps([{"type": "short_answer", "title": "One"}, {"type": "short_answer", "title": "Two"}])
    asyncio.run(google_forms.add_google_form_questions(FORM_URL, new))
    assert [r["createItem"]["location"]["index"] for r in _requests(calls)] == [7, 8]
    calls.clear()
    asyncio.run(google_forms.add_google_form_questions(FORM_URL, new, position=2))
    assert [r["createItem"]["location"]["index"] for r in _requests(calls)] == [1, 2]


def test_update_question_merges_and_keeps_question_id(monkeypatch):
    calls = _fake(monkeypatch)
    out = asyncio.run(google_forms.update_google_form_question(FORM_URL, "i0", '{"correct": ["C"], "points": 4}'))
    (req,) = _requests(calls)
    update = req["updateItem"]
    assert update["location"] == {"index": 0} and update["updateMask"] == "*"
    assert update["item"]["itemId"] == "i0"
    question = update["item"]["questionItem"]["question"]
    assert question["questionId"] == "q0"
    assert question["grading"]["correctAnswers"] == {"answers": [{"value": "C"}]}
    assert question["grading"]["pointValue"] == 4
    assert question["grading"]["whenRight"] == {"text": "Yes."}  # untouched keys survive
    assert "Updated item i0" in out


def test_update_question_type_change_drops_old_fields(monkeypatch):
    calls = _fake(monkeypatch)
    asyncio.run(google_forms.update_google_form_question(FORM_URL, "i0", '{"type": "paragraph"}'))
    question = _requests(calls)[0]["updateItem"]["item"]["questionItem"]["question"]
    assert question == {"required": True, "textQuestion": {"paragraph": True}}


def test_update_question_unknown_item(monkeypatch):
    _fake(monkeypatch)
    assert "No item 'zz'" in asyncio.run(google_forms.update_google_form_question(FORM_URL, "zz", '{"points": 1}'))


def test_update_form_publish_close_and_info(monkeypatch):
    calls = _fake(monkeypatch, form=_form(published=True))
    asyncio.run(google_forms.update_google_form(FORM_URL, accepting_responses=False))
    (pub,) = [c[3]["publishSettings"]["publishState"] for c in calls if c[1].endswith(":setPublishSettings")]
    assert pub == {"isPublished": True, "isAcceptingResponses": False}
    calls.clear()
    asyncio.run(google_forms.update_google_form(FORM_URL, title="New", description=""))
    assert _requests(calls) == [{"updateFormInfo": {"info": {"title": "New", "description": ""}, "updateMask": "title,description"}}]
    assert asyncio.run(google_forms.update_google_form(FORM_URL)).startswith("Nothing to update")


def test_delete_question(monkeypatch):
    calls = _fake(monkeypatch)
    out = asyncio.run(google_forms.delete_google_form_question(FORM_URL, "i2"))
    assert _requests(calls) == [{"deleteItem": {"location": {"index": 2}}}]
    assert "Capital of France?" in out and "6 item(s) remain" in out


def test_read_form_marks_uneditable_items(monkeypatch):
    form = _form()
    form["items"].append({"itemId": "g1", "title": "Rate each", "questionGroupItem": {
        "questions": [{"questionId": "r1", "rowQuestion": {"title": "Clarity"}}], "grid": {}}})
    _fake(monkeypatch, form=form)
    out = asyncio.run(google_forms.read_google_form(FORM_URL))
    assert "NOT published" in out and "item_id i0, multiple_choice" in out
    assert json.loads(out.split("(item_id i0, multiple_choice)\n")[1].split("\n")[0]) == QUIZ[0]
    assert "item_id g1, grid" in out and "rows: Clarity" in out


def test_list_responses(monkeypatch):
    responses = {"responses": [
        {"responseId": "b", "lastSubmittedTime": "2026-10-05T15:00:00Z", "respondentEmail": "kim@school.edu",
         "totalScore": 3, "answers": {"q2": {"questionId": "q2", "textAnswers": {"answers": [{"value": "Paris"}]}},
                                      "q1": {"questionId": "q1", "textAnswers": {"answers": [{"value": "X"}, {"value": "Z"}]}}}},
        {"responseId": "a", "lastSubmittedTime": "2026-10-05T14:00:00Z",
         "answers": {"q0": {"questionId": "q0", "textAnswers": {"answers": [{"value": "B"}]}}}},
    ]}
    _fake(monkeypatch, responses=responses)
    out = asyncio.run(google_forms.list_google_form_responses(FORM_URL))
    assert out.index("#1 — anonymous") < out.index("#2 — kim@school.edu")
    assert "score 3" in out
    assert out.index("Pick all claims: X; Z") < out.index("Capital of France?: Paris")


def test_service_disabled_message(monkeypatch):
    async def fake_request(*args, **kwargs):
        raise google_client.GoogleAPIError(403, '{"reason": "SERVICE_DISABLED"}', "u")

    monkeypatch.setattr(google_forms, "google_request", fake_request)
    assert "Forms API isn't enabled" in asyncio.run(google_forms.read_google_form(FORM_URL))


def test_unconfigured_returns_setup_message(monkeypatch):
    for var in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(google_client, "_runtime_refresh_token", None)
    assert "connect_google_docs" in asyncio.run(google_forms.read_google_form(FORM_URL))
