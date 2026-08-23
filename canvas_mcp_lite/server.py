"""FastMCP entry point for canvas-mcp-lite: lean, instructor-focused Canvas server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import (
    analytics,
    announcements,
    assignments,
    courses,
    discussions,
    files,
    grading,
    messaging,
    modules_pages,
    peer_review,
    quizzes,
)

mcp = FastMCP("canvas-mcp-lite")

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
    announcements.list_announcements,
    discussions.list_discussion_topics,
    discussions.get_discussion_topic_details,
    discussions.list_discussion_entries,
    files.list_course_files,
    files.read_course_file,
    quizzes.list_quizzes,
    quizzes.get_quiz_details,
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
    grading.grade_submission,
    grading.bulk_grade_submissions,
    grading.grade_with_rubric,
    grading.create_rubric,
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
    mcp.run()


if __name__ == "__main__":
    main()
