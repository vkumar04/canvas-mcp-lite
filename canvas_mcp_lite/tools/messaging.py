from __future__ import annotations

from typing import Optional, Union

from ..client import canvas_paginated, canvas_request
from ..util import format_date


async def list_conversations(scope: str = "inbox") -> str:
    """List your Canvas Inbox conversations. scope: inbox, unread, starred, sent, archived."""
    conversations = await canvas_paginated("/conversations", {"scope": scope})
    if not conversations:
        return f"No conversations in scope '{scope}'."
    lines = []
    for c in conversations:
        participants = ", ".join(p.get("name", "?") for p in c.get("participants", []) if p)
        lines.append(
            f"ID: {c.get('id')}\n"
            f"Subject: {c.get('subject') or '(no subject)'}\n"
            f"Participants: {participants}\n"
            f"Last message: {c.get('last_message')}\n"
            f"Updated: {format_date(c.get('last_message_at'))}"
        )
    return f"Conversations ({scope}):\n\n" + "\n\n".join(lines)


async def get_conversation_details(conversation_id: Union[str, int]) -> str:
    """Get the full message thread for one conversation."""
    c = await canvas_request("GET", f"/conversations/{conversation_id}")
    lines = [f"Subject: {c.get('subject') or '(no subject)'}\n"]
    for m in c.get("messages", []):
        lines.append(f"[{format_date(m.get('created_at'))}] {m.get('author_id')}: {m.get('body')}")
    return "\n".join(lines)


async def send_message(
    recipient_user_ids: str,
    body: str,
    subject: Optional[str] = None,
    course_identifier: Optional[Union[str, int]] = None,
    group_conversation: bool = False,
) -> str:
    """Send a Canvas Inbox message to one or more users.
    recipient_user_ids is a comma-separated list of Canvas user IDs (get IDs from list_users).
    Set group_conversation=True to send one message all recipients can see/reply to together;
    otherwise each recipient gets a separate 1:1 conversation."""
    recipients = [r.strip() for r in recipient_user_ids.split(",") if r.strip()]
    if not recipients:
        return "No recipients provided."
    payload: dict = {
        "recipients": recipients,
        "body": body,
        "group_conversation": group_conversation,
    }
    if subject:
        payload["subject"] = subject
    if course_identifier is not None:
        from ..util import get_course_id

        course_id = await get_course_id(course_identifier)
        payload["context_code"] = f"course_{course_id}"
    result = await canvas_request("POST", "/conversations", json_body=payload)
    count = len(result) if isinstance(result, list) else 1
    return f"Sent message to {len(recipients)} recipient(s), created {count} conversation(s)."
