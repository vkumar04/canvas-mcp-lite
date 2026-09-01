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


# --- Revision-history forensics (Draftback-style) ---------------------------

SESSION_GAP_MS = 10 * 60 * 1000  # a >10-minute pause starts a new editing session
LARGE_INSERT_CHARS = 100  # a single op this big usually means pasted text

def _fmt_millis(ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _command_chars(cmd: dict) -> tuple[int, int, list[str]]:
    """(chars_inserted, chars_deleted, large_insert_texts) for one changelog command."""
    ty = cmd.get("ty")
    if ty == "is":
        text = cmd.get("s") or ""
        return len(text), 0, [text] if len(text) >= LARGE_INSERT_CHARS else []
    if ty == "ds":
        return 0, cmd.get("ei", 0) - cmd.get("si", 0) + 1, []
    if ty == "mlti":
        ins = dele = 0
        large: list[str] = []
        for sub in cmd.get("mts") or []:
            if isinstance(sub, dict):
                i, d, l = _command_chars(sub)
                ins, dele = ins + i, dele + d
                large.extend(l)
        return ins, dele, large
    return 0, 0, []


def _analyze_changelog(changelog: list) -> dict:
    """Reduce a Docs changelog ([command, millis, user_id, ...] entries) to
    session/paste/volume facts."""
    events = []  # (millis, user_id, ins, dele, large_texts)
    for entry in changelog:
        if not (isinstance(entry, list) and entry and isinstance(entry[0], dict)):
            continue
        millis = entry[1] if len(entry) > 1 and isinstance(entry[1], (int, float)) else None
        if millis is None:
            continue
        user = str(entry[2]) if len(entry) > 2 else ""
        ins, dele, large = _command_chars(entry[0])
        events.append((int(millis), user, ins, dele, large))

    events.sort(key=lambda e: e[0])
    sessions: list[dict] = []
    large_inserts: list[tuple[int, str]] = []
    total_ins = total_del = 0
    for millis, user, ins, dele, large in events:
        if not sessions or millis - sessions[-1]["end"] > SESSION_GAP_MS:
            sessions.append({"start": millis, "end": millis, "ops": 0, "ins": 0, "del": 0})
        s = sessions[-1]
        s["end"] = millis
        s["ops"] += 1
        s["ins"] += ins
        s["del"] += dele
        total_ins += ins
        total_del += dele
        large_inserts.extend((millis, text) for text in large)

    return {
        "events": len(events),
        "sessions": sessions,
        "large_inserts": large_inserts,
        "total_ins": total_ins,
        "total_del": total_del,
        "large_ins_chars": sum(len(t) for _, t in large_inserts),
        "users": sorted({u for _, u, _, _, _ in events if u}),
    }


async def get_google_doc_forensics(doc_url: str) -> str:
    """Draftback-style revision-history facts for a Google Doc submission: when
    it was created and edited, how many distinct editing sessions, how much text
    was typed in small edits vs. added in large single insertions (usually
    pastes), and who edited. Reports observable metadata facts only — a large
    paste may be the student's own drafting from another document, dictation, or
    a quote, so interpretation belongs to the instructor. Requires the doc to be
    shared with the connected instructor account (same access as read_google_doc)."""
    try:
        doc_id = extract_doc_id(doc_url)
    except ValueError as exc:
        return str(exc)

    try:
        meta = await google_request(
            "GET", f"/files/{doc_id}", params={"fields": "name", "supportsAllDrives": "true"}
        )
        tiles_text = await google_request(
            "GET",
            f"https://docs.google.com/document/d/{doc_id}/revisions/tiles",
            params={
                "id": doc_id,
                "start": 1,
                "showDetailedRevisions": "false",
                "filterNamed": "false",
                "includes_info_params": "true",
            },
            raw=True,
        )
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, doc_id)

    import json as _json

    tiles = _json.loads(tiles_text[4:]) if tiles_text.startswith(")]}'") else _json.loads(tiles_text)
    tile_info = tiles.get("tileInfo") or []
    user_map = {
        uid: (info or {}).get("name", uid) for uid, info in (tiles.get("userMap") or {}).items()
    }
    if not tile_info:
        return f"No revision history available for doc {doc_id}."
    latest_rev = max(t.get("end", 0) for t in tile_info)

    try:
        load_text = await google_request(
            "GET",
            f"https://docs.google.com/document/d/{doc_id}/revisions/load",
            params={"id": doc_id, "start": 1, "end": latest_rev},
            raw=True,
        )
        payload = _json.loads(load_text[4:]) if load_text.startswith(")]}'") else _json.loads(load_text)
        changelog = payload.get("changelog") or []
    except (GoogleAPIError, ValueError):
        # Detailed changelog unavailable — fall back to the coarse tile timeline.
        lines = [f"Revision history for '{meta.get('name')}' (doc_id: {doc_id}) — coarse only:"]
        for t in tile_info:
            who = ", ".join(user_map.get(u, u) for u in t.get("users", []))
            lines.append(
                f"- revisions {t.get('start')}–{t.get('end')}, last change "
                f"{_fmt_millis(t.get('endMillis', 0))} by {who}"
            )
        return "\n".join(lines)

    stats = _analyze_changelog(changelog)
    if not stats["events"]:
        return f"Revision log for '{meta.get('name')}' contained no readable edit events."

    sessions = stats["sessions"]
    first, last = sessions[0]["start"], sessions[-1]["end"]
    active_minutes = sum(max(1, round((s["end"] - s["start"]) / 60000)) for s in sessions)
    editors = ", ".join(user_map.get(u, u) for u in stats["users"]) or "unknown"
    typed = stats["total_ins"] - stats["large_ins_chars"]

    lines = [
        f"Revision history for '{meta.get('name')}' (doc_id: {doc_id})",
        f"Editors: {editors}",
        f"First edit: {_fmt_millis(first)} | Last edit: {_fmt_millis(last)}",
        f"Edit operations: {stats['events']} across {len(sessions)} editing session(s) "
        f"(~{active_minutes} min of active editing; a >10-min pause starts a new session)",
        f"Characters: {stats['total_ins']:,} inserted "
        f"({typed:,} in small edits, {stats['large_ins_chars']:,} in large insertions), "
        f"{stats['total_del']:,} deleted",
        "",
        "Sessions:",
    ]
    for s in sessions:
        minutes = max(1, round((s["end"] - s["start"]) / 60000))
        lines.append(
            f"- {_fmt_millis(s['start'])} → {_fmt_millis(s['end'])} ({minutes} min): "
            f"{s['ops']} ops, +{s['ins']:,}/-{s['del']:,} chars"
        )

    large = sorted(stats["large_inserts"], key=lambda x: -len(x[1]))[:5]
    if large:
        lines.append("")
        lines.append(
            f"Largest single insertions (≥{LARGE_INSERT_CHARS} chars in one operation — "
            "often a paste; may be the student's own drafting from elsewhere, or a quote):"
        )
        for millis, text in large:
            snippet = " ".join(text.split())[:70]
            lines.append(f"- {_fmt_millis(millis)}: {len(text):,} chars — \"{snippet}...\"")
    else:
        lines.append("")
        lines.append(f"No single insertion reached {LARGE_INSERT_CHARS} chars — the text was typed incrementally.")

    return "\n".join(lines)


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
