import asyncio
import datetime
import io

import pytest
from docx import Document

from canvas_mcp_lite.tools import integrity


def make_docx_bytes():
    doc = Document()
    doc.add_paragraph("An essay about libraries.")
    props = doc.core_properties
    props.author = "Someone Else"
    props.last_modified_by = "Someone Else"
    props.created = datetime.datetime(2026, 8, 20, 10, 0, 0)
    props.modified = datetime.datetime(2026, 8, 20, 10, 4, 0)
    props.revision = 1
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_docx_metadata_extraction():
    meta = integrity._docx_metadata(make_docx_bytes())
    assert meta["author"] == "Someone Else"
    assert meta["revision"] == "1"
    assert meta["created"].startswith("2026-08-20")


def test_format_file_section_empty():
    assert "No embedded metadata" in integrity._format_file_section("a.docx", {})


def test_forensics_report(monkeypatch):
    async def fake_course_id(identifier):
        return 100

    async def fake_request(method, path, params=None, json_body=None, data=None):
        if path.endswith("/assignments/1"):
            return {"name": "Letter to Your Instructor", "due_at": "2026-08-26T03:59:00Z"}
        return {
            "submitted_at": "2026-08-26T03:50:00Z",
            "attempt": 1,
            "late": False,
            "submission_history": [],
            "attachments": [],
        }

    monkeypatch.setattr(integrity, "get_course_id", fake_course_id)
    monkeypatch.setattr(integrity, "canvas_request", fake_request)
    result = asyncio.run(integrity.get_submission_forensics(100, 1, 2))
    assert "Letter to Your Instructor" in result
    assert "No file attachments" in result
    # The caution block must always be present — it is the point of the tool.
    assert "not verdicts" in result
    assert "Google Docs" in result
    assert "false-positive" in result
