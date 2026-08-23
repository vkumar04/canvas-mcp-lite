# canvas-mcp-lite

A lean, instructor-focused [MCP](https://modelcontextprotocol.io) server for Canvas LMS, built with [FastMCP](https://github.com/jlowin/fastmcp). It lets an AI assistant act as a teaching assistant against your Canvas instance: browsing courses, reading student submissions (including PDF/DOCX file uploads), grading with rubrics, posting announcements, managing modules and pages, and more.

## Tools

64 tools across 11 Canvas domains, organized by risk level:

| Group | Count | Examples |
|---|---|---|
| Read | 32 | `list_courses`, `list_ungraded_submissions`, `list_missing_submissions`, `get_submission_content`, `list_quiz_submissions`, `get_student_analytics` |
| Write | 23 | `create_assignment`, `grade_submission`, `grade_with_rubric`, `post_grades`, `create_announcement`, `send_message`, `upload_course_file` |
| Delete | 9 | `delete_assignment`, `delete_page`, `bulk_delete_announcements` |

Highlights:

- **Read what students actually submitted** — `get_submission_content` extracts text from uploaded PDFs, DOCX files, and plain text (plus typed text entries, URLs, and discussion submissions) and includes the submission comment thread, so grading tools work from real content.
- **A real grading workflow** — `list_ungraded_submissions` is the grading queue, `grade_submission` handles points, pass/fail, letter, and percent grades (and reports partial success when Canvas saves a comment but rejects a grade), and `post_grades`/`hide_grades` control when students see results.
- **Course codes or IDs** — every course-scoped tool accepts either a numeric course ID or a `course_code` string (resolved with a short-TTL cache).
- **Safe by default** — assignments and pages are created unpublished unless you say otherwise; destructive tools are clearly marked.
- **LLM-friendly output** — every tool returns formatted, readable text rather than raw JSON.

## Setup

Requires Python 3.10+.

1. **Install** (editable, from the project root):

   ```sh
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

2. **Configure credentials** in a `.env` file next to the package:

   ```sh
   CANVAS_API_URL=https://yourschool.instructure.com/api/v1
   CANVAS_API_TOKEN=your-canvas-access-token
   ```

   Generate a token in Canvas under **Account → Settings → New Access Token**. The `.env` is loaded relative to the package location (not the working directory), because MCP clients launch servers from arbitrary directories.

3. **Run** via the console script:

   ```sh
   canvas-mcp-lite
   ```

## Using with Claude

Register it as an stdio MCP server. For Claude Code:

```sh
claude mcp add canvas -- /path/to/.venv/bin/canvas-mcp-lite
```

Or in a Claude Desktop / MCP client config:

```json
{
  "mcpServers": {
    "canvas": {
      "command": "/path/to/.venv/bin/canvas-mcp-lite"
    }
  }
}
```

## Project layout

```
canvas_mcp_lite/
  server.py      FastMCP entry point; registers READ/WRITE/DELETE tool lists
  client.py      Async Canvas API client: auth, pagination, retry, errors
  util.py        Course code → ID resolution (cached), date formatting
  tools/         One module per domain: courses, assignments, grading,
                 modules_pages, announcements, discussions, files, quizzes,
                 messaging, peer_review, analytics
```

## Notes

- The package name is `canvas_mcp_lite` and it uses relative imports — run it through the installed `canvas-mcp-lite` script, not `python server.py`.
- File downloads for text extraction are capped at 25MB; scanned/image-only PDFs return a notice instead of text.
- Canvas API errors surface with status code and URL; transient timeouts are retried with backoff.
