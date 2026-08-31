"""Grade Google Docs submissions in place: read the live doc, then leave
feedback as real Google Docs comments from the instructor's account."""

from __future__ import annotations

import re
from typing import Union

import os

from ..client import canvas_paginated
from ..google_client import (
    GoogleAPIError,
    GoogleConfigError,
    connected_account_email,
    extract_doc_id,
    google_request,
)
from ..google_oauth_flow import begin_auth, redirect_uri
from ..util import format_date, get_course_id
from .files import _cap_text

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"

# Fields the Drive comments API requires us to enumerate explicitly.
_COMMENT_FIELDS = (
    "id,content,author(displayName),createdTime,resolved,"
    "quotedFileContent(value),replies(author(displayName),content,createdTime)"
)


async def _explain_api_error(exc: GoogleAPIError, doc_id: str) -> str:
    if exc.status_code in (403, 404):
        email = await connected_account_email()
        acting_as = f" ({email})" if email else ""
        return (
            f"Can't access Google Doc {doc_id} (HTTP {exc.status_code}). The "
            f"connected instructor Google account{acting_as} most likely doesn't "
            "have access to this document. Ask the student to share the doc with "
            "that account as Commenter or higher (or set link sharing to 'Anyone "
            "with the link — Commenter'), then try again."
        )
    return str(exc)


async def google_docs_status() -> str:
    """Check whether Google Docs grading is set up on this server: which
    credentials are present, which Google account is connected, and the next
    setup step if anything is missing. Run this first when a Google Docs tool
    reports a configuration problem."""
    has_client = bool(
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID") and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    )
    lines = ["Google Docs grading setup:"]
    lines.append(f"- OAuth client (admin-provided): {'configured' if has_client else 'MISSING'}")
    if not has_client:
        lines.append(
            "\nNext step (server admin, one-time): at console.cloud.google.com enable "
            "the Google Drive API, publish the OAuth consent screen, create a 'Web "
            "application' OAuth client with authorized redirect URI "
            f"{redirect_uri() or '<server URL>/oauth/google/callback'}, and set "
            "GOOGLE_OAUTH_CLIENT_ID + GOOGLE_OAUTH_CLIENT_SECRET in the server env."
        )
        return "\n".join(lines)

    email = await connected_account_email()
    if email:
        lines.append(f"- Connected Google account: {email} — doc comments will post as this account.")
        lines.append("\nEverything is ready. Use connect_google_docs only to switch accounts.")
    else:
        lines.append("- Connected Google account: NONE (or the stored token stopped working)")
        lines.append(
            "\nNext step (instructor): run the connect_google_docs tool, open the "
            "sign-in link it returns, and approve access with the Google account "
            "that should own the doc comments."
        )
    return "\n".join(lines)


async def connect_google_docs() -> str:
    """Connect (or switch) the Google account used for doc commenting — returns a
    Google sign-in link for the INSTRUCTOR to open in their own browser. Approving
    lands back on this server and activates commenting immediately; the page also
    shows the GOOGLE_OAUTH_REFRESH_TOKEN to store in the server's environment so
    the connection survives restarts. Comments post as whichever account approves,
    so the instructor — not an assistant — should click the link."""
    try:
        url = begin_auth()
    except GoogleConfigError as exc:
        return str(exc)
    return (
        "Have the instructor open this link in their browser and approve access "
        "(valid for 10 minutes, single use):\n\n"
        f"{url}\n\n"
        "The confirmation page activates commenting right away and shows a "
        "GOOGLE_OAUTH_REFRESH_TOKEN value to save in the server's environment "
        "(Railway → Variables) so the connection survives restarts."
    )


_LINK_PATTERN = re.compile(r"https://(?:docs|drive)\.google\.com/\S+")


def _find_doc_links(text: str) -> list[str]:
    """Google Doc/Drive links in a blob of text that carry a usable file ID."""
    links = []
    for raw in _LINK_PATTERN.findall(text or ""):
        url = raw.rstrip(".,;:!?'\")>]")
        try:
            extract_doc_id(url)
        except ValueError:
            continue
        links.append(url)
    return links


async def list_google_doc_links(
    course_identifier: Union[str, int], assignment_id: Union[str, int]
) -> str:
    """Collect every student's Google Doc link for an assignment where students
    post their doc link as a submission comment (also catches online_url
    submissions). One call gives the whole-class roster of links plus who hasn't
    posted one yet — start here when grading Google Docs submissions, then use
    read_google_doc / comment_on_google_doc per student."""
    course_id = await get_course_id(course_identifier)
    subs = await canvas_paginated(
        f"/courses/{course_id}/assignments/{assignment_id}/submissions",
        {"include[]": ["submission_comments", "user"]},
    )
    if not subs:
        return "No submissions found."

    found: list[str] = []
    missing: list[str] = []
    for s in subs:
        user = s.get("user") or {}
        name = user.get("name", "Unknown")
        if user.get("name") == "Test Student":
            continue
        student_id = s.get("user_id")

        own_links: list[str] = []
        other_links: list[tuple[str, str]] = []  # (url, author_name)
        for c in s.get("submission_comments") or []:
            for url in _find_doc_links(c.get("comment") or ""):
                if c.get("author_id") == student_id:
                    own_links.append(url)
                else:
                    other_links.append((url, c.get("author_name", "?")))
        if s.get("submission_type") == "online_url":
            own_links.extend(_find_doc_links(s.get("url") or ""))

        if own_links:
            # Most recent link wins — students repost after fixing sharing.
            found.append(f"- {name} (user_id={student_id}): {own_links[-1]}")
        elif other_links:
            url, author = other_links[-1]
            found.append(f"- {name} (user_id={student_id}): {url} (posted by {author}, not the student)")
        else:
            n_comments = len(s.get("submission_comments") or [])
            detail = f"{n_comments} comment(s), none with a doc link" if n_comments else "no submission comments"
            missing.append(f"- {name} (user_id={student_id}) — {detail}")

    total = len(found) + len(missing)
    parts = [f"Google Doc links for assignment {assignment_id} ({len(found)} of {total} students):"]
    if found:
        parts.append("\n".join(found))
    if missing:
        parts.append(f"No Google Doc link yet ({len(missing)}):\n" + "\n".join(missing))
    return "\n\n".join(parts)


async def read_google_doc(doc_url: str) -> str:
    """Read the live text of a Google Doc the student submitted (get the URL from
    list_google_doc_links or get_submission_content, or pass a bare Drive file
    ID). Reads the CURRENT
    version of the doc, not a snapshot. Use this before comment_on_google_doc so
    feedback quotes the student's actual wording."""
    try:
        doc_id = extract_doc_id(doc_url)
    except ValueError as exc:
        return str(exc)
    try:
        meta = await google_request(
            "GET",
            f"/files/{doc_id}",
            params={"fields": "name,mimeType", "supportsAllDrives": "true"},
        )
        if meta.get("mimeType") != GOOGLE_DOC_MIME:
            return (
                f"'{meta.get('name')}' is not a Google Doc (type: {meta.get('mimeType')}). "
                "Only Google Docs are supported for reading and commenting."
            )
        text = await google_request(
            "GET",
            f"/files/{doc_id}/export",
            params={"mimeType": "text/plain"},
            raw=True,
        )
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, doc_id)

    name = meta.get("name", doc_id)
    body = text.strip() or "(document is empty)"
    return f"Google Doc: {name} (doc_id: {doc_id})\n\n{_cap_text(body, name)}"


async def list_google_doc_comments(doc_url: str) -> str:
    """List the comment threads already on a student's Google Doc (including
    resolved ones and replies). Check this before commenting so feedback doesn't
    duplicate what peer reviewers or the instructor already said."""
    try:
        doc_id = extract_doc_id(doc_url)
    except ValueError as exc:
        return str(exc)
    try:
        result = await google_request(
            "GET",
            f"/files/{doc_id}/comments",
            params={
                "fields": f"comments({_COMMENT_FIELDS}),nextPageToken",
                "pageSize": 100,
            },
        )
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, doc_id)

    comments = result.get("comments") or []
    if not comments:
        return f"No comments on Google Doc {doc_id}."

    blocks = []
    for c in comments:
        author = (c.get("author") or {}).get("displayName", "?")
        status = " [RESOLVED]" if c.get("resolved") else ""
        quote = (c.get("quotedFileContent") or {}).get("value")
        lines = [f"[{format_date(c.get('createdTime'))}] {author}{status}: {c.get('content', '')}"]
        if quote:
            lines.append(f'  re: "{quote}"')
        for reply in c.get("replies") or []:
            reply_author = (reply.get("author") or {}).get("displayName", "?")
            lines.append(
                f"  ↳ [{format_date(reply.get('createdTime'))}] {reply_author}: {reply.get('content', '')}"
            )
        blocks.append("\n".join(lines))

    note = "\n\n(Showing first 100 comment threads — more exist.)" if result.get("nextPageToken") else ""
    return f"Comments on Google Doc {doc_id}:\n\n" + "\n\n".join(blocks) + note


async def comment_on_google_doc(doc_url: str, comment: str, quoted_text: str = "") -> str:
    """Leave one feedback comment on a student's Google Doc, posted from the
    connected instructor Google account. Pass the exact passage the feedback is
    about as quoted_text — it's shown in the comment card so the student sees
    what the comment refers to (the Drive API can't highlight/anchor text in the
    doc itself, so the quote is the anchor). Make one call per piece of feedback;
    leave quoted_text empty only for whole-document comments. Grades still go
    through grade_submission in Canvas — this posts feedback only."""
    try:
        doc_id = extract_doc_id(doc_url)
    except ValueError as exc:
        return str(exc)
    if not comment.strip():
        return "Comment text is empty — nothing posted."

    body: dict = {"content": comment}
    if quoted_text.strip():
        body["quotedFileContent"] = {"mimeType": "text/plain", "value": quoted_text.strip()}

    try:
        created = await google_request(
            "POST",
            f"/files/{doc_id}/comments",
            params={"fields": _COMMENT_FIELDS},
            json_body=body,
        )
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, doc_id)

    author = (created.get("author") or {}).get("displayName", "the connected account")
    quote_note = f'\nre: "{quoted_text.strip()}"' if quoted_text.strip() else ""
    return (
        f"Posted comment {created.get('id')} on doc {doc_id} as {author}:"
        f"\n{created.get('content', comment)}{quote_note}"
    )
