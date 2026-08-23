from __future__ import annotations

import io
import mimetypes
import os
from typing import Optional, Union

import httpx
from docx import Document
from pypdf import PdfReader

from ..client import canvas_paginated, canvas_request
from ..util import get_course_id

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024  # 25MB
MAX_TEXT_CHARS = 60_000


def _cap_text(text: str, name: str) -> str:
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return (
        text[:MAX_TEXT_CHARS]
        + f"\n\n[... truncated: showing first {MAX_TEXT_CHARS} of {len(text)} characters of '{name}']"
    )


async def download_and_extract_text(url: str, content_type: str, name: str) -> str:
    """Download a file from a pre-signed Canvas URL and extract readable text.
    Supports PDF, DOCX, and plain text. Shared by read_course_file and submission-content tools."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        content = response.content

    lower_name = name.lower()

    if content_type == "application/pdf" or lower_name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(content))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(pages_text).strip()
        if not text:
            return f"'{name}': PDF has no extractable text (may be scanned/image-based)."
        return f"'{name}' ({len(reader.pages)} pages):\n\n{_cap_text(text, name)}"

    is_docx = (
        content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or lower_name.endswith(".docx")
    )
    if is_docx:
        doc = Document(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n\n".join(paragraphs).strip()
        if not text:
            return f"'{name}': DOCX has no extractable paragraph text (may be tables/images only)."
        return f"'{name}' ({len(paragraphs)} paragraphs):\n\n{_cap_text(text, name)}"

    if content_type.startswith("text/"):
        return f"'{name}':\n\n{_cap_text(content.decode('utf-8', errors='replace'), name)}"

    return (
        f"'{name}' is type '{content_type}' — text extraction not supported for this type "
        f"(PDF, DOCX, and plain text are). Raw download URL: {url}"
    )


async def list_course_files(course_identifier: Union[str, int]) -> str:
    """List files uploaded to a course."""
    course_id = await get_course_id(course_identifier)
    files = await canvas_paginated(f"/courses/{course_id}/files")
    if not files:
        return "No files found."
    lines = [
        f"- {f.get('display_name')} (ID: {f.get('id')}, {f.get('size', 0)} bytes, "
        f"{'hidden' if f.get('hidden') else 'visible'})"
        for f in files
    ]
    return f"Files in course {course_id}:\n\n" + "\n".join(lines)


async def read_course_file(course_identifier: Union[str, int], file_id: Union[str, int]) -> str:
    """Fetch a course file's content and return extracted text. Supports PDF, DOCX, and plain text.
    Find file_id via list_course_files."""
    course_id = await get_course_id(course_identifier)
    meta = await canvas_request("GET", f"/courses/{course_id}/files/{file_id}")

    size = meta.get("size", 0) or 0
    if size > MAX_DOWNLOAD_BYTES:
        return (
            f"'{meta.get('display_name')}' is {size} bytes, over the "
            f"{MAX_DOWNLOAD_BYTES} byte limit for this tool."
        )

    download_url = meta.get("url")
    if not download_url:
        return f"No download URL available for '{meta.get('display_name')}'."

    return await download_and_extract_text(
        download_url, meta.get("content-type", ""), meta.get("display_name", f"file_{file_id}")
    )


async def upload_course_file(
    course_identifier: Union[str, int], local_file_path: str, folder_path: Optional[str] = None
) -> str:
    """Upload a local file (from this machine) into a course's Files. folder_path is the
    destination folder within the course (e.g. "Week 1"), defaults to the course's root."""
    course_id = await get_course_id(course_identifier)
    if not os.path.isfile(local_file_path):
        return f"No such local file: {local_file_path}"

    name = os.path.basename(local_file_path)
    size = os.path.getsize(local_file_path)
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"

    step1_fields: dict = {"name": name, "size": size, "content_type": content_type}
    if folder_path:
        step1_fields["parent_folder_path"] = folder_path
    step1 = await canvas_request("POST", f"/courses/{course_id}/files", json_body=step1_fields)

    upload_url = step1["upload_url"]
    upload_params = step1["upload_params"]

    with open(local_file_path, "rb") as fh:
        file_bytes = fh.read()

    async with httpx.AsyncClient(follow_redirects=False, timeout=60.0) as client:
        response = await client.post(
            upload_url,
            data=upload_params,
            files={"file": (name, file_bytes, content_type)},
        )
        if response.status_code in (301, 302, 303, 307, 308):
            confirm_url = response.headers["location"]
            confirm = await client.get(confirm_url, headers={"Accept": "application/json"})
            confirm.raise_for_status()
            file_obj = confirm.json()
        else:
            response.raise_for_status()
            file_obj = response.json()

    return f"Uploaded '{file_obj.get('display_name', name)}' (file ID: {file_obj.get('id')}, {size} bytes)."


async def delete_course_file(file_id: Union[str, int]) -> str:
    """Permanently delete a course file. This cannot be undone."""
    result = await canvas_request("DELETE", f"/files/{file_id}")
    return f"Deleted file '{result.get('display_name', file_id)}'."
