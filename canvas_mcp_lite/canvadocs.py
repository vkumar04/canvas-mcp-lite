"""Read Canvas DocViewer (Canvadocs) annotations — the highlights, point/area
comments, and text a peer reviewer leaves directly on a submitted PDF/doc,
which do NOT show up in the normal submission-comment thread.

There's no public REST endpoint for these, so we replicate what the browser
does: the submission attachment carries a signed `canvadoc_session` preview URL;
following it redirects to a DocViewer session whose id (a signed JWT) authorizes
reading that document's annotations. Verified working with an instructor token.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .client import CANVAS_API_TOKEN, CANVAS_API_URL


def local_user_id(global_id: Any) -> int | None:
    """Canvadocs reports the Canvas *global* user id (shard-prefixed, e.g.
    73010000000416654). The last 13 digits are the local id used everywhere
    else in the Canvas API (416654)."""
    try:
        return int(global_id) % 10**13
    except (TypeError, ValueError):
        return None


async def fetch_attachment_annotations(preview_url: str) -> list[dict]:
    """Given a submission attachment's `preview_url` (a /api/v1/canvadoc_session
    link), open the DocViewer session and return its raw annotation dicts.
    Returns [] if the attachment isn't a Canvadocs-rendered document."""
    if "canvadoc_session" not in (preview_url or ""):
        return []

    base = CANVAS_API_URL.rsplit("/api/", 1)[0]
    session_url = preview_url if preview_url.startswith("http") else base + preview_url

    # A freshly minted DocViewer session sometimes returns an empty annotation
    # list before it has loaded the document's annotation layer. A new session
    # per attempt (Canvas mints a fresh signed blob each call) plus a short wait
    # reliably surfaces them; genuinely un-annotated docs just stay empty.
    for attempt in range(3):
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"Authorization": f"Bearer {CANVAS_API_TOKEN}"},
        ) as client:
            viewed = await client.get(session_url)
            final = str(viewed.url)
            if "/sessions/" not in final:
                return []
            host = final.split("/sessions/")[0]  # e.g. https://canvadocs.instructure.com/1
            session_id = final.split("/sessions/")[1].split("/")[0].split("?")[0]

            resp = await client.get(f"{host}/sessions/{session_id}/annotations")
            if resp.status_code != 200 or "json" not in resp.headers.get("content-type", ""):
                return []
            data = resp.json()
        annotations = data.get("data", data) if isinstance(data, dict) else data
        annotations = annotations if isinstance(annotations, list) else []
        if annotations or attempt == 2:
            return annotations
        await asyncio.sleep(1.5)
    return []
