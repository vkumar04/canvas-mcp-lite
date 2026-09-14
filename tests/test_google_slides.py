import asyncio
import json

import pytest

from canvas_mcp_lite import google_client
from canvas_mcp_lite.tools import google_slides

PRES_ID = "1zYxWvUtSrQpOnMlKjIhGfEdCbA9876543210_-abcd"
SLIDES_URL = f"https://docs.google.com/presentation/d/{PRES_ID}/edit#slide=id.p"


def _text(*paragraphs, bulleted=False, level=0):
    """Build a Slides `text` object from paragraph strings."""
    elements = []
    for p in paragraphs:
        marker = {"paragraphMarker": {}}
        if bulleted:
            marker["paragraphMarker"]["bullet"] = {"nestingLevel": level}
        elements.append(marker)
        elements.append({"textRun": {"content": p + "\n"}})
    return {"textElements": elements}


def _presentation():
    return {
        "presentationId": PRES_ID,
        "title": "Lecture 3: Argument",
        "layouts": [{"objectId": "lay1", "layoutProperties": {"displayName": "Title and body"}}],
        "slides": [
            {
                "objectId": "s1",
                "slideProperties": {
                    "layoutObjectId": "lay1",
                    "notesPage": {
                        "notesProperties": {"speakerNotesObjectId": "n1"},
                        "pageElements": [
                            {"objectId": "n1", "shape": {"text": _text("Remind them of the reading.")}}
                        ],
                    },
                },
                "pageElements": [
                    {
                        "objectId": "t1",
                        "shape": {"placeholder": {"type": "TITLE"}, "text": _text("What is a claim?")},
                    },
                    {
                        "objectId": "b1",
                        "shape": {
                            "placeholder": {"type": "BODY"},
                            "text": _text("Arguable", "Specific", bulleted=True),
                        },
                    },
                    {"objectId": "img1", "image": {}, "description": "Toulmin diagram"},
                ],
            },
            {
                "objectId": "s2",
                "slideProperties": {"notesPage": {"notesProperties": {"speakerNotesObjectId": "n2"}}},
                "pageElements": [
                    {"objectId": "empty1", "shape": {"shapeType": "TEXT_BOX"}},
                    {
                        "objectId": "tbl1",
                        "table": {
                            "rows": 1,
                            "columns": 2,
                            "tableRows": [
                                {"tableCells": [{"text": _text("Claim")}, {"text": {}}]}
                            ],
                        },
                    },
                ],
            },
        ],
    }


def test_extract_id_from_slides_url():
    assert google_slides._extract_id(SLIDES_URL) == PRES_ID
    with pytest.raises(ValueError, match="Google Slides ID"):
        google_slides._extract_id("https://example.com/deck")


def test_format_presentation_lists_ids_bullets_tables_and_notes():
    out = google_slides.format_presentation(_presentation())
    assert "Lecture 3: Argument (presentation_id: " + PRES_ID in out
    assert "2 slide(s)" in out
    assert "Slide 1 (slide_id: s1) — layout: Title and body" in out
    assert "[title id=t1]\n    What is a claim?" in out
    assert "[body id=b1]\n    - Arguable\n    - Specific" in out
    assert "[image id=img1] Toulmin diagram" in out
    assert "Speaker notes:\n    Remind them of the reading." in out
    assert "[text box id=empty1] (empty)" in out
    assert "tbl1:<row>:<col>" in out
    assert "[0:0] Claim | [0:1] (empty)" in out


def test_unconfigured_returns_setup_message(monkeypatch):
    for var in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(google_client, "_runtime_refresh_token", None)
    result = asyncio.run(google_slides.read_google_slides(SLIDES_URL))
    assert "canvas-mcp-google-auth" in result


def _fake(monkeypatch, presentation=None, replies=None):
    """Patch google_request; record batchUpdate/other write calls."""
    calls = []

    async def fake_request(method, path, params=None, json_body=None, raw=False):
        calls.append((method, path, params, json_body))
        if method == "GET" and path.endswith(f"/presentations/{PRES_ID}"):
            return presentation or _presentation()
        if path.endswith(":batchUpdate"):
            return {"replies": replies or []}
        return {}

    monkeypatch.setattr(google_slides, "google_request", fake_request)
    return calls


def _batch_requests(calls):
    return [c[3]["requests"] for c in calls if c[1].endswith(":batchUpdate")]


def test_update_text_replaces_existing_shape_text(monkeypatch):
    calls = _fake(monkeypatch)
    result = asyncio.run(google_slides.update_google_slide_text(SLIDES_URL, "t1", "What is a thesis?"))
    assert "Updated text of 't1'" in result
    (requests,) = _batch_requests(calls)
    assert requests == [
        {"deleteText": {"objectId": "t1", "textRange": {"type": "ALL"}}},
        {"insertText": {"objectId": "t1", "text": "What is a thesis?", "insertionIndex": 0}},
    ]


def test_update_text_skips_delete_on_empty_shape_and_adds_bullets(monkeypatch):
    calls = _fake(monkeypatch)
    asyncio.run(google_slides.update_google_slide_text(SLIDES_URL, "empty1", "- one\n- two\n  - nested"))
    (requests,) = _batch_requests(calls)
    assert requests[0] == {"insertText": {"objectId": "empty1", "text": "one\ntwo\n\tnested", "insertionIndex": 0}}
    assert requests[1]["createParagraphBullets"]["objectId"] == "empty1"
    assert requests[1]["createParagraphBullets"]["textRange"] == {"type": "ALL"}
    assert not any("deleteText" in r for r in requests)


def test_update_text_targets_table_cells(monkeypatch):
    calls = _fake(monkeypatch)
    asyncio.run(google_slides.update_google_slide_text(SLIDES_URL, "tbl1:0:1", "Evidence"))
    (requests,) = _batch_requests(calls)
    cell = {"rowIndex": 0, "columnIndex": 1}
    # cell [0:1] is empty → no deleteText
    assert requests == [
        {"insertText": {"objectId": "tbl1", "cellLocation": cell, "text": "Evidence", "insertionIndex": 0}}
    ]

    calls.clear()
    asyncio.run(google_slides.update_google_slide_text(SLIDES_URL, "tbl1:0:0", "Thesis"))
    (requests,) = _batch_requests(calls)
    assert requests[0]["deleteText"]["cellLocation"] == {"rowIndex": 0, "columnIndex": 0}


def test_update_text_rejects_unknown_or_non_text_ids(monkeypatch):
    calls = _fake(monkeypatch)
    assert "No element with id 'nope'" in asyncio.run(
        google_slides.update_google_slide_text(SLIDES_URL, "nope", "x")
    )
    assert "not a text element" in asyncio.run(
        google_slides.update_google_slide_text(SLIDES_URL, "img1", "x")
    )
    assert "not a table" in asyncio.run(
        google_slides.update_google_slide_text(SLIDES_URL, "t1:0:0", "x")
    )
    assert not _batch_requests(calls)


def test_replace_text_reports_count_and_scopes_to_slides(monkeypatch):
    calls = _fake(monkeypatch, replies=[{"replaceAllText": {"occurrencesChanged": 3}}])
    result = asyncio.run(
        google_slides.replace_text_in_google_slides(SLIDES_URL, "2025", "2026", slide_ids="s1, s2")
    )
    assert "Replaced 3 occurrence(s) of '2025' with '2026'" in result
    (requests,) = _batch_requests(calls)
    assert requests[0]["replaceAllText"]["containsText"] == {"text": "2025", "matchCase": True}
    assert requests[0]["replaceAllText"]["pageObjectIds"] == ["s1", "s2"]


def test_update_notes_uses_speaker_notes_shape(monkeypatch):
    calls = _fake(monkeypatch)
    result = asyncio.run(google_slides.update_google_slide_notes(SLIDES_URL, "s1", "New notes"))
    assert "Updated speaker notes on slide 's1'" in result
    (requests,) = _batch_requests(calls)
    assert requests[0] == {"deleteText": {"objectId": "n1", "textRange": {"type": "ALL"}}}
    assert requests[1]["insertText"]["objectId"] == "n1"

    calls.clear()  # slide 2 has a notes shape id but no text yet → insert only
    asyncio.run(google_slides.update_google_slide_notes(SLIDES_URL, "s2", "Fresh"))
    (requests,) = _batch_requests(calls)
    assert [list(r)[0] for r in requests] == ["insertText"]


def test_add_slide_maps_placeholders_for_layout():
    requests, slide_id = google_slides._add_slide_requests(
        "Evidence", "- quote\n- paraphrase", "", "title_and_body", 3
    )
    create = requests[0]["createSlide"]
    assert create["objectId"] == slide_id
    assert create["insertionIndex"] == 2
    assert create["slideLayoutReference"] == {"predefinedLayout": "TITLE_AND_BODY"}
    types = [m["layoutPlaceholder"]["type"] for m in create["placeholderIdMappings"]]
    assert types == ["TITLE", "BODY"]
    title_id = create["placeholderIdMappings"][0]["objectId"]
    body_id = create["placeholderIdMappings"][1]["objectId"]
    assert requests[1] == {"insertText": {"objectId": title_id, "text": "Evidence", "insertionIndex": 0}}
    assert requests[2]["insertText"] == {"objectId": body_id, "text": "quote\nparaphrase", "insertionIndex": 0}
    assert requests[3]["createParagraphBullets"]["objectId"] == body_id

    # Title layout uses the centered title + subtitle placeholders.
    requests, _ = google_slides._add_slide_requests("Week 1", "Argument", "", "title", None)
    types = [m["layoutPlaceholder"]["type"] for m in requests[0]["createSlide"]["placeholderIdMappings"]]
    assert types == ["CENTERED_TITLE", "SUBTITLE"]
    assert "insertionIndex" not in requests[0]["createSlide"]

    # Blank layout has no placeholders; text is dropped rather than erroring.
    requests, _ = google_slides._add_slide_requests("ignored", "ignored", "", "blank", None)
    assert requests[0]["createSlide"]["placeholderIdMappings"] == []
    assert len(requests) == 1

    with pytest.raises(ValueError, match="Unknown layout"):
        google_slides._add_slide_requests("x", "", "", "two_columns", None)


def test_add_slide_sets_notes_in_second_call(monkeypatch):
    state = {"pres": _presentation()}
    calls = []

    async def fake_request(method, path, params=None, json_body=None, raw=False):
        calls.append((method, path, json_body))
        if path.endswith(":batchUpdate"):
            for req in json_body["requests"]:
                if "createSlide" in req:
                    sid = req["createSlide"]["objectId"]
                    state["pres"]["slides"].append(
                        {
                            "objectId": sid,
                            "slideProperties": {"notesPage": {"notesProperties": {"speakerNotesObjectId": sid + "_notes"}}},
                            "pageElements": [],
                        }
                    )
            return {}
        return state["pres"]

    monkeypatch.setattr(google_slides, "google_request", fake_request)
    result = asyncio.run(google_slides.add_google_slide(SLIDES_URL, "Recap", "- a", notes="Say hi"))
    assert "Added slide 'Recap'" in result
    batches = [c[2]["requests"] for c in calls if c[1].endswith(":batchUpdate")]
    assert len(batches) == 2
    notes_req = batches[1][0]["insertText"]
    assert notes_req["objectId"].endswith("_notes") and notes_req["text"] == "Say hi"


def test_move_slide_adjusts_insertion_index(monkeypatch):
    pres = _presentation()
    pres["slides"].append({"objectId": "s3", "pageElements": []})
    calls = _fake(monkeypatch, presentation=pres)

    result = asyncio.run(google_slides.move_google_slide(SLIDES_URL, "s1", 3))
    assert "from position 1 to 3" in result
    (requests,) = _batch_requests(calls)
    assert requests[0]["updateSlidesPosition"] == {"slideObjectIds": ["s1"], "insertionIndex": 3}

    calls.clear()
    asyncio.run(google_slides.move_google_slide(SLIDES_URL, "s3", 1))
    (requests,) = _batch_requests(calls)
    assert requests[0]["updateSlidesPosition"]["insertionIndex"] == 0

    calls.clear()
    assert "already at position 2" in asyncio.run(google_slides.move_google_slide(SLIDES_URL, "s2", 2))
    assert not _batch_requests(calls)


def test_duplicate_and_delete_slide(monkeypatch):
    calls = _fake(monkeypatch)
    result = asyncio.run(google_slides.duplicate_google_slide(SLIDES_URL, "s1"))
    assert "Duplicated slide 's1'" in result
    (requests,) = _batch_requests(calls)
    dup = requests[0]["duplicateObject"]
    assert dup["objectId"] == "s1" and list(dup["objectIds"]) == ["s1"]

    calls.clear()
    assert "Deleted slide 's2'" in asyncio.run(google_slides.delete_google_slide(SLIDES_URL, "s2"))
    (requests,) = _batch_requests(calls)
    assert requests == [{"deleteObject": {"objectId": "s2"}}]


def test_create_deck_replaces_default_slide_and_returns_link(monkeypatch):
    calls = []

    async def fake_request(method, path, params=None, json_body=None, raw=False):
        calls.append((method, path, json_body))
        if method == "POST" and path.endswith("/presentations"):
            return {"presentationId": "NEWID", "slides": [{"objectId": "default_p"}]}
        if path.endswith(":batchUpdate"):
            return {}
        return {"presentationId": "NEWID", "slides": []}

    monkeypatch.setattr(google_slides, "google_request", fake_request)
    spec = json.dumps(
        [
            {"title": "Week 4", "body": "Argument", "layout": "title"},
            {"title": "Goals", "body": "- Define claim\n- Find evidence"},
        ]
    )
    result = asyncio.run(google_slides.create_google_slides("Week 4 lecture", spec))
    assert "Created Google Slides deck 'Week 4 lecture' with 2 slide(s)" in result
    assert "https://docs.google.com/presentation/d/NEWID/edit" in result

    assert calls[0][2] == {"title": "Week 4 lecture"}
    batch = calls[1][2]["requests"]
    assert [list(r)[0] for r in batch].count("createSlide") == 2
    assert batch[-1] == {"deleteObject": {"objectId": "default_p"}}


def test_create_deck_validates_spec(monkeypatch):
    calls = _fake(monkeypatch)
    assert "Invalid slides_json" in asyncio.run(google_slides.create_google_slides("Deck", "not json"))
    assert "Invalid slides_json" in asyncio.run(google_slides.create_google_slides("Deck", '{"title": "x"}'))
    assert "title is empty" in asyncio.run(google_slides.create_google_slides("  ", "[]"))
    assert not calls


def test_copy_deck_uses_drive_copy(monkeypatch):
    calls = []

    async def fake_request(method, path, params=None, json_body=None, raw=False):
        calls.append((method, path, params, json_body))
        return {"id": "COPYID", "name": json_body["name"]}

    monkeypatch.setattr(google_slides, "google_request", fake_request)
    result = asyncio.run(google_slides.copy_google_slides(SLIDES_URL, "Lecture 3 (Fall 2026)"))
    assert "presentation_id: COPYID" in result
    assert calls[0][0] == "POST" and calls[0][1] == f"/files/{PRES_ID}/copy"
    assert calls[0][3] == {"name": "Lecture 3 (Fall 2026)"}


def test_list_slides_builds_drive_query(monkeypatch):
    calls = []

    async def fake_request(method, path, params=None, json_body=None, raw=False):
        calls.append(params)
        return {
            "files": [
                {"id": "A1", "name": "Lecture 1", "modifiedTime": "2026-09-01T10:00:00Z",
                 "owners": [{"displayName": "Hayley Lawson"}]}
            ]
        }

    monkeypatch.setattr(google_slides, "google_request", fake_request)
    result = asyncio.run(google_slides.list_google_slides("Lecture O'Brien"))
    assert "Lecture 1 (presentation_id: A1)" in result and "Hayley Lawson" in result
    q = calls[0]["q"]
    assert "mimeType='application/vnd.google-apps.presentation'" in q
    assert "name contains 'Lecture O\\'Brien'" in q


def test_comment_posts_drive_comment_with_quote(monkeypatch):
    calls = []

    async def fake_request(method, path, params=None, json_body=None, raw=False):
        calls.append((method, path, json_body))
        return {"id": "c9", "content": json_body["content"], "author": {"displayName": "Hayley Lawson"}}

    monkeypatch.setattr(google_slides, "google_request", fake_request)
    result = asyncio.run(
        google_slides.comment_on_google_slides(SLIDES_URL, "Slide 2: cite the source.", quoted_text="Claim")
    )
    assert "Posted comment c9" in result and "as Hayley Lawson" in result and 're: "Claim"' in result
    body = calls[0][2]
    assert body["quotedFileContent"] == {"mimeType": "text/plain", "value": "Claim"}
    assert "anchor" not in body
    assert "empty" in asyncio.run(google_slides.comment_on_google_slides(SLIDES_URL, "   "))


def test_permission_error_names_connected_account(monkeypatch):
    async def fake_request(method, path, params=None, json_body=None, raw=False):
        raise google_client.GoogleAPIError(403, "forbidden", path)

    async def fake_email():
        return "hayley@example.edu"

    monkeypatch.setattr(google_slides, "google_request", fake_request)
    monkeypatch.setattr(google_slides, "connected_account_email", fake_email)
    result = asyncio.run(google_slides.read_google_slides(SLIDES_URL))
    assert "hayley@example.edu" in result and "Slides API" in result
