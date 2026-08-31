# canvas-mcp-lite

Lean, instructor-focused Canvas LMS MCP server built on FastMCP. ~1,500 lines, flat layout, no test suite.

## Architecture

All source lives in the `canvas_mcp_lite/` package directory:

- `server.py` — FastMCP entry point. Registers every tool from `tools/`. Tools are grouped into `READ_TOOLS`, `WRITE_TOOLS`, and `DELETE_TOOLS` lists — the grouping documents intent (all register identically). **When adding a tool, add it to the correct list.**
- `client.py` — async Canvas API client (httpx). Bearer auth from `.env`, `canvas_request` for single calls, `canvas_paginated` for Link-header pagination (defaults `per_page=100`), retry with exponential backoff on timeouts/connect errors, raises `CanvasAPIError` on non-2xx.
- `util.py` — `get_course_id` resolves a numeric ID or `course_code` string (5-minute TTL cache; do not make it permanent — stale code→ID mappings were a real bug), `format_date`, `announcement_posting_status`.
- `google_client.py` — Google Drive API client for grading Google Docs submissions in place. Auth is a long-lived OAuth **refresh token**: from the env (`GOOGLE_OAUTH_REFRESH_TOKEN`), or a runtime override set by the in-chat connect flow (runtime wins). Tools return the setup message (not an exception) when unconfigured.
- `google_oauth_flow.py` — in-chat account connection for HTTP deployments: the `connect_google_docs` tool mints a single-use state and returns a sign-in link; the public `/oauth/google/callback` route (registered in server.py) exchanges the code, activates the token in-process, and shows it for persisting to the env. States are the security gate — only the tool (behind the secret `MCP_PATH`) can mint one; keep it that way. Requires a **Web application** OAuth client. `google_auth.py` (`canvas-mcp-google-auth`) is the terminal flow for local stdio servers (Desktop-app OAuth client).
- `tools/` — one module per domain: courses, modules_pages, assignments, announcements, discussions, files, quizzes, grading, messaging, peer_review, analytics, integrity, google_docs. `google_docs.comment_on_google_doc` posts real Google Docs comments as the connected account; the Drive API can't anchor comments to a range, so `quoted_text` (shown in the comment card) is the anchor — keep that contract.

## Conventions

- Every tool is an `async def` returning a **formatted human-readable string** (not JSON) — output is read directly by an LLM. Follow the existing `f"ID: ...\nName: ..."` style.
- Course-scoped tools take `course_identifier: Union[str, int]` as the first param and start with `course_id = await get_course_id(course_identifier)`.
- Docstrings ARE the MCP tool descriptions. Encode workflow hints there (e.g. "use get_submission_content before grade_submission"; "peer_reviews=True must be set before assign_peer_review works").
- Safe defaults: create things unpublished (`published=False`); delete-tool docstrings must state the action is permanent.
- File/text extraction goes through `files.download_and_extract_text` (PDF via pypdf, DOCX via python-docx, plain text; 25MB cap). Reuse it rather than downloading directly.

## Packaging (important)

- Packaged as `canvas_mcp_lite` (see `pyproject.toml`) with **relative imports** (`from .tools import ...`) — it runs as an installed package (`canvas-mcp-lite` console script), never `python server.py` directly. Source lives in `canvas_mcp_lite/`; installed editable into `.venv` (`uv pip install --python .venv/bin/python -e .`).
- `.env` at the repo root (with `CANVAS_API_URL`, `CANVAS_API_TOKEN`) is loaded relative to the package location (`client.py` resolves `parent.parent / ".env"`), not cwd — MCP clients launch the server from arbitrary directories. Keep it that way.
