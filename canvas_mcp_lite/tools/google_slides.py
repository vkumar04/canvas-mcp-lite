"""Google Slides: read, create, and edit presentations in place through the
Slides API, using the same connected instructor account as the Google Docs
tools. Covers both the instructor's own lecture decks (find, read, edit, build
new ones) and student Slides submissions (read, comment).

Editing model: read_google_slides prints a stable object ID next to every
slide, text box, and table cell. The edit tools take those IDs and send
Slides API batchUpdate requests, so changes land in the existing deck — no
regenerating, no copies unless copy_google_slides is asked for."""

from __future__ import annotations

import json
import uuid
from typing import Optional

from ..google_client import (
    GOOGLE_SLIDES_API,
    GoogleAPIError,
    GoogleConfigError,
    connected_account_email,
    extract_doc_id,
    google_request,
)
from ..util import format_date
from .files import _cap_text

GOOGLE_SLIDES_MIME = "application/vnd.google-apps.presentation"

# Predefined layouts and the placeholder types each one exposes, so
# add_google_slide can map text onto the right placeholders. Slides rejects a
# placeholderIdMappings entry that names a placeholder the layout doesn't have.
_LAYOUTS: dict[str, tuple[str, Optional[str], Optional[str]]] = {
    # name: (predefinedLayout, title placeholder type, body placeholder type)
    "title_and_body": ("TITLE_AND_BODY", "TITLE", "BODY"),
    "title_only": ("TITLE_ONLY", "TITLE", None),
    "title": ("TITLE", "CENTERED_TITLE", "SUBTITLE"),
    "section_header": ("SECTION_HEADER", "TITLE", None),
    "blank": ("BLANK", None, None),
}

_BULLET_MARKERS = ("- ", "* ", "• ")


def _new_object_id(prefix: str) -> str:
    # Slides object IDs: 5–50 chars of [a-zA-Z0-9_-], first char alphanumeric or _.
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


async def _explain_api_error(exc: GoogleAPIError, presentation_id: str) -> str:
    if exc.status_code in (403, 404):
        email = await connected_account_email()
        acting_as = f" ({email})" if email else ""
        return (
            f"Can't access Google Slides deck {presentation_id} (HTTP {exc.status_code}). "
            f"The connected instructor Google account{acting_as} most likely doesn't "
            "have access to this presentation, or the Google Slides API isn't "
            "enabled in the Cloud project. For a student's deck, ask them to share "
            "it with that account (Commenter to comment, Editor to edit); for the "
            "instructor's own decks, check that the Slides API is enabled at "
            "console.cloud.google.com → APIs & Services → Library."
        )
    return str(exc)


def _extract_id(url_or_id: str) -> str:
    try:
        return extract_doc_id(url_or_id)
    except ValueError:
        raise ValueError(
            f"Couldn't find a Google Slides ID in {url_or_id!r}. Expected a link like "
            "https://docs.google.com/presentation/d/<id>/edit, or a bare file ID."
        )


async def _get_presentation(presentation_id: str) -> dict:
    return await google_request("GET", f"{GOOGLE_SLIDES_API}/presentations/{presentation_id}")


async def _batch_update(presentation_id: str, requests: list[dict]) -> dict:
    return await google_request(
        "POST",
        f"{GOOGLE_SLIDES_API}/presentations/{presentation_id}:batchUpdate",
        json_body={"requests": requests},
    )


# --- Reading -----------------------------------------------------------------


def _paragraphs(text: Optional[dict]) -> list[tuple[int, bool, str]]:
    """(nesting_level, bulleted, text) for each paragraph of a Slides text body."""
    result: list[tuple[int, bool, str]] = []
    level, bulleted, buffer = 0, False, []
    for element in (text or {}).get("textElements") or []:
        marker = element.get("paragraphMarker")
        if marker is not None:
            if buffer:
                result.append((level, bulleted, "".join(buffer)))
            buffer = []
            bullet = marker.get("bullet")
            bulleted = bullet is not None
            level = (bullet or {}).get("nestingLevel", 0) or 0
            continue
        run = element.get("textRun")
        if run:
            buffer.append(run.get("content", ""))
        elif element.get("autoText"):
            buffer.append(element["autoText"].get("content", ""))
    if buffer:
        result.append((level, bulleted, "".join(buffer)))
    return [(lvl, bul, txt.rstrip("\n")) for lvl, bul, txt in result if txt.rstrip("\n")]


def _plain_text(text: Optional[dict]) -> str:
    return "\n".join(t for _, _, t in _paragraphs(text))


def _format_paragraphs(text: Optional[dict], indent: str) -> list[str]:
    lines = []
    for level, bulleted, txt in _paragraphs(text):
        prefix = ("  " * level + "- ") if bulleted else ""
        lines.append(f"{indent}{prefix}{txt}")
    return lines


def _shape_label(element: dict) -> str:
    shape = element.get("shape") or {}
    placeholder = shape.get("placeholder") or {}
    ptype = placeholder.get("type")
    if ptype:
        return ptype.lower().replace("_", " ")
    shape_type = shape.get("shapeType", "shape")
    return "text box" if shape_type == "TEXT_BOX" else shape_type.lower().replace("_", " ")


def _describe_element(element: dict, lines: list[str]) -> None:
    object_id = element.get("objectId", "?")
    if "shape" in element:
        text = (element["shape"] or {}).get("text")
        label = _shape_label(element)
        if _paragraphs(text):
            lines.append(f"  [{label} id={object_id}]")
            lines.extend(_format_paragraphs(text, "    "))
        else:
            lines.append(f"  [{label} id={object_id}] (empty)")
    elif "table" in element:
        table = element["table"]
        rows = table.get("rows", 0)
        cols = table.get("columns", 0)
        lines.append(f"  [table id={object_id}, {rows}x{cols} — edit a cell with object_id '{object_id}:<row>:<col>' (0-based)]")
        for r, row in enumerate(table.get("tableRows") or []):
            cells = []
            for c, cell in enumerate(row.get("tableCells") or []):
                cells.append(f"[{r}:{c}] {_plain_text(cell.get('text')) or '(empty)'}")
            lines.append("    " + " | ".join(cells))
    elif "image" in element:
        desc = element.get("description") or element.get("title") or ""
        lines.append(f"  [image id={object_id}]" + (f" {desc}" if desc else ""))
    elif "video" in element:
        lines.append(f"  [video id={object_id}] {(element['video'] or {}).get('url', '')}")
    elif "elementGroup" in element:
        lines.append(f"  [group id={object_id}]")
        for child in (element["elementGroup"] or {}).get("children") or []:
            _describe_element(child, lines)
    elif "sheetsChart" in element:
        lines.append(f"  [chart id={object_id}]")
    elif "line" in element:
        pass  # decorative; not worth a line of output
    elif "wordArt" in element:
        lines.append(f"  [word art id={object_id}] {(element['wordArt'] or {}).get('renderedText', '')}")


def _speaker_notes(slide: dict) -> tuple[Optional[str], str]:
    """(notes_shape_object_id, notes_text) for a slide."""
    notes_page = (slide.get("slideProperties") or {}).get("notesPage") or {}
    notes_id = (notes_page.get("notesProperties") or {}).get("speakerNotesObjectId")
    text = ""
    if notes_id:
        for element in notes_page.get("pageElements") or []:
            if element.get("objectId") == notes_id:
                text = _plain_text((element.get("shape") or {}).get("text"))
    return notes_id, text


def _layout_names(presentation: dict) -> dict[str, str]:
    return {
        layout.get("objectId"): (layout.get("layoutProperties") or {}).get("displayName", "")
        for layout in presentation.get("layouts") or []
    }


def format_presentation(presentation: dict) -> str:
    """Human-readable dump of a presentation with the object IDs the edit tools need."""
    pid = presentation.get("presentationId", "?")
    title = presentation.get("title", pid)
    slides = presentation.get("slides") or []
    layouts = _layout_names(presentation)
    header = [f"Google Slides: {title} (presentation_id: {pid}) — {len(slides)} slide(s)"]

    blocks = []
    for n, slide in enumerate(slides, start=1):
        props = slide.get("slideProperties") or {}
        layout = layouts.get(props.get("layoutObjectId"), "")
        skipped = " [SKIPPED]" if props.get("isSkipped") else ""
        lines = [f"Slide {n} (slide_id: {slide.get('objectId')}){skipped}" + (f" — layout: {layout}" if layout else "")]
        for element in slide.get("pageElements") or []:
            _describe_element(element, lines)
        _, notes = _speaker_notes(slide)
        if notes:
            lines.append("  Speaker notes:")
            lines.extend(f"    {line}" for line in notes.splitlines())
        blocks.append("\n".join(lines))

    body = "\n\n".join(blocks) if blocks else "(no slides)"
    return "\n".join(header) + "\n\n" + _cap_text(body, title)


async def list_google_slides(query: str = "", limit: int = 20) -> str:
    """Find Google Slides presentations the connected instructor account can
    see, newest-modified first — the instructor's own lecture decks as well as
    decks shared with them. Optional query matches words in the deck title. Use
    this to locate a deck the instructor already made, then read_google_slides
    with its presentation_id."""
    q = f"mimeType='{GOOGLE_SLIDES_MIME}' and trashed=false"
    if query.strip():
        safe = query.strip().replace("\\", "\\\\").replace("'", "\\'")
        q += f" and name contains '{safe}'"
    try:
        result = await google_request(
            "GET",
            "/files",
            params={
                "q": q,
                "orderBy": "modifiedTime desc",
                "pageSize": max(1, min(int(limit), 100)),
                "fields": "files(id,name,modifiedTime,owners(displayName),webViewLink),nextPageToken",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "corpora": "allDrives",
            },
        )
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, "(search)")

    files = result.get("files") or []
    if not files:
        return "No Google Slides presentations found" + (f" matching '{query}'." if query.strip() else ".")
    lines = [f"Google Slides presentations ({len(files)}{'+' if result.get('nextPageToken') else ''}):"]
    for f in files:
        owner = ", ".join(o.get("displayName", "?") for o in f.get("owners") or []) or "?"
        lines.append(
            f"- {f.get('name')} (presentation_id: {f.get('id')}) — modified "
            f"{format_date(f.get('modifiedTime'))}, owner: {owner}"
        )
    return "\n".join(lines)


async def read_google_slides(presentation_url: str) -> str:
    """Read a Google Slides deck slide by slide: every title, text box, bullet
    list, table cell, image, and speaker note, each tagged with the object ID
    the edit tools need. Works for the instructor's own decks and for student
    decks shared with the connected account (links come from
    list_google_doc_links, which also catches Slides links). Read the CURRENT
    version — always run this before editing so object IDs are fresh."""
    try:
        presentation_id = _extract_id(presentation_url)
    except ValueError as exc:
        return str(exc)
    try:
        presentation = await _get_presentation(presentation_id)
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, presentation_id)
    return format_presentation(presentation)


# --- Editing existing decks ---------------------------------------------------


def _split_bullets(text: str) -> tuple[str, bool]:
    """Strip leading '- ' markers; report whether the block should be bulleted.
    A block where every non-blank line carries a marker becomes a bullet list."""
    lines = text.splitlines()
    non_blank = [line for line in lines if line.strip()]
    if not non_blank:
        return text, False
    if all(line.lstrip().startswith(_BULLET_MARKERS) for line in non_blank):
        stripped = []
        for line in lines:
            body = line.lstrip()
            if body.startswith(_BULLET_MARKERS):
                indent = line[: len(line) - len(body)]
                stripped.append(indent.replace("  ", "\t") + body[2:])
            else:
                stripped.append(line)
        return "\n".join(stripped), True
    return text, False


def _parse_cell_ref(object_id: str) -> tuple[str, Optional[dict]]:
    """'tableId:row:col' → (tableId, cellLocation); plain IDs pass through."""
    parts = object_id.split(":")
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        return parts[0], {"rowIndex": int(parts[1]), "columnIndex": int(parts[2])}
    return object_id, None


def _find_element(presentation: dict, object_id: str) -> Optional[dict]:
    """Page element by ID across slides (descending into groups) plus speaker-notes shapes."""

    def walk(elements):
        for element in elements or []:
            if element.get("objectId") == object_id:
                return element
            found = walk((element.get("elementGroup") or {}).get("children"))
            if found:
                return found
        return None

    for slide in presentation.get("slides") or []:
        found = walk(slide.get("pageElements"))
        if found:
            return found
        notes_page = (slide.get("slideProperties") or {}).get("notesPage") or {}
        found = walk(notes_page.get("pageElements"))
        if found:
            return found
    return None


def _element_has_text(element: Optional[dict], cell: Optional[dict]) -> bool:
    if not element:
        return False
    if cell is not None:
        rows = (element.get("table") or {}).get("tableRows") or []
        try:
            cell_obj = rows[cell["rowIndex"]]["tableCells"][cell["columnIndex"]]
        except (IndexError, KeyError):
            return False
        return bool(_paragraphs(cell_obj.get("text")))
    return bool(_paragraphs((element.get("shape") or {}).get("text")))


def _set_text_requests(
    object_id: str, new_text: str, *, has_text: bool, cell: Optional[dict] = None
) -> list[dict]:
    """batchUpdate requests that replace all text in a shape or table cell.
    Deleting an already-empty shape's text is an API error, so the delete is
    conditional. Lines that all start with '- ' become a real bullet list."""
    text, bulleted = _split_bullets(new_text)
    target: dict = {"objectId": object_id}
    if cell is not None:
        target["cellLocation"] = cell
    requests: list[dict] = []
    if has_text:
        requests.append({"deleteText": {**target, "textRange": {"type": "ALL"}}})
    if text:
        requests.append({"insertText": {**target, "text": text, "insertionIndex": 0}})
        if bulleted:
            requests.append(
                {
                    "createParagraphBullets": {
                        **target,
                        "textRange": {"type": "ALL"},
                        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    }
                }
            )
    return requests


async def update_google_slide_text(presentation_url: str, object_id: str, new_text: str) -> str:
    """Replace the text of one text box, title, or table cell in an existing
    Google Slides deck — the deck is edited in place, not copied. Get object_id
    from read_google_slides (for a table cell use 'tableId:row:col'). Lines
    that all start with '- ' become a bullet list; otherwise the text keeps the
    box's existing formatting. Keep replacement text about the same length as
    the original so it still fits the box. Empty new_text clears the box.
    Requires Editor access on the deck."""
    try:
        presentation_id = _extract_id(presentation_url)
    except ValueError as exc:
        return str(exc)
    target_id, cell = _parse_cell_ref(object_id.strip())
    try:
        presentation = await _get_presentation(presentation_id)
        element = _find_element(presentation, target_id)
        if element is None:
            return (
                f"No element with id '{target_id}' in this deck. Re-run read_google_slides — "
                "the deck may have changed since it was last read."
            )
        if cell is not None and "table" not in element:
            return f"'{target_id}' is not a table, so '{object_id}' isn't a valid cell reference."
        if cell is None and "shape" not in element:
            return f"'{target_id}' is not a text element (it's an image, table, or group) — can't set its text."
        requests = _set_text_requests(
            target_id, new_text, has_text=_element_has_text(element, cell), cell=cell
        )
        if not requests:
            return f"'{object_id}' is already empty — nothing to do."
        await _batch_update(presentation_id, requests)
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, presentation_id)

    preview = new_text.strip().replace("\n", " / ")
    if len(preview) > 80:
        preview = preview[:77] + "..."
    return f"Updated text of '{object_id}' in deck {presentation_id}: {preview or '(cleared)'}"


async def replace_text_in_google_slides(
    presentation_url: str, find: str, replace: str, match_case: bool = True, slide_ids: str = ""
) -> str:
    """Find-and-replace text across a whole Google Slides deck (or only the
    comma-separated slide_ids given) — e.g. fix a date, rename a term, or fill
    in {{placeholders}} in a copied template. Edits the deck in place and keeps
    formatting. Reports how many occurrences changed."""
    try:
        presentation_id = _extract_id(presentation_url)
    except ValueError as exc:
        return str(exc)
    if not find:
        return "The find text is empty — nothing replaced."
    request: dict = {
        "replaceAllText": {
            "containsText": {"text": find, "matchCase": bool(match_case)},
            "replaceText": replace,
        }
    }
    ids = [s.strip() for s in slide_ids.split(",") if s.strip()]
    if ids:
        request["replaceAllText"]["pageObjectIds"] = ids
    try:
        result = await _batch_update(presentation_id, [request])
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, presentation_id)
    replies = (result or {}).get("replies") or [{}]
    count = (replies[0].get("replaceAllText") or {}).get("occurrencesChanged", 0)
    scope = f" on {len(ids)} slide(s)" if ids else ""
    return f"Replaced {count} occurrence(s) of '{find}' with '{replace}' in deck {presentation_id}{scope}."


async def update_google_slide_notes(presentation_url: str, slide_id: str, notes: str) -> str:
    """Set the speaker notes of one slide (replacing any existing notes).
    slide_id comes from read_google_slides. Pass empty notes to clear them."""
    try:
        presentation_id = _extract_id(presentation_url)
    except ValueError as exc:
        return str(exc)
    try:
        presentation = await _get_presentation(presentation_id)
        slide = next((s for s in presentation.get("slides") or [] if s.get("objectId") == slide_id), None)
        if slide is None:
            return f"No slide with id '{slide_id}' in this deck. Re-run read_google_slides."
        notes_id, existing = _speaker_notes(slide)
        if not notes_id:
            return f"Slide '{slide_id}' has no speaker-notes shape — Google Slides didn't expose one."
        requests = _set_text_requests(notes_id, notes, has_text=bool(existing))
        if not requests:
            return f"Slide '{slide_id}' already has no notes — nothing to do."
        await _batch_update(presentation_id, requests)
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, presentation_id)
    return f"Updated speaker notes on slide '{slide_id}' in deck {presentation_id}."


def _add_slide_requests(
    title: str, body: str, notes: str, layout: str, position: Optional[int]
) -> tuple[list[dict], str]:
    """(requests, new_slide_id). Text goes into the layout's placeholders, whose
    IDs we choose up front via placeholderIdMappings so the inserts can be part
    of the same batch. Speaker notes need the notes shape ID, which only exists
    after creation, so the caller handles notes in a second call."""
    layout_key = (layout or "title_and_body").strip().lower()
    if layout_key not in _LAYOUTS:
        raise ValueError(f"Unknown layout '{layout}'. Choose one of: {', '.join(_LAYOUTS)}.")
    predefined, title_type, body_type = _LAYOUTS[layout_key]
    slide_id = _new_object_id("slide")
    title_id = _new_object_id("title")
    body_id = _new_object_id("body")

    mappings = []
    if title_type:
        mappings.append({"layoutPlaceholder": {"type": title_type, "index": 0}, "objectId": title_id})
    if body_type:
        mappings.append({"layoutPlaceholder": {"type": body_type, "index": 0}, "objectId": body_id})

    create: dict = {
        "objectId": slide_id,
        "slideLayoutReference": {"predefinedLayout": predefined},
        "placeholderIdMappings": mappings,
    }
    if position is not None:
        create["insertionIndex"] = max(0, int(position) - 1)
    requests: list[dict] = [{"createSlide": create}]
    if title_type and title.strip():
        requests.append({"insertText": {"objectId": title_id, "text": title.strip(), "insertionIndex": 0}})
    if body_type and body.strip():
        requests.extend(_set_text_requests(body_id, body.strip(), has_text=False))
    return requests, slide_id


async def _set_notes_after_create(presentation_id: str, slide_id: str, notes: str) -> None:
    if not notes.strip():
        return
    presentation = await _get_presentation(presentation_id)
    slide = next((s for s in presentation.get("slides") or [] if s.get("objectId") == slide_id), None)
    notes_id, _ = _speaker_notes(slide or {})
    if notes_id:
        await _batch_update(presentation_id, _set_text_requests(notes_id, notes.strip(), has_text=False))


async def add_google_slide(
    presentation_url: str,
    title: str,
    body: str = "",
    notes: str = "",
    layout: str = "title_and_body",
    position: Optional[int] = None,
) -> str:
    """Add one slide to an existing Google Slides deck, using the deck's own
    theme. layout: title_and_body (default; body lines starting with '- '
    become bullets), title_only, title (title + subtitle in body), section_header,
    or blank. position is the 1-based slot for the new slide (default: end).
    Call once per slide; use create_google_slides for a whole new deck."""
    try:
        presentation_id = _extract_id(presentation_url)
        requests, slide_id = _add_slide_requests(title, body, notes, layout, position)
    except ValueError as exc:
        return str(exc)
    try:
        await _batch_update(presentation_id, requests)
        await _set_notes_after_create(presentation_id, slide_id, notes)
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, presentation_id)
    where = f"at position {position}" if position else "at the end"
    return f"Added slide '{title.strip() or '(untitled)'}' (slide_id: {slide_id}) {where} of deck {presentation_id}."


async def move_google_slide(presentation_url: str, slide_id: str, position: int) -> str:
    """Move a slide to a new 1-based position in the deck (1 = first). Get
    slide_id from read_google_slides."""
    try:
        presentation_id = _extract_id(presentation_url)
    except ValueError as exc:
        return str(exc)
    try:
        presentation = await _get_presentation(presentation_id)
        ids = [s.get("objectId") for s in presentation.get("slides") or []]
        if slide_id not in ids:
            return f"No slide with id '{slide_id}' in this deck. Re-run read_google_slides."
        target = max(1, min(int(position), len(ids)))
        current = ids.index(slide_id) + 1
        if target == current:
            return f"Slide '{slide_id}' is already at position {target}."
        # updateSlidesPosition's insertionIndex is evaluated after the moved
        # slide is removed, so moving later needs +1 to land on the visible slot.
        insertion = target if target > current else target - 1
        await _batch_update(
            presentation_id,
            [{"updateSlidesPosition": {"slideObjectIds": [slide_id], "insertionIndex": insertion}}],
        )
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, presentation_id)
    return f"Moved slide '{slide_id}' from position {current} to {target} in deck {presentation_id}."


async def duplicate_google_slide(presentation_url: str, slide_id: str) -> str:
    """Duplicate a slide (the copy appears right after the original, with the
    same content and formatting) — handy for repeating a well-designed slide
    before editing the copy's text with update_google_slide_text."""
    try:
        presentation_id = _extract_id(presentation_url)
    except ValueError as exc:
        return str(exc)
    new_id = _new_object_id("slide")
    try:
        await _batch_update(
            presentation_id, [{"duplicateObject": {"objectId": slide_id, "objectIds": {slide_id: new_id}}}]
        )
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, presentation_id)
    return (
        f"Duplicated slide '{slide_id}' → new slide_id {new_id} (placed right after it) in deck "
        f"{presentation_id}. Run read_google_slides to get the copy's element IDs before editing it."
    )


async def delete_google_slide(presentation_url: str, slide_id: str) -> str:
    """Permanently remove one slide from a Google Slides deck (recoverable only
    through the deck's version history in the Slides UI). Confirm the slide
    with read_google_slides first."""
    try:
        presentation_id = _extract_id(presentation_url)
    except ValueError as exc:
        return str(exc)
    try:
        await _batch_update(presentation_id, [{"deleteObject": {"objectId": slide_id}}])
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, presentation_id)
    return f"Deleted slide '{slide_id}' from deck {presentation_id}."


# --- Creating decks -----------------------------------------------------------


async def create_google_slides(title: str, slides_json: str = "[]") -> str:
    """Create a new Google Slides deck in the connected instructor's Drive and
    fill it with slides. slides_json is a JSON array string; each item:
    {"title": str, "body": str (optional; lines starting with '- ' become
    bullets), "notes": str (optional speaker notes), "layout": str (optional:
    title_and_body, title_only, title, section_header, blank)}. The first slide
    usually wants layout "title" (title + subtitle). Creates the deck with the
    default theme; to reuse the look of an existing deck, copy_google_slides
    that deck instead and edit the copy. Returns the deck link."""
    if not title.strip():
        return "Deck title is empty — nothing created."
    try:
        specs = json.loads(slides_json or "[]")
        if not isinstance(specs, list):
            raise ValueError("expected a JSON array")
        prepared: list[tuple[list[dict], str, str]] = []
        for i, spec in enumerate(specs):
            if not isinstance(spec, dict):
                raise ValueError(f"slide {i + 1} is not an object")
            requests, slide_id = _add_slide_requests(
                str(spec.get("title", "")),
                str(spec.get("body", "")),
                str(spec.get("notes", "")),
                str(spec.get("layout", "title_and_body")),
                None,
            )
            prepared.append((requests, slide_id, str(spec.get("notes", ""))))
    except (ValueError, json.JSONDecodeError) as exc:
        return f"Invalid slides_json: {exc}"

    try:
        created = await google_request(
            "POST", f"{GOOGLE_SLIDES_API}/presentations", json_body={"title": title.strip()}
        )
        presentation_id = created["presentationId"]
        # A fresh deck comes with one blank title slide; replace it with ours.
        default_slide_ids = [s.get("objectId") for s in created.get("slides") or []]
        requests: list[dict] = []
        for reqs, _, _ in prepared:
            requests.extend(reqs)
        if prepared and default_slide_ids:
            requests.extend({"deleteObject": {"objectId": sid}} for sid in default_slide_ids)
        if requests:
            await _batch_update(presentation_id, requests)
        for _, slide_id, notes in prepared:
            await _set_notes_after_create(presentation_id, slide_id, notes)
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, "(new deck)")

    return (
        f"Created Google Slides deck '{title.strip()}' with {len(prepared)} slide(s).\n"
        f"presentation_id: {presentation_id}\n"
        f"Link: https://docs.google.com/presentation/d/{presentation_id}/edit"
    )


async def copy_google_slides(presentation_url: str, new_title: str) -> str:
    """Make a copy of an existing Google Slides deck (theme, layouts, and all
    slides) under a new title in the connected instructor's Drive. Use this to
    build a new lecture from an old one, or to adapt a deck without touching
    the original; then edit the copy with the update/add/delete slide tools."""
    try:
        presentation_id = _extract_id(presentation_url)
    except ValueError as exc:
        return str(exc)
    if not new_title.strip():
        return "new_title is empty — nothing copied."
    try:
        copied = await google_request(
            "POST",
            f"/files/{presentation_id}/copy",
            params={"fields": "id,name", "supportsAllDrives": "true"},
            json_body={"name": new_title.strip()},
        )
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, presentation_id)
    new_id = copied.get("id")
    return (
        f"Copied deck {presentation_id} → '{copied.get('name')}' (presentation_id: {new_id}).\n"
        f"Link: https://docs.google.com/presentation/d/{new_id}/edit\n"
        "Run read_google_slides on the copy to get its slide and element IDs."
    )


# --- Commenting (student decks) ------------------------------------------------

_COMMENT_FIELDS = (
    "id,content,author(displayName),createdTime,resolved,"
    "quotedFileContent(value),replies(author(displayName),content,createdTime)"
)


async def list_google_slides_comments(presentation_url: str) -> str:
    """List the comment threads already on a Google Slides deck (including
    resolved ones and replies). Check before commenting so feedback doesn't
    repeat what peer reviewers or the instructor already said."""
    try:
        presentation_id = _extract_id(presentation_url)
    except ValueError as exc:
        return str(exc)
    try:
        result = await google_request(
            "GET",
            f"/files/{presentation_id}/comments",
            params={"fields": f"comments({_COMMENT_FIELDS}),nextPageToken", "pageSize": 100},
        )
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, presentation_id)

    comments = result.get("comments") or []
    if not comments:
        return f"No comments on Google Slides deck {presentation_id}."
    blocks = []
    for c in comments:
        author = (c.get("author") or {}).get("displayName", "?")
        status = " [RESOLVED]" if c.get("resolved") else ""
        quote = (c.get("quotedFileContent") or {}).get("value")
        lines = [f"[{format_date(c.get('createdTime'))}] {author}{status}: {c.get('content', '')}"]
        if quote:
            lines.append(f'  re: "{quote}"')
        for reply in c.get("replies") or []:
            reply_author = (reply.get("author") or {}).get("displayName", "?")
            lines.append(f"  ↳ [{format_date(reply.get('createdTime'))}] {reply_author}: {reply.get('content', '')}")
        blocks.append("\n".join(lines))
    note = "\n\n(Showing first 100 comment threads — more exist.)" if result.get("nextPageToken") else ""
    return f"Comments on Google Slides deck {presentation_id}:\n\n" + "\n\n".join(blocks) + note


async def comment_on_google_slides(presentation_url: str, comment: str, quoted_text: str = "") -> str:
    """Leave one feedback comment on a student's Google Slides deck, posted from
    the connected instructor account. Pass the slide text the feedback is about
    as quoted_text so the card shows what it refers to (say the slide number in
    the comment too — Slides comments can't be pinned to a text range through
    the API, so the comment appears in the deck's comment panel). One call per
    piece of feedback. Grades still go through grade_submission in Canvas."""
    try:
        presentation_id = _extract_id(presentation_url)
    except ValueError as exc:
        return str(exc)
    if not comment.strip():
        return "Comment text is empty — nothing posted."
    body: dict = {"content": comment}
    quote = quoted_text.strip()
    if quote:
        body["quotedFileContent"] = {"mimeType": "text/plain", "value": quote}
    try:
        created = await google_request(
            "POST", f"/files/{presentation_id}/comments", params={"fields": _COMMENT_FIELDS}, json_body=body
        )
    except GoogleConfigError as exc:
        return str(exc)
    except GoogleAPIError as exc:
        return await _explain_api_error(exc, presentation_id)
    author = (created.get("author") or {}).get("displayName", "the connected account")
    quote_note = f'\nre: "{quote}"' if quote else ""
    return f"Posted comment {created.get('id')} on deck {presentation_id} as {author}:\n{created.get('content', comment)}{quote_note}"
