"""Submission authenticity signals from verifiable document metadata.

Deliberately NOT an "AI detector": no tool can reliably detect AI-written text,
and false accusations harm real students. This reports facts — file creation
and editing timestamps, revision counts, author fields, the authoring
application — for the instructor to weigh."""

from __future__ import annotations

import io
import zipfile
from typing import Optional, Union
from xml.etree import ElementTree

import httpx
from pypdf import PdfReader

from ..client import canvas_request
from ..util import format_date, get_course_id

_CORE_NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
}
_APP_NS = {"ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"}


def _docx_metadata(content: bytes) -> dict:
    meta: dict = {}
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = set(zf.namelist())
        if "docProps/core.xml" in names:
            core = ElementTree.fromstring(zf.read("docProps/core.xml"))
            for key, xpath in [
                ("author", "dc:creator"),
                ("last_modified_by", "cp:lastModifiedBy"),
                ("created", "dcterms:created"),
                ("modified", "dcterms:modified"),
                ("revision", "cp:revision"),
            ]:
                node = core.find(xpath, _CORE_NS)
                if node is not None and node.text:
                    meta[key] = node.text
        if "docProps/app.xml" in names:
            app = ElementTree.fromstring(zf.read("docProps/app.xml"))
            for key, xpath in [
                ("total_editing_minutes", "ep:TotalTime"),
                ("application", "ep:Application"),
                ("words", "ep:Words"),
            ]:
                node = app.find(xpath, _APP_NS)
                if node is not None and node.text:
                    meta[key] = node.text
    return meta


def _pdf_metadata(content: bytes) -> dict:
    meta: dict = {}
    info = PdfReader(io.BytesIO(content)).metadata or {}
    for key, field in [
        ("author", "/Author"),
        ("application", "/Creator"),
        ("producer", "/Producer"),
        ("created", "/CreationDate"),
        ("modified", "/ModDate"),
    ]:
        value = info.get(field)
        if value:
            meta[key] = str(value)
    return meta


def _format_file_section(name: str, meta: dict) -> str:
    if not meta:
        return (
            f"File: {name}\n"
            "  No embedded metadata at all — this is the typical signature of a file "
            "exported from Google Docs (or another online editor), which strips document "
            "properties. Check the submission comments for a shared Google Doc link; its "
            "version history (File > Version history in Google Docs) shows the real "
            "writing process."
        )
    labels = [
        ("author", "Author"),
        ("last_modified_by", "Last modified by"),
        ("application", "Created with"),
        ("producer", "PDF producer"),
        ("created", "File created"),
        ("modified", "File last modified"),
        ("revision", "Revision count"),
        ("total_editing_minutes", "Total editing time (minutes)"),
        ("words", "Word count"),
    ]
    lines = [f"File: {name}"]
    for key, label in labels:
        if key in meta:
            value = meta[key]
            if key in ("created", "modified") and "T" in str(value):
                value = format_date(str(value))
            lines.append(f"  {label}: {value}")
    return "\n".join(lines)


async def get_submission_forensics(
    course_identifier: Union[str, int],
    assignment_id: Union[str, int],
    user_id: Union[str, int],
) -> str:
    """Report verifiable authorship metadata for a student's submitted files (DOCX/PDF):
    creation and modification times, total editing minutes, revision count, author fields,
    and the authoring application, alongside Canvas submission timing. These are
    conversation-starters about process, NOT proof of misconduct — no tool can reliably
    detect AI-written text, so this reports facts and leaves judgment to the instructor."""
    course_id = await get_course_id(course_identifier)
    sub = await canvas_request(
        "GET",
        f"/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}",
        params={"include[]": "submission_history"},
    )
    assignment = await canvas_request("GET", f"/courses/{course_id}/assignments/{assignment_id}")

    lines = [
        f"Submission forensics — assignment '{assignment.get('name')}', user_id={user_id}",
        f"Due: {format_date(assignment.get('due_at'))} | "
        f"Submitted: {format_date(sub.get('submitted_at'))} | "
        f"Attempt: {sub.get('attempt')}"
        f"{' | LATE' if sub.get('late') else ''}",
    ]

    history = sub.get("submission_history") or []
    if len(history) > 1:
        stamps = [format_date(h.get("submitted_at")) for h in history if h.get("submitted_at")]
        lines.append(f"Submission attempts ({len(stamps)}): " + "; ".join(stamps))

    attachments = sub.get("attachments") or []
    if not attachments:
        lines.append(
            "\nNo file attachments on this submission — metadata analysis only applies "
            "to uploaded DOCX/PDF files."
        )
    for att in attachments:
        name = att.get("display_name") or att.get("filename") or "file"
        lower = name.lower()
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as http:
            response = await http.get(att["url"])
            response.raise_for_status()
            content = response.content
        try:
            if lower.endswith(".docx"):
                meta = _docx_metadata(content)
            elif lower.endswith(".pdf"):
                meta = _pdf_metadata(content)
            else:
                lines.append(f"\nFile: {name}\n  Unsupported type for metadata analysis.")
                continue
        except Exception as exc:
            lines.append(f"\nFile: {name}\n  Could not read metadata: {exc}")
            continue
        lines.append("\n" + _format_file_section(name, meta))

    lines.append(
        "\nHow to read this:\n"
        "- These are facts about the FILE, not verdicts about the writing. Use them to "
        "decide whether to have a conversation with the student, never as proof.\n"
        "- A document exported from Google Docs normally shows near-zero editing time, "
        "revision 1, and a creation time near export — that pattern is innocent. Ask the "
        "student to share the Google Doc's version history instead.\n"
        "- Very low editing time on a Word-authored file, an author name that isn't the "
        "student, or creation moments before the deadline are worth asking about — there "
        "are legitimate explanations for each.\n"
        "- No tool (including this one, and including commercial 'AI detectors') can "
        "reliably determine whether text was AI-written. Detector scores have documented "
        "false-positive problems, especially for non-native English writers."
    )
    return "\n".join(lines)
