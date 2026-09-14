# canvas-mcp-lite

A lean, instructor-focused [MCP](https://modelcontextprotocol.io) server for Canvas LMS, built with [FastMCP](https://github.com/jlowin/fastmcp). It lets an AI assistant act as a teaching assistant against your Canvas instance: browsing courses, reading student submissions (including PDF/DOCX file uploads), grading with rubrics, posting announcements, managing modules and pages, and more.

## Tools

91 tools across 15 domains, organized by risk level:

| Group | Count | Examples |
|---|---|---|
| Read | 45 | `list_courses`, `list_ungraded_submissions`, `get_submission_content`, `get_submission_forensics`, `get_submission_annotations`, `read_google_slides`, `get_new_quiz_item_analysis` |
| Write | 36 | `create_assignment`, `grade_submission`, `grade_with_rubric`, `comment_on_google_doc`, `update_google_slide_text`, `create_google_slides`, `randomly_assign_peer_reviews`, `upload_course_file` |
| Delete | 10 | `delete_assignment`, `delete_page`, `bulk_delete_announcements`, `delete_google_slide` |

Highlights:

- **Read what students actually submitted** — `get_submission_content` extracts text from uploaded PDFs, DOCX files, and plain text (plus typed text entries, URLs, and discussion submissions) and includes the submission comment thread, so grading tools work from real content.
- **A real grading workflow** — `list_ungraded_submissions` is the grading queue, `grade_submission` handles points, pass/fail, letter, and percent grades (and reports partial success when Canvas saves a comment but rejects a grade), and `post_grades`/`hide_grades` control when students see results.
- **Course codes or IDs** — every course-scoped tool accepts either a numeric course ID or a `course_code` string (resolved with a short-TTL cache).
- **Safe by default** — assignments and pages are created unpublished unless you say otherwise; destructive tools are clearly marked.
- **Peer review, end to end** — assign reviews randomly (`randomly_assign_peer_reviews`, a fair shuffled rotation) or from your own pairings/groups (`assign_peer_reviews_manual`); both only involve students **who submitted their own draft** — no one reviews (or is reviewed) if they didn't turn in their draft, so no one earns reviewer credit without doing their own work. Then `get_submission_annotations` and `summarize_reviewer_annotations` read the highlights and margin comments reviewers leave in Canvas's **DocViewer** (which never appear in the normal comment thread), so you can grade the quality of each student's peer feedback. All with dry-run previews.
- **LLM-friendly output** — every tool returns formatted, readable text rather than raw JSON.
- **Grade Google Docs in place** — when students submit Google Doc links, `read_google_doc` reads the live document and `comment_on_google_doc` leaves feedback as real Google Docs comments from the instructor's account (see below).
- **Read, edit, and build Google Slides decks** — the same connected account can find the instructor's existing lecture decks, read them slide by slide, edit text in place, add/move/duplicate/delete slides, set speaker notes, copy a deck as a template, create a new deck, and comment on student Slides submissions (see below).

## Google Docs and Google Slides (optional)

Students in writing courses often submit a Google Doc link rather than a file — commonly pasted as a submission comment. With a Google account connected, the grading loop becomes: `list_google_doc_links` (one call collects every student's doc link from their submission comments or URL submissions, and flags who hasn't posted one) → `read_google_doc` (read the live draft) → `comment_on_google_doc` (one call per piece of feedback, quoting the passage it refers to) → `grade_submission` (score in Canvas).

### Connecting a Google account

**Remote/HTTP servers — in-chat, no terminal (recommended).** The instructor asks Claude to run `google_docs_status` / `connect_google_docs`, opens the sign-in link it returns in their browser, and approves. The redirect lands on the server's public `/oauth/google/callback` route, which activates commenting immediately and shows a `GOOGLE_OAUTH_REFRESH_TOKEN` value to save in the server's environment (Railway → Variables) so the connection survives restarts. Completing the flow requires a single-use state minted by the tool (which sits behind the secret `MCP_PATH`), so the public callback can't be abused to swap accounts.

One-time **admin** prerequisite at [console.cloud.google.com](https://console.cloud.google.com): create a project, enable the **Google Drive API**, **Google Docs API**, and **Google Slides API**, configure and publish the OAuth consent screen (unpublished "Testing" apps expire refresh tokens after 7 days), create an **OAuth client ID** of type **Web application** with authorized redirect URI `https://<server-domain>/oauth/google/callback`, and set `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` in the server's environment.

**Local stdio servers — terminal.** Create a **Desktop app** OAuth client instead, run `canvas-mcp-google-auth`, and sign in when the browser opens (use `--manual` if the terminal is on a different machine than the browser). It saves all three `GOOGLE_OAUTH_*` values to `.env`.

Notes:

- Comments post **as the connected Google account** — connect the instructor's account, not a bot.
- The instructor's account needs at least Commenter access to each student doc (have students share their docs with the instructor, or use "Anyone with the link — Commenter").
- `comment_on_google_doc` takes a `quoted_text` passage and **pins the comment to that text in the document**, so the student sees the passage highlighted with the note in the margin, exactly like a comment left by hand. Repeated passages take an `occurrence` argument. Anchoring uses the Google Docs API's `insertComment`, which is currently in [Developer Preview](https://developers.google.com/workspace/preview) — if the connected account isn't enrolled, the comment still posts, just unanchored with the passage quoted in the card, and the tool's reply says which happened. Enrolling needs no new consent: the existing `auth/drive` scope covers it. The **Google Docs API** must be enabled in the same Cloud project as the Drive API (APIs & Services → Library), or every quoted comment falls back to unanchored with an HTTP 403 note.
- `list_new_quizzes` / `get_new_quiz_item_analysis` cover **New Quizzes** (the classic `list_quizzes` tools don't). Item analysis asks Canvas to build its item-analysis report, waits for it, and returns the most-missed questions ranked plus per-question correct/incorrect counts, mean, difficulty and discrimination indices, and answer-choice distributions (per prompt for matching items).
- Grading tools (`grade_submission`, `grade_with_rubric`) attach comments to the student's **most recent attempt** and warn in the reply when a student has submitted more than once; `get_submission_content` and `list_ungraded_submissions` flag resubmissions too.
- `get_google_doc_forensics` reports Draftback-style revision-history facts for a shared doc — editing sessions, time span, text typed in small edits vs. added in large single insertions (often pastes), and who edited. Facts only, no AI-detection verdicts; it degrades to a coarse timeline if the detailed changelog is unavailable.
- **Google Slides.** `list_google_slides` finds decks the connected account can see (optionally by title), and `read_google_slides` prints every slide with its title, text boxes, bullet lists, table cells, images, and speaker notes — each tagged with the object ID the edit tools take. Edits happen **in the existing deck**, keeping its theme and formatting: `update_google_slide_text` (one text box or `table:row:col` cell; lines starting with `- ` become bullets), `replace_text_in_google_slides` (deck-wide find-and-replace, e.g. dates or `{{placeholders}}`), `update_google_slide_notes`, `add_google_slide` (layouts: `title_and_body`, `title_only`, `title`, `section_header`, `blank`), `move_google_slide`, `duplicate_google_slide`, and `delete_google_slide`. `copy_google_slides` clones a deck as a starting point and `create_google_slides` builds a new one from a JSON list of slides. Student decks shared with the account work the same way for reading, plus `list_google_slides_comments` / `comment_on_google_slides` (Drive comments — the Slides API can't pin a comment to a text range, so name the slide in the comment). Editing needs Editor access on the deck; the **Google Slides API** must be enabled in the Cloud project.
- The scope requested is full Drive access (`auth/drive`) — the narrower scopes can't comment on files the app didn't create. The token lives only in the server's environment.

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
  google_client.py  Google Drive/Docs/Slides API client (refresh-token auth, file-ID parsing)
  google_auth.py    One-time OAuth setup CLI (canvas-mcp-google-auth)
  util.py        Course code → ID resolution (cached), date formatting
  tools/         One module per domain: courses, assignments, grading,
                 modules_pages, announcements, discussions, files, quizzes,
                 messaging, peer_review, analytics, integrity, google_docs,
                 google_slides, new_quizzes
```

## Notes

- The package name is `canvas_mcp_lite` and it uses relative imports — run it through the installed `canvas-mcp-lite` script, not `python server.py`.
- File downloads for text extraction are capped at 25MB; scanned/image-only PDFs return a notice instead of text.
- Canvas API errors surface with status code and URL; transient timeouts are retried with backoff.
