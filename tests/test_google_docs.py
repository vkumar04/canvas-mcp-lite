import asyncio

import pytest

from canvas_mcp_lite import google_client
from canvas_mcp_lite.tools import google_docs

DOC_ID = "1a2B3c4D5e6F7g8H9i0JkLmNoPqRsTuVwXyZ_-abcde"


@pytest.mark.parametrize(
    "url",
    [
        f"https://docs.google.com/document/d/{DOC_ID}/edit?usp=sharing",
        f"https://docs.google.com/document/d/{DOC_ID}",
        f"https://docs.google.com/document/u/1/d/{DOC_ID}/edit#heading=h.abc",
        f"https://drive.google.com/file/d/{DOC_ID}/view",
        f"https://drive.google.com/open?id={DOC_ID}",
        DOC_ID,
        f"  {DOC_ID}  ",
    ],
)
def test_extract_doc_id(url):
    assert google_client.extract_doc_id(url) == DOC_ID


@pytest.mark.parametrize(
    "bad",
    [
        "https://docs.google.com/document/d/e/2PACX-1vTabcdefghijk/pub",  # published link
        "https://example.com/essay",
        "not a url",
    ],
)
def test_extract_doc_id_rejects(bad):
    with pytest.raises(ValueError):
        google_client.extract_doc_id(bad)


def test_unconfigured_returns_setup_message(monkeypatch):
    for var in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    result = asyncio.run(
        google_docs.comment_on_google_doc(
            f"https://docs.google.com/document/d/{DOC_ID}/edit", "Nice thesis."
        )
    )
    assert "canvas-mcp-google-auth" in result


def test_comment_posts_with_quote(monkeypatch):
    calls = {}

    async def fake_request(method, path, params=None, json_body=None, raw=False):
        if method == "GET":  # doc lookup for anchoring; quote isn't in this doc
            return _document("Unrelated wording.\n")
        calls["method"] = method
        calls["path"] = path
        calls["body"] = json_body
        return {"id": "c1", "content": json_body["content"], "author": {"displayName": "Hayley"}}

    monkeypatch.setattr(google_docs, "google_request", fake_request)
    result = asyncio.run(
        google_docs.comment_on_google_doc(
            f"https://docs.google.com/document/d/{DOC_ID}/edit",
            "Sharpen this claim.",
            quoted_text="I have always been a writer.",
        )
    )
    assert calls["method"] == "POST"
    assert calls["path"] == f"/files/{DOC_ID}/comments"
    assert calls["body"]["quotedFileContent"]["value"] == "I have always been a writer."
    assert "Posted comment c1" in result
    assert "Hayley" in result


def _document(*paragraphs):
    """Minimal documents.get body: paragraphs laid out from index 1, the way
    Docs numbers a real document."""
    content = [{"startIndex": 0, "endIndex": 1, "sectionBreak": {}}]
    index = 1
    for text in paragraphs:
        length = len(text.encode("utf-16-le")) // 2
        content.append(
            {
                "startIndex": index,
                "endIndex": index + length,
                "paragraph": {
                    "elements": [
                        {
                            "startIndex": index,
                            "endIndex": index + length,
                            "textRun": {"content": text},
                        }
                    ]
                },
            }
        )
        index += length
    return {"body": {"content": content}}


def test_locate_range_maps_to_doc_indices():
    doc = _document("The thesis is weak.\n", "But the evidence is strong.\n")
    assert google_docs._locate_range(doc, "thesis") == (5, 11)
    # Second paragraph starts at 1 + len("The thesis is weak.\n") == 21
    assert google_docs._locate_range(doc, "evidence") == (29, 37)


def test_locate_range_tolerates_whitespace_and_missing_text():
    doc = _document("A claim that\nspans a line break.\n")
    assert google_docs._locate_range(doc, "claim   that spans") == (3, 19)
    assert google_docs._locate_range(doc, "nowhere in the doc") is None


def test_locate_range_counts_utf16_units_and_occurrences():
    # The emoji is one Python char but two Docs indices, so text after it shifts.
    doc = _document("Nice 😄 work. Nice work.\n")
    assert google_docs._locate_range(doc, "work", occurrence=1) == (9, 13)
    assert google_docs._locate_range(doc, "work", occurrence=2) == (20, 24)
    assert google_docs._locate_range(doc, "work", occurrence=3) is None


def test_locate_range_folds_smart_punctuation_and_case():
    # Docs auto-converts quotes/dashes as students type; the LLM quotes ASCII.
    doc = _document("The author\u2019s \u201cbold\u201d claim \u2014 stated twice.\n")
    assert google_docs._locate_range(doc, "the author's \"bold\" claim - stated") == (1, 35)
    # Folding must not shift indices for text after an ellipsis (1 char -> 3).
    doc = _document("First\u2026 then second.\n")
    assert google_docs._locate_range(doc, "then second") == (8, 19)
    assert google_docs._locate_range(doc, "First... then") == (1, 12)


def test_locate_range_end_stops_before_inline_object():
    # "word" [image] "next": the image occupies index 5; the quote must end at 5.
    doc = {
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {"startIndex": 1, "endIndex": 5, "textRun": {"content": "word"}},
                            {"startIndex": 5, "endIndex": 6, "inlineObjectElement": {"inlineObjectId": "i"}},
                            {"startIndex": 6, "endIndex": 11, "textRun": {"content": "next\n"}},
                        ]
                    }
                }
            ]
        }
    }
    assert google_docs._locate_range(doc, "word") == (1, 5)
    assert google_docs._locate_range(doc, "next") == (6, 10)


def test_locate_range_reads_table_cells():
    doc = {
        "body": {
            "content": [
                {
                    "table": {
                        "tableRows": [
                            {
                                "tableCells": [
                                    {
                                        "content": [
                                            {
                                                "paragraph": {
                                                    "elements": [
                                                        {
                                                            "startIndex": 40,
                                                            "endIndex": 52,
                                                            "textRun": {"content": "cell wording"},
                                                        }
                                                    ]
                                                }
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                }
            ]
        }
    }
    assert google_docs._locate_range(doc, "wording") == (45, 52)


def test_comment_anchors_via_docs_api(monkeypatch):
    calls = []

    async def fake_request(method, path, params=None, json_body=None, raw=False):
        calls.append((method, path, json_body))
        if method == "GET":
            return _document("The thesis is weak.\n")
        return {}

    monkeypatch.setattr(google_docs, "google_request", fake_request)
    result = asyncio.run(
        google_docs.comment_on_google_doc(DOC_ID, "Sharpen this.", quoted_text="thesis")
    )

    assert calls[-1][1].endswith(f"/documents/{DOC_ID}:batchUpdate")
    assert calls[-1][2]["requests"][0]["insertComment"] == {
        "content": "Sharpen this.",
        "range": {"startIndex": 5, "endIndex": 11},
    }
    assert "anchored comment" in result
    # Never fall through to an unanchored Drive comment once anchoring worked.
    assert not any(path.endswith("/comments") for _, path, _ in calls)


def test_comment_falls_back_when_preview_access_missing(monkeypatch):
    async def fake_request(method, path, params=None, json_body=None, raw=False):
        if method == "GET":
            return _document("The thesis is weak.\n")
        if "batchUpdate" in path:
            raise google_client.GoogleAPIError(403, "preview only", path)
        return {"id": "c9", "content": json_body["content"], "author": {"displayName": "Hayley"}}

    monkeypatch.setattr(google_docs, "google_request", fake_request)
    result = asyncio.run(
        google_docs.comment_on_google_doc(DOC_ID, "Sharpen this.", quoted_text="thesis")
    )
    assert "Posted comment c9" in result
    assert "preview access" in result
    assert "Not anchored" in result


def test_comment_falls_back_when_docs_reports_comment_not_saved(monkeypatch):
    """batchUpdate can return 200 with commentUpdateState=ALL_FAILED_UNKNOWN_REASON."""
    posted = {}

    async def fake_request(method, path, params=None, json_body=None, raw=False):
        if method == "GET":
            return _document("The thesis is weak.\n")
        if "batchUpdate" in path:
            return {"documentId": DOC_ID, "commentUpdateState": "ALL_FAILED_UNKNOWN_REASON"}
        posted["body"] = json_body
        return {"id": "c8", "content": json_body["content"], "author": {"displayName": "Hayley"}}

    monkeypatch.setattr(google_docs, "google_request", fake_request)
    result = asyncio.run(
        google_docs.comment_on_google_doc(DOC_ID, "Sharpen this.", quoted_text="thesis")
    )
    assert posted["body"]["quotedFileContent"]["value"] == "thesis"
    assert "failed to save the comment thread" in result


def test_comment_falls_back_when_quote_absent(monkeypatch):
    posted = {}

    async def fake_request(method, path, params=None, json_body=None, raw=False):
        if method == "GET":
            return _document("Something else entirely.\n")
        posted["body"] = json_body
        return {"id": "c7", "content": json_body["content"], "author": {"displayName": "Hayley"}}

    monkeypatch.setattr(google_docs, "google_request", fake_request)
    result = asyncio.run(
        google_docs.comment_on_google_doc(DOC_ID, "Sharpen this.", quoted_text="the thesis")
    )
    # The quote still reaches the student in the comment card.
    assert posted["body"]["quotedFileContent"]["value"] == "the thesis"
    assert "isn't in the document" in result


def test_anchor_field_is_never_sent_to_drive(monkeypatch):
    """A Drive `anchor` renders as 'Original content deleted' in Docs."""
    seen = {}

    async def fake_request(method, path, params=None, json_body=None, raw=False):
        if method == "GET":
            raise google_client.GoogleAPIError(404, "no preview", path)
        seen["body"] = json_body
        return {"id": "c2", "content": json_body["content"], "author": {"displayName": "Hayley"}}

    monkeypatch.setattr(google_docs, "google_request", fake_request)
    asyncio.run(google_docs.comment_on_google_doc(DOC_ID, "Note.", quoted_text="anything"))
    assert "anchor" not in seen["body"]


def test_empty_comment_not_posted():
    result = asyncio.run(
        google_docs.comment_on_google_doc(
            f"https://docs.google.com/document/d/{DOC_ID}/edit", "   "
        )
    )
    assert "nothing posted" in result.lower()


def test_list_google_doc_links_from_comments(monkeypatch):
    doc_url = f"https://docs.google.com/document/d/{DOC_ID}/edit?usp=sharing"

    async def fake_course_id(identifier):
        return 264948

    async def fake_paginated(path, params=None):
        assert "submission_comments" in params["include[]"]
        return [
            {  # link in the student's own comment, trailing period to trim
                "user_id": 1,
                "user": {"name": "Alice A"},
                "submission_comments": [
                    {"author_id": 1, "author_name": "Alice A", "comment": f"Here it is: {doc_url}."}
                ],
            },
            {  # no link at all
                "user_id": 2,
                "user": {"name": "Bob B"},
                "submission_comments": [{"author_id": 2, "author_name": "Bob B", "comment": "done!"}],
            },
            {  # link posted by someone else — flagged
                "user_id": 3,
                "user": {"name": "Carol C"},
                "submission_comments": [
                    {"author_id": 99, "author_name": "Hayley Lawson", "comment": doc_url}
                ],
            },
            {  # online_url submission counts too
                "user_id": 4,
                "user": {"name": "Dan D"},
                "submission_type": "online_url",
                "url": doc_url,
                "submission_comments": [],
            },
            {"user_id": 5, "user": {"name": "Test Student"}, "submission_comments": []},
        ]

    monkeypatch.setattr(google_docs, "get_course_id", fake_course_id)
    monkeypatch.setattr(google_docs, "canvas_paginated", fake_paginated)
    result = asyncio.run(google_docs.list_google_doc_links("264948", 3025891))

    assert "3 of 4 students" in result
    assert f"- Alice A (user_id=1): {doc_url}" in result
    assert "posted by Hayley Lawson, not the student" in result
    assert f"- Dan D (user_id=4): {doc_url}" in result
    assert "Bob B" in result.split("No Google Doc link yet")[1]
    assert "Test Student" not in result


def test_find_doc_links_ignores_non_doc_urls():
    text = (
        "see https://example.com/a and "
        "https://docs.google.com/document/d/e/2PACX-1vTabcdefghijk/pub (published) "
        f"but really https://docs.google.com/document/d/{DOC_ID}/edit"
    )
    assert google_docs._find_doc_links(text) == [
        f"https://docs.google.com/document/d/{DOC_ID}/edit"
    ]


def test_analyze_changelog_sessions_and_pastes():
    minute = 60_000
    t0 = 1_788_000_000_000
    changelog = [
        [{"ty": "is", "ibi": 1, "s": "Hello "}, t0, "u1"],
        [{"ty": "is", "ibi": 7, "s": "world"}, t0 + minute, "u1"],
        [{"ty": "ds", "si": 1, "ei": 5}, t0 + 2 * minute, "u1"],
        # 30-minute gap -> new session; one large paste inside a mlti wrapper
        [{"ty": "mlti", "mts": [{"ty": "is", "ibi": 1, "s": "x" * 250}]}, t0 + 32 * minute, "u1"],
        ["not-a-valid-entry"],
    ]
    stats = google_docs._analyze_changelog(changelog)
    assert stats["events"] == 4
    assert len(stats["sessions"]) == 2
    assert stats["total_ins"] == 11 + 250
    assert stats["total_del"] == 5
    assert stats["large_ins_chars"] == 250
    assert len(stats["large_inserts"]) == 1
    assert stats["users"] == ["u1"]


def test_permission_error_names_connected_account(monkeypatch):
    async def fake_request(method, path, params=None, json_body=None, raw=False):
        raise google_client.GoogleAPIError(403, "insufficient permissions", "url")

    async def fake_email():
        return "hlawson3@charlotte.edu"

    monkeypatch.setattr(google_docs, "google_request", fake_request)
    monkeypatch.setattr(google_docs, "connected_account_email", fake_email)
    result = asyncio.run(google_docs.read_google_doc(DOC_ID))
    assert "hlawson3@charlotte.edu" in result
    assert "share" in result.lower()


# --- Direct edits ---------------------------------------------------------------


def _edit_harness(monkeypatch, *paragraphs, replies=None):
    calls = []

    async def fake_request(method, path, params=None, json_body=None, raw=False):
        calls.append((method, path, json_body))
        if method == "GET":
            return _document(*paragraphs)
        return {"replies": replies or []}

    monkeypatch.setattr(google_docs, "google_request", fake_request)
    return calls


def _batch(calls):
    return [c[2]["requests"] for c in calls if c[1].endswith(":batchUpdate")]


def test_edit_text_deletes_range_then_inserts(monkeypatch):
    calls = _edit_harness(monkeypatch, "The thesis is weak.\n")
    result = asyncio.run(google_docs.edit_google_doc_text(DOC_ID, "thesis is weak", "argument is strong"))
    assert "replaced" in result
    (requests,) = _batch(calls)
    assert requests == [
        {"deleteContentRange": {"range": {"startIndex": 5, "endIndex": 19}}},
        {"insertText": {"text": "argument is strong", "location": {"index": 5}}},
    ]


def test_edit_text_empty_replacement_deletes(monkeypatch):
    calls = _edit_harness(monkeypatch, "Drop this. Keep this.\n")
    result = asyncio.run(google_docs.edit_google_doc_text(DOC_ID, "Drop this. ", ""))
    assert "deleted" in result
    (requests,) = _batch(calls)
    assert [list(r)[0] for r in requests] == ["deleteContentRange"]


def test_edit_text_missing_passage_makes_no_change(monkeypatch):
    calls = _edit_harness(monkeypatch, "Some text.\n")
    result = asyncio.run(google_docs.edit_google_doc_text(DOC_ID, "not here", "x"))
    assert "isn't in the document" in result
    assert not _batch(calls)
    assert "empty" in asyncio.run(google_docs.edit_google_doc_text(DOC_ID, "  ", "x"))


def test_insert_after_and_before_anchor(monkeypatch):
    calls = _edit_harness(monkeypatch, "First claim. Second claim.\n")
    asyncio.run(google_docs.insert_text_in_google_doc(DOC_ID, " (cite)", anchor_text="First claim."))
    (requests,) = _batch(calls)
    assert requests[0]["insertText"] == {"text": " (cite)", "location": {"index": 13}}

    calls.clear()
    asyncio.run(
        google_docs.insert_text_in_google_doc(DOC_ID, "Note: ", anchor_text="Second", position="before")
    )
    (requests,) = _batch(calls)
    assert requests[0]["insertText"]["location"] == {"index": 14}


def test_insert_appends_as_new_paragraph(monkeypatch):
    calls = _edit_harness(monkeypatch, "Body.\n")
    result = asyncio.run(google_docs.insert_text_in_google_doc(DOC_ID, "Instructor note"))
    assert "Appended" in result
    (requests,) = _batch(calls)
    assert requests[0]["insertText"] == {"text": "\nInstructor note", "endOfSegmentLocation": {}}

    calls.clear()  # an empty doc gets no leading newline
    _edit_harness(monkeypatch, "\n")
    asyncio.run(google_docs.insert_text_in_google_doc(DOC_ID, "Start"))


def test_insert_validates_inputs(monkeypatch):
    calls = _edit_harness(monkeypatch, "Body.\n")
    assert "empty" in asyncio.run(google_docs.insert_text_in_google_doc(DOC_ID, ""))
    assert "position must be" in asyncio.run(
        google_docs.insert_text_in_google_doc(DOC_ID, "x", anchor_text="Body", position="middle")
    )
    assert "anchor passage isn't" in asyncio.run(
        google_docs.insert_text_in_google_doc(DOC_ID, "x", anchor_text="missing")
    )
    assert not _batch(calls)


def test_replace_text_reports_count(monkeypatch):
    calls = _edit_harness(monkeypatch, replies=[{"replaceAllText": {"occurrencesChanged": 4}}])
    result = asyncio.run(google_docs.replace_text_in_google_doc(DOC_ID, "teh", "the", match_case=False))
    assert "Replaced 4 occurrence(s)" in result
    (requests,) = _batch(calls)
    assert requests[0]["replaceAllText"] == {
        "containsText": {"text": "teh", "matchCase": False},
        "replaceText": "the",
    }
    assert not any(c[0] == "GET" for c in calls)  # no document read needed
