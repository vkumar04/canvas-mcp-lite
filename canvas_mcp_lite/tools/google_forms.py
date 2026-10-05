"""Google Forms: create, read, edit, publish, and collect responses through the
Forms API, using the same connected instructor account (and drive scope) as
the Google Docs and Slides tools.

Questions go in and come back out in one JSON format (questions_json), so a
caller can read a form, change a field, and pass the question back to
update_google_form_question. New forms are set unpublished explicitly
(Google's own unpublished-by-default for API-created forms doesn't apply to
every account); publishing is a separate, explicit step."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..google_client import (
    GoogleAPIError,
    GoogleConfigError,
    connected_account_email,
    extract_doc_id,
    google_request,
)
from ..util import format_date

GOOGLE_FORMS_API = "https://forms.googleapis.com/v1"
GOOGLE_FORMS_MIME = "application/vnd.google-apps.form"
_FORM_ID = re.compile(r"/forms/(?:u/\d+/)?d/([A-Za-z0-9_-]{10,})")

QUESTIONS_FORMAT = """questions_json is a JSON array; each item is one object:
  {"type": "multiple_choice" | "checkboxes" | "dropdown" | "short_answer" |
           "paragraph" | "scale" | "date" | "time" | "section" | "text",
   "title": "Question text", "description": "optional help text",
   "required": true/false (default false)}
  plus, by type:
  multiple_choice / checkboxes / dropdown: "options": ["A", "B", ...],
      "shuffle": true (optional), "other": true (adds an "Other:" box; not dropdown)
  scale: "low": 1, "high": 5 (low 0-1, high 2-10), "low_label", "high_label" (optional)
  section: starts a new page; "title"/"description" are the section header
  text: a title + description block with no answer (instructions)
  Quiz forms only (is_quiz on the form), for answerable questions:
  "points": 2, "correct": ["B"] (choice questions: the right option(s);
      short_answer: every accepted answer), "correct_feedback",
      "incorrect_feedback" (choice questions), "general_feedback" (any)."""

_CHOICE_TYPES = {"multiple_choice": "RADIO", "checkboxes": "CHECKBOX", "dropdown": "DROP_DOWN"}
_CHOICE_BACK = {v: k for k, v in _CHOICE_TYPES.items()}
_TYPES = set(_CHOICE_TYPES) | {"short_answer", "paragraph", "scale", "date", "time", "section", "text"}
_ANSWERABLE = set(_CHOICE_TYPES) | {"short_answer", "paragraph", "scale", "date", "time"}


class FormSpecError(ValueError):
    """A questions_json entry that can't become a form item."""


def _extract_id(url_or_id: str) -> str:
    candidate = url_or_id.strip()
    if "/forms/d/e/" in candidate:
        raise ValueError(
            "That's the form's public responder link (/forms/d/e/...), which doesn't "
            "contain the form ID. Use the edit link (https://docs.google.com/forms/d/<id>/edit) "
            "or the form_id from list_google_forms."
        )
    match = _FORM_ID.search(candidate)
    if match:
        return match.group(1)
    try:
        return extract_doc_id(candidate)
    except ValueError:
        raise ValueError(
            f"Couldn't find a Google Form ID in {url_or_id!r}. Expected a link like "
            "https://docs.google.com/forms/d/<id>/edit, or a bare form ID."
        )


async def _explain_api_error(exc: GoogleAPIError, form_id: str) -> str:
    if exc.status_code == 403 and "SERVICE_DISABLED" in str(exc):
        return (
            "The Google Forms API isn't enabled in the server's Google Cloud project. "
            "The server admin needs to enable it at console.cloud.google.com → APIs & "
            "Services → Library → Google Forms API, wait a minute, and retry."
        )
    if exc.status_code in (403, 404):
        email = await connected_account_email()
        acting_as = f" ({email})" if email else ""
        return (
            f"Can't access Google Form {form_id} (HTTP {exc.status_code}). The connected "
            f"instructor Google account{acting_as} most likely isn't an editor of this "
            "form — share it with that account as an editor and try again."
        )
    if exc.status_code == 400:
        return f"Google Forms rejected the request: {exc}"
    return str(exc)


async def _get_form(form_id: str) -> dict:
    return await google_request("GET", f"{GOOGLE_FORMS_API}/forms/{form_id}")


async def _batch_update(form_id: str, requests: list[dict]) -> dict:
    return await google_request(
        "POST",
        f"{GOOGLE_FORMS_API}/forms/{form_id}:batchUpdate",
        json_body={"requests": requests, "includeFormInResponse": True},
    )


async def _set_publish(form_id: str, published: bool, accepting: bool) -> dict:
    return await google_request(
        "POST",
        f"{GOOGLE_FORMS_API}/forms/{form_id}:setPublishSettings",
        json_body={
            "publishSettings": {"publishState": {"isPublished": published, "isAcceptingResponses": accepting}},
            "updateMask": "publishState",
        },
    )


# --- questions_json <-> Forms items --------------------------------------------


def _feedback(text: Any) -> Optional[dict]:
    text = str(text or "").strip()
    return {"text": text} if text else None


def spec_to_item(spec: Any, number: int, is_quiz: bool) -> dict:
    """One questions_json entry → a Forms Item (no itemId)."""
    if not isinstance(spec, dict):
        raise FormSpecError(f"Question {number} is not a JSON object.")
    qtype = str(spec.get("type") or "").strip().lower().replace(" ", "_").replace("-", "_")
    qtype = {"radio": "multiple_choice", "checkbox": "checkboxes", "drop_down": "dropdown",
             "short": "short_answer", "long_answer": "paragraph", "linear_scale": "scale",
             "page_break": "section", "description": "text"}.get(qtype, qtype)
    if qtype not in _TYPES:
        raise FormSpecError(f"Question {number}: unknown type {spec.get('type')!r}. Use one of: {', '.join(sorted(_TYPES))}.")
    title = str(spec.get("title") or spec.get("text") or "").strip()
    if not title and qtype != "section":
        raise FormSpecError(f"Question {number} ({qtype}): 'title' is required.")
    item: dict[str, Any] = {"title": title}
    if str(spec.get("description") or "").strip():
        item["description"] = str(spec["description"]).strip()
    if qtype == "section":
        item["pageBreakItem"] = {}
        return item
    if qtype == "text":
        item["textItem"] = {}
        return item

    question: dict[str, Any] = {"required": bool(spec.get("required", False))}
    options: list[str] = []
    if qtype in _CHOICE_TYPES:
        options = [str(o).strip() for o in spec.get("options") or []]
        if len(options) < 1 or any(not o for o in options):
            raise FormSpecError(f"Question {number} ({qtype}): 'options' must list at least one non-empty choice.")
        if len(set(options)) != len(options):
            raise FormSpecError(f"Question {number} ({qtype}): options must be unique (Forms rejects duplicates).")
        choice: dict[str, Any] = {"type": _CHOICE_TYPES[qtype], "options": [{"value": o} for o in options]}
        if spec.get("other"):
            if qtype == "dropdown":
                raise FormSpecError(f"Question {number}: dropdown questions can't have an 'Other' option.")
            choice["options"].append({"isOther": True})
        if spec.get("shuffle"):
            choice["shuffle"] = True
        question["choiceQuestion"] = choice
    elif qtype in ("short_answer", "paragraph"):
        question["textQuestion"] = {"paragraph": qtype == "paragraph"}
    elif qtype == "scale":
        low, high = int(spec.get("low", 1)), int(spec.get("high", 5))
        if low not in (0, 1) or not 2 <= high <= 10:
            raise FormSpecError(f"Question {number} (scale): 'low' must be 0 or 1 and 'high' 2-10.")
        scale: dict[str, Any] = {"low": low, "high": high}
        for key, api in (("low_label", "lowLabel"), ("high_label", "highLabel")):
            if str(spec.get(key) or "").strip():
                scale[api] = str(spec[key]).strip()
        question["scaleQuestion"] = scale
    elif qtype == "date":
        question["dateQuestion"] = {"includeYear": True}
    elif qtype == "time":
        question["timeQuestion"] = {}

    graded = any(spec.get(k) not in (None, "", []) for k in ("points", "correct"))
    if graded and not is_quiz:
        raise FormSpecError(
            f"Question {number}: 'points'/'correct' only work on a quiz form — "
            "create it with is_quiz=true (or turn quiz mode on with update_google_form)."
        )
    if is_quiz and qtype in _ANSWERABLE and (graded or spec.get("general_feedback")):
        grading: dict[str, Any] = {"pointValue": int(spec.get("points") or 0)}
        correct = spec.get("correct")
        correct = [str(c).strip() for c in (correct if isinstance(correct, list) else [correct]) if str(c or "").strip()]
        if correct:
            if qtype in _CHOICE_TYPES:
                missing = [c for c in correct if c not in options]
                if missing:
                    raise FormSpecError(f"Question {number}: correct answer(s) {missing} aren't among the options.")
                if qtype != "checkboxes" and len(correct) > 1:
                    raise FormSpecError(f"Question {number} ({qtype}): only one correct option allowed — use checkboxes for several.")
            elif qtype != "short_answer":
                raise FormSpecError(f"Question {number} ({qtype}): 'correct' answers only apply to choice and short_answer questions.")
            grading["correctAnswers"] = {"answers": [{"value": c} for c in correct]}
        if qtype in _CHOICE_TYPES:
            for key, api in (("correct_feedback", "whenRight"), ("incorrect_feedback", "whenWrong")):
                if _feedback(spec.get(key)):
                    grading[api] = _feedback(spec.get(key))
        if _feedback(spec.get("general_feedback")):
            grading["generalFeedback"] = _feedback(spec.get("general_feedback"))
        question["grading"] = grading
    item["questionItem"] = {"question": question}
    return item


def parse_questions(questions_json: str, is_quiz: bool) -> list[dict]:
    try:
        specs = json.loads(questions_json or "[]")
    except json.JSONDecodeError as exc:
        raise FormSpecError(f"questions_json is not valid JSON: {exc}")
    if isinstance(specs, dict):
        specs = [specs]
    if not isinstance(specs, list):
        raise FormSpecError("questions_json must be a JSON array of question objects.")
    return [spec_to_item(spec, n, is_quiz) for n, spec in enumerate(specs, start=1)]


def item_to_spec(item: dict) -> Optional[dict]:
    """A Forms Item back in questions_json form; None for kinds the format
    can't express (grids, images, videos)."""
    spec: dict[str, Any] = {}
    if "pageBreakItem" in item:
        spec["type"] = "section"
    elif "textItem" in item:
        spec["type"] = "text"
    elif "questionItem" in item:
        question = (item["questionItem"] or {}).get("question") or {}
        if "choiceQuestion" in question:
            choice = question["choiceQuestion"]
            spec["type"] = _CHOICE_BACK.get(choice.get("type"), "multiple_choice")
            opts = choice.get("options") or []
            spec["options"] = [o.get("value", "") for o in opts if not o.get("isOther")]
            if any(o.get("isOther") for o in opts):
                spec["other"] = True
            if choice.get("shuffle"):
                spec["shuffle"] = True
        elif "textQuestion" in question:
            spec["type"] = "paragraph" if (question["textQuestion"] or {}).get("paragraph") else "short_answer"
        elif "scaleQuestion" in question:
            scale = question["scaleQuestion"]
            spec.update(type="scale", low=scale.get("low", 0), high=scale.get("high"))
            if scale.get("lowLabel"):
                spec["low_label"] = scale["lowLabel"]
            if scale.get("highLabel"):
                spec["high_label"] = scale["highLabel"]
        elif "dateQuestion" in question:
            spec["type"] = "date"
        elif "timeQuestion" in question:
            spec["type"] = "time"
        else:
            return None
        if question.get("required"):
            spec["required"] = True
        grading = question.get("grading")
        if grading:
            spec["points"] = grading.get("pointValue", 0)
            correct = [a.get("value") for a in (grading.get("correctAnswers") or {}).get("answers") or []]
            if correct:
                spec["correct"] = correct
            for key, api in (("correct_feedback", "whenRight"), ("incorrect_feedback", "whenWrong"),
                             ("general_feedback", "generalFeedback")):
                text = (grading.get(api) or {}).get("text")
                if text:
                    spec[key] = text
    else:
        return None
    ordered: dict[str, Any] = {"type": spec.pop("type"), "title": item.get("title", "")}
    if item.get("description"):
        ordered["description"] = item["description"]
    ordered.update(spec)
    return ordered


def _item_kind(item: dict) -> str:
    for key, label in (("questionGroupItem", "grid"), ("imageItem", "image"), ("videoItem", "video")):
        if key in item:
            return label
    spec = item_to_spec(item)
    return spec["type"] if spec else "other"


def format_form(form: dict) -> str:
    form_id = form.get("formId", "?")
    info = form.get("info") or {}
    is_quiz = ((form.get("settings") or {}).get("quizSettings") or {}).get("isQuiz", False)
    state = (form.get("publishSettings") or {}).get("publishState") or {}
    if not form.get("publishSettings"):
        status = "published (legacy form)"
    elif not state.get("isPublished"):
        status = "NOT published — students can't open it yet (publish with update_google_form)"
    elif state.get("isAcceptingResponses"):
        status = "published, accepting responses"
    else:
        status = "published, NOT accepting responses (closed)"
    items = form.get("items") or []
    total = sum(
        ((i.get("questionItem") or {}).get("question") or {}).get("grading", {}).get("pointValue", 0) for i in items
    )
    lines = [
        f"Title: {info.get('title', '(untitled)')} (form_id: {form_id})",
        f"Type: {'quiz (' + str(total) + ' pts)' if is_quiz else 'form'}",
        f"Status: {status}",
        f"Student link: {form.get('responderUri') or '(none until published)'}",
        f"Edit link: https://docs.google.com/forms/d/{form_id}/edit",
        f"\nDescription:\n{info.get('description') or '(none)'}",
        f"\nItems ({len(items)}):",
    ]
    for n, item in enumerate(items, start=1):
        lines.append(f"\n{n}. (item_id {item.get('itemId')}, {_item_kind(item)})")
        spec = item_to_spec(item)
        if spec:
            lines.append(json.dumps(spec, ensure_ascii=False))
        else:
            rows = [q.get("rowQuestion", {}).get("title", "") for q in (item.get("questionGroupItem") or {}).get("questions") or []]
            extra = f" — rows: {', '.join(rows)}" if rows else ""
            lines.append(f"[Can't be edited with these tools — edit it in Google Forms.] {item.get('title', '')}{extra}")
    if not items:
        lines.append("(none yet — add them with add_google_form_questions)")
    return "\n".join(lines)


# --- Tools ---------------------------------------------------------------------


async def list_google_forms(query: str = "", limit: int = 20) -> str:
    """Find Google Forms the connected instructor account can see (own forms and
    forms shared with it), newest-modified first. Optional query matches words
    in the form title. Use this to locate a form, then read_google_form."""
    q = f"mimeType='{GOOGLE_FORMS_MIME}' and trashed=false"
    if query.strip():
        safe = query.strip().replace("\\", "\\\\").replace("'", "\\'")
        q += f" and name contains '{safe}'"
    try:
        result = await google_request(
            "GET",
            "/files",
            params={
                "q": q,
                "orderBy": "modifiedTime desc",
                "pageSize": max(1, min(int(limit), 100)),
                "fields": "files(id,name,modifiedTime,owners(displayName)),nextPageToken",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "corpora": "allDrives",
            },
        )
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, "(search)")
    files = result.get("files") or []
    if not files:
        return "No Google Forms found" + (f" matching '{query}'." if query.strip() else ".")
    lines = [f"Google Forms ({len(files)}{'+' if result.get('nextPageToken') else ''}):"]
    for f in files:
        owner = ", ".join(o.get("displayName", "?") for o in f.get("owners") or []) or "?"
        lines.append(f"- {f.get('name')} (form_id: {f.get('id')}) — modified {format_date(f.get('modifiedTime'))}, owner: {owner}")
    return "\n".join(lines)


async def read_google_form(form_url: str) -> str:
    """Read a Google Form: title, description, quiz or plain form, publish status,
    the student link, and every item with its item_id printed in the same
    questions_json format the create/edit tools take (including correct answers
    and points on quizzes). Run this before editing so item IDs are current."""
    try:
        form_id = _extract_id(form_url)
    except ValueError as exc:
        return str(exc)
    try:
        return format_form(await _get_form(form_id))
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, form_id)


async def create_google_form(
    title: str,
    description: str = "",
    questions_json: str = "[]",
    is_quiz: bool = False,
    publish: bool = False,
) -> str:
    """Create a new Google Form in the connected instructor's Drive — a survey,
    sign-up, exit ticket, reflection, or self-grading quiz (is_quiz=true enables
    points, answer keys, and feedback). The form is created UNPUBLISHED unless
    publish=true: students can't open it until it's published, so review it
    first and publish with update_google_form. Share the returned student link
    with the class (e.g. create_announcement or add_module_item with an
    ExternalUrl). For graded Canvas quizzes use create_quiz/create_new_quiz
    instead — Forms scores don't flow into the Canvas gradebook. The question
    format is described below."""
    if not title.strip():
        return "Form title is empty — nothing created."
    try:
        items = parse_questions(questions_json, is_quiz)
    except FormSpecError as exc:
        return f"Nothing created — {exc}"
    try:
        created = await google_request(
            "POST", f"{GOOGLE_FORMS_API}/forms", json_body={"info": {"title": title.strip(), "documentTitle": title.strip()}}
        )
        form_id = created["formId"]
        requests: list[dict] = []
        if is_quiz:
            requests.append({"updateSettings": {"settings": {"quizSettings": {"isQuiz": True}}, "updateMask": "quizSettings.isQuiz"}})
        if description.strip():
            requests.append({"updateFormInfo": {"info": {"description": description.strip()}, "updateMask": "description"}})
        requests.extend({"createItem": {"item": item, "location": {"index": n}}} for n, item in enumerate(items))
        if requests:
            await _batch_update(form_id, requests)
        # Set the state explicitly: Google's "API-created forms start
        # unpublished" default doesn't reach every account (live-verified).
        await _set_publish(form_id, publish, publish)
        form = await _get_form(form_id)
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, "(new form)")
    return f"Created Google Form '{title.strip()}' with {len(items)} item(s).\n\n" + format_form(form or created)


async def add_google_form_questions(form_url: str, questions_json: str, position: int = 0) -> str:
    """Add questions (or sections / text blocks) to an existing Google Form.
    position is the 1-based spot for the first new item; 0 (default) appends to
    the end. Points and correct answers need a quiz form. The question format
    is described below."""
    try:
        form_id = _extract_id(form_url)
        form = await _get_form(form_id)
        is_quiz = ((form.get("settings") or {}).get("quizSettings") or {}).get("isQuiz", False)
        items = parse_questions(questions_json, is_quiz)
        count = len(form.get("items") or [])
        start = count if position <= 0 else min(position - 1, count)
        form = (await _batch_update(
            form_id, [{"createItem": {"item": item, "location": {"index": start + n}}} for n, item in enumerate(items)]
        )).get("form") or form
    except (ValueError, FormSpecError) as exc:
        return f"Nothing added — {exc}"
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, form_url)
    return f"Added {len(items)} item(s) to form {form_id}.\n\n" + format_form(form)


create_google_form.__doc__ += "\n\n" + QUESTIONS_FORMAT
add_google_form_questions.__doc__ += "\n\n" + QUESTIONS_FORMAT


async def update_google_form_question(form_url: str, item_id: str, question_json: str) -> str:
    """Edit one item of a Google Form. question_json is ONE question object in
    the read_google_form format; keys you give replace the current ones and the
    rest stay, so {"required": true} or {"correct": ["B"]} alone works. Changing
    "type" between kinds (e.g. multiple_choice → paragraph) is allowed. Edits
    are live immediately, including on a published form."""
    try:
        form_id = _extract_id(form_url)
        patch = json.loads(question_json)
        if isinstance(patch, list) and len(patch) == 1:
            patch = patch[0]
        if not isinstance(patch, dict) or not patch:
            raise FormSpecError("question_json must be one non-empty JSON object.")
        form = await _get_form(form_id)
        items = form.get("items") or []
        index = next((n for n, i in enumerate(items) if i.get("itemId") == item_id.strip()), None)
        if index is None:
            return f"No item {item_id!r} on form {form_id} — run read_google_form for current item IDs."
        current = item_to_spec(items[index])
        if current is None:
            return "That item (grid/image/video) can't be edited with these tools — edit it in Google Forms."
        is_quiz = ((form.get("settings") or {}).get("quizSettings") or {}).get("isQuiz", False)
        merged = {**current, **patch}
        if patch.get("type") and patch["type"] != current["type"]:
            merged = {k: v for k, v in merged.items() if k in patch or k in ("title", "description", "required")}
        item = spec_to_item(merged, 1, is_quiz)
        item["itemId"] = item_id.strip()
        old_question = ((items[index].get("questionItem") or {}).get("question") or {})
        if "questionItem" in item and old_question.get("questionId") and (patch.get("type") in (None, current["type"])):
            item["questionItem"]["question"]["questionId"] = old_question["questionId"]
        form = (await _batch_update(
            form_id, [{"updateItem": {"item": item, "location": {"index": index}, "updateMask": "*"}}]
        )).get("form") or form
    except json.JSONDecodeError as exc:
        return f"question_json is not valid JSON: {exc}"
    except (ValueError, FormSpecError) as exc:
        return f"Nothing changed — {exc}"
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, form_url)
    updated = next((i for i in form.get("items") or [] if i.get("itemId") == item_id.strip()), None)
    now = json.dumps(item_to_spec(updated) if updated else merged, ensure_ascii=False)
    return f"Updated item {item_id} (#{index + 1}) on form {form_id}.\nNow: {now}"


async def update_google_form(
    form_url: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    is_quiz: Optional[bool] = None,
    published: Optional[bool] = None,
    accepting_responses: Optional[bool] = None,
) -> str:
    """Change a Google Form's title, description, quiz mode, or availability.
    published=true makes the form openable by students (and accepting responses
    unless accepting_responses=false); accepting_responses=false closes a
    published form to new submissions without unpublishing it; published=false
    takes it offline. Only the fields you pass change."""
    try:
        form_id = _extract_id(form_url)
    except ValueError as exc:
        return str(exc)
    requests: list[dict] = []
    info: dict[str, str] = {}
    if title is not None and title.strip():
        info["title"] = title.strip()
    if description is not None:
        info["description"] = description.strip()
    if info:
        requests.append({"updateFormInfo": {"info": info, "updateMask": ",".join(info)}})
    if is_quiz is not None:
        requests.append({"updateSettings": {"settings": {"quizSettings": {"isQuiz": bool(is_quiz)}}, "updateMask": "quizSettings.isQuiz"}})
    if not requests and published is None and accepting_responses is None:
        return "Nothing to update — pass title, description, is_quiz, published, or accepting_responses."
    try:
        if requests:
            await _batch_update(form_id, requests)
        if published is not None or accepting_responses is not None:
            form = await _get_form(form_id)
            state = (form.get("publishSettings") or {}).get("publishState") or {}
            is_published = state.get("isPublished", True) if published is None else published
            accepting = (accepting_responses if accepting_responses is not None
                         else bool(is_published) if published is not None else state.get("isAcceptingResponses", True))
            await _set_publish(form_id, bool(is_published), bool(accepting and is_published))
        form = await _get_form(form_id)
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, form_id)
    header = format_form(form).split("\n\nDescription:")[0]
    return f"Updated form {form_id}.\n{header}"


async def delete_google_form_question(form_url: str, item_id: str) -> str:
    """Permanently delete one item (question, section, or text block) from a
    Google Form. Responses already collected for a deleted question are no
    longer shown with the form. This cannot be undone."""
    try:
        form_id = _extract_id(form_url)
        form = await _get_form(form_id)
        items = form.get("items") or []
        index = next((n for n, i in enumerate(items) if i.get("itemId") == item_id.strip()), None)
        if index is None:
            return f"No item {item_id!r} on form {form_id} — run read_google_form for current item IDs."
        await _batch_update(form_id, [{"deleteItem": {"location": {"index": index}}}])
    except ValueError as exc:
        return str(exc)
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, form_url)
    return f"Deleted item {item_id} ('{items[index].get('title', '')}', #{index + 1}) from form {form_id}. {len(items) - 1} item(s) remain."


def _answer_text(answer: dict) -> str:
    if "textAnswers" in answer:
        return "; ".join(a.get("value", "") for a in (answer["textAnswers"] or {}).get("answers") or [])
    if "fileUploadAnswers" in answer:
        files = (answer["fileUploadAnswers"] or {}).get("answers") or []
        return "; ".join(f"{f.get('fileName')} (drive file {f.get('fileId')})" for f in files)
    return ""


async def list_google_form_responses(form_url: str, limit: int = 100) -> str:
    """List submitted responses to a Google Form, newest last: when, who (if the
    form collects emails), quiz score, and each answer labeled by question. Use
    it to read exit tickets or survey results, or to check who has responded."""
    try:
        form_id = _extract_id(form_url)
    except ValueError as exc:
        return str(exc)
    try:
        form = await _get_form(form_id)
        responses: list[dict] = []
        token = None
        while len(responses) < limit:
            params: dict[str, Any] = {"pageSize": min(5000, max(1, int(limit)))}
            if token:
                params["pageToken"] = token
            page = await google_request("GET", f"{GOOGLE_FORMS_API}/forms/{form_id}/responses", params=params) or {}
            responses.extend(page.get("responses") or [])
            token = page.get("nextPageToken")
            if not token:
                break
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, form_id)

    titles: dict[str, str] = {}
    order: list[str] = []
    for item in form.get("items") or []:
        question = (item.get("questionItem") or {}).get("question")
        if question and question.get("questionId"):
            titles[question["questionId"]] = item.get("title", "")
            order.append(question["questionId"])
        for row in (item.get("questionGroupItem") or {}).get("questions") or []:
            titles[row.get("questionId")] = f"{item.get('title', '')} — {row.get('rowQuestion', {}).get('title', '')}"
            order.append(row.get("questionId"))

    title = (form.get("info") or {}).get("title", form_id)
    if not responses:
        return f"No responses yet to '{title}' (form_id: {form_id})."
    responses = sorted(responses, key=lambda r: r.get("lastSubmittedTime") or r.get("createTime") or "")[:limit]
    blocks = [f"Responses to '{title}' (form_id: {form_id}): {len(responses)}{'+' if token else ''}"]
    for n, r in enumerate(responses, start=1):
        who = r.get("respondentEmail") or "anonymous"
        score = f", score {r['totalScore']}" if "totalScore" in r else ""
        lines = [f"\n#{n} — {who}, submitted {format_date(r.get('lastSubmittedTime') or r.get('createTime'))}{score}"]
        answers = r.get("answers") or {}
        for qid in order + [q for q in answers if q not in titles]:
            if qid in answers:
                lines.append(f"  {titles.get(qid, qid)}: {_answer_text(answers[qid])}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)
