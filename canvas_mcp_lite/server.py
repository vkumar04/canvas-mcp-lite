"""FastMCP entry point for canvas-mcp-lite: lean, instructor-focused Canvas server."""

from __future__ import annotations

import logging
import os

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse

from .google_oauth_flow import CALLBACK_PATH, handle_callback
from .tools import (
    analytics,
    announcements,
    assignments,
    courses,
    discussions,
    files,
    google_docs,
    google_slides,
    grading,
    integrity,
    messaging,
    modules_pages,
    new_quizzes,
    peer_review,
    quizzes,
)

access_log = logging.getLogger("canvas_mcp_lite.access")


class AccessLogMiddleware(Middleware):
    """One line per tool call (tool name + course), no student data. Goes to
    stderr, so it's visible in `railway logs` and safe in stdio mode."""

    async def on_call_tool(self, context, call_next):
        args = getattr(context.message, "arguments", None) or {}
        course = args.get("course_identifier", "-")
        access_log.info("tool=%s course=%s", context.message.name, course)
        return await call_next(context)


mcp = FastMCP("canvas-mcp-lite")
mcp.add_middleware(AccessLogMiddleware())


# Public (not behind MCP_PATH) — Google must be able to redirect here. Safe
# because completing a flow requires a single-use state minted by the
# connect_google_docs tool, which IS behind the secret path.
@mcp.custom_route(CALLBACK_PATH, methods=["GET"])
async def google_oauth_callback(request: Request) -> HTMLResponse:
    status_code, page = await handle_callback(dict(request.query_params))
    return HTMLResponse(page, status_code=status_code)


_PAGE_STYLE = (
    "<style>body{font-family:system-ui;max-width:44rem;margin:4rem auto;"
    "padding:0 1rem;line-height:1.6}</style>"
)

# Public homepage and privacy policy: Google requires both URLs (on an
# authorized domain) before an OAuth app can be published to production.
@mcp.custom_route("/", methods=["GET"])
async def homepage(request: Request) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Canvas Grading Assistant</title>{_PAGE_STYLE}</head><body>"
        "<h1>Canvas Grading Assistant</h1>"
        "<p>A private teaching-assistant service (an MCP server) used by an "
        "instructor to manage Canvas LMS coursework and provide feedback on "
        "student work, including comments on Google Docs and Google Slides that "
        "students share with the instructor, and editing the instructor's own "
        "Google Slides lecture decks.</p>"
        "<p><a href='/privacy'>Privacy policy</a></p>"
        "</body></html>"
    )


@mcp.custom_route("/privacy", methods=["GET"])
async def privacy_policy(request: Request) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Privacy Policy — Canvas Grading Assistant</title>{_PAGE_STYLE}</head><body>"
        "<h1>Privacy Policy</h1>"
        "<p>Canvas Grading Assistant is a private, single-instructor tool. It is "
        "not a public service and has no users other than the instructor who "
        "operates it.</p>"
        "<h2>Google user data</h2>"
        "<p>With the instructor's explicit authorization via Google sign-in, the "
        "service accesses Google Drive solely to: (1) read the text of documents "
        "and presentations students have shared with the instructor for grading, "
        "(2) post feedback comments on those files on the instructor's behalf, and "
        "(3) read, create, and edit the instructor's own Google Slides decks at "
        "the instructor's request.</p>"
        "<p>The service stores only the OAuth credentials needed to act on the "
        "instructor's behalf. It does not store document contents, sell or share "
        "any data with third parties, use data for advertising, or transfer data "
        "to any service other than Google's and Canvas's own APIs.</p>"
        "<p>Access can be revoked at any time at "
        "<a href='https://myaccount.google.com/permissions'>myaccount.google.com/permissions</a>.</p>"
        "</body></html>"
    )

READ_TOOLS = [
    courses.list_courses,
    courses.get_course_details,
    courses.get_syllabus,
    courses.get_front_page,
    courses.list_users,
    courses.list_sections,
    modules_pages.list_modules,
    modules_pages.get_course_structure,
    modules_pages.list_pages,
    modules_pages.get_page_content,
    assignments.list_assignments,
    assignments.get_assignment_details,
    assignments.list_submissions,
    assignments.get_submission_content,
    assignments.list_ungraded_submissions,
    assignments.list_missing_submissions,
    integrity.get_submission_forensics,
    announcements.list_announcements,
    discussions.list_discussion_topics,
    discussions.get_discussion_topic_details,
    discussions.list_discussion_entries,
    files.list_course_files,
    files.read_course_file,
    google_docs.google_docs_status,
    google_docs.list_google_doc_links,
    google_docs.read_google_doc,
    google_docs.get_google_doc_forensics,
    google_docs.list_google_doc_comments,
    google_slides.list_google_slides,
    google_slides.read_google_slides,
    google_slides.list_google_slides_comments,
    quizzes.list_quizzes,
    quizzes.get_quiz_details,
    quizzes.list_quiz_submissions,
    new_quizzes.list_new_quizzes,
    new_quizzes.get_new_quiz_item_analysis,
    grading.list_rubrics,
    grading.get_rubric,
    messaging.list_conversations,
    messaging.get_conversation_details,
    peer_review.list_peer_reviews,
    peer_review.get_submission_annotations,
    peer_review.summarize_reviewer_annotations,
    analytics.get_assignment_analytics,
    analytics.get_student_analytics,
]

WRITE_TOOLS = [
    announcements.create_announcement,
    announcements.update_announcement,
    discussions.create_discussion_topic,
    discussions.post_discussion_entry,
    discussions.reply_to_discussion_entry,
    modules_pages.create_page,
    modules_pages.edit_page_content,
    modules_pages.update_page_settings,
    modules_pages.create_module,
    modules_pages.update_module,
    modules_pages.add_module_item,
    modules_pages.update_module_item,
    assignments.create_assignment,
    assignments.update_assignment,
    google_docs.connect_google_docs,
    google_docs.comment_on_google_doc,
    google_docs.edit_google_doc_text,
    google_docs.insert_text_in_google_doc,
    google_docs.replace_text_in_google_doc,
    google_slides.create_google_slides,
    google_slides.copy_google_slides,
    google_slides.add_google_slide,
    google_slides.update_google_slide_text,
    google_slides.replace_text_in_google_slides,
    google_slides.update_google_slide_notes,
    google_slides.move_google_slide,
    google_slides.duplicate_google_slide,
    google_slides.comment_on_google_slides,
    grading.grade_submission,
    grading.bulk_grade_submissions,
    grading.grade_with_rubric,
    grading.create_rubric,
    grading.post_grades,
    grading.hide_grades,
    messaging.send_message,
    peer_review.assign_peer_review,
    peer_review.assign_peer_reviews_manual,
    peer_review.randomly_assign_peer_reviews,
    files.upload_course_file,
]

DELETE_TOOLS = [
    modules_pages.delete_page,
    modules_pages.delete_module,
    assignments.delete_assignment,
    announcements.delete_announcement,
    announcements.bulk_delete_announcements,
    discussions.delete_discussion_topic,
    grading.delete_rubric,
    files.delete_course_file,
    peer_review.delete_peer_review,
    google_slides.delete_google_slide,
]

for fn in READ_TOOLS + WRITE_TOOLS + DELETE_TOOLS:
    mcp.tool()(fn)


def main() -> None:
    if not access_log.handlers:
        handler = logging.StreamHandler()  # stderr — never stdout (stdio transport)
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
        access_log.addHandler(handler)
        access_log.setLevel(logging.INFO)

    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport == "http":
        # Remote deployment (e.g. Railway). MCP_PATH should be set to a long
        # random path — it is the only gate on a server that can read/write a
        # Canvas gradebook, since claude.ai custom connectors send no custom
        # headers. Refuse to start wide-open on the default path.
        path = os.environ.get("MCP_PATH", "")
        if not path or path == "/mcp":
            raise RuntimeError(
                "HTTP mode requires MCP_PATH set to a secret path, e.g. "
                "/mcp-<long-random-string> — refusing to serve on a guessable path."
            )
        if not path.startswith("/"):
            path = "/" + path
        mcp.run(
            transport="http",
            host="0.0.0.0",
            port=int(os.environ.get("PORT", "8000")),
            path=path,
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()
