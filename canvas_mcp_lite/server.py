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
    grading,
    integrity,
    messaging,
    modules_pages,
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
    google_docs.list_google_doc_comments,
    quizzes.list_quizzes,
    quizzes.get_quiz_details,
    quizzes.list_quiz_submissions,
    grading.list_rubrics,
    grading.get_rubric,
    messaging.list_conversations,
    messaging.get_conversation_details,
    peer_review.list_peer_reviews,
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
    grading.grade_submission,
    grading.bulk_grade_submissions,
    grading.grade_with_rubric,
    grading.create_rubric,
    grading.post_grades,
    grading.hide_grades,
    messaging.send_message,
    peer_review.assign_peer_review,
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
