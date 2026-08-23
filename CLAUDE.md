# canvas-mcp-lite

Lean, instructor-focused Canvas LMS MCP server built on FastMCP. ~1,500 lines, flat layout, no test suite.

## Architecture

All source lives in the `canvas_mcp_lite/` package directory:

- `server.py` — FastMCP entry point. Registers every tool from `tools/`. Tools are grouped into `READ_TOOLS`, `WRITE_TOOLS`, and `DELETE_TOOLS` lists — the grouping documents intent (all register identically). **When adding a tool, add it to the correct list.**
- `client.py` — async Canvas API client (httpx). Bearer auth from `.env`, `canvas_request` for single calls, `canvas_paginated` for Link-header pagination (defaults `per_page=100`), retry with exponential backoff on timeouts/connect errors, raises `CanvasAPIError` on non-2xx.
- `util.py` — `get_course_id` resolves a numeric ID or `course_code` string (5-minute TTL cache; do not make it permanent — stale code→ID mappings were a real bug), `format_date`, `announcement_posting_status`.
- `tools/` — one module per Canvas domain: courses, modules_pages, assignments, announcements, discussions, files, quizzes, grading, messaging, peer_review, analytics.

## Conventions

- Every tool is an `async def` returning a **formatted human-readable string** (not JSON) — output is read directly by an LLM. Follow the existing `f"ID: ...\nName: ..."` style.
- Course-scoped tools take `course_identifier: Union[str, int]` as the first param and start with `course_id = await get_course_id(course_identifier)`.
- Docstrings ARE the MCP tool descriptions. Encode workflow hints there (e.g. "use get_submission_content before grade_submission"; "peer_reviews=True must be set before assign_peer_review works").
- Safe defaults: create things unpublished (`published=False`); delete-tool docstrings must state the action is permanent.
- File/text extraction goes through `files.download_and_extract_text` (PDF via pypdf, DOCX via python-docx, plain text; 25MB cap). Reuse it rather than downloading directly.

## Packaging (important)

- Packaged as `canvas_mcp_lite` (see `pyproject.toml`) with **relative imports** (`from .tools import ...`) — it runs as an installed package (`canvas-mcp-lite` console script), never `python server.py` directly. Source lives in `canvas_mcp_lite/`; installed editable into `.venv` (`uv pip install --python .venv/bin/python -e .`).
- `.env` at the repo root (with `CANVAS_API_URL`, `CANVAS_API_TOKEN`) is loaded relative to the package location (`client.py` resolves `parent.parent / ".env"`), not cwd — MCP clients launch the server from arbitrary directories. Keep it that way.
