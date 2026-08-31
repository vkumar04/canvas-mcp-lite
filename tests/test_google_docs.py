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
