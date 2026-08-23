from __future__ import annotations

from typing import Optional, Union

from ..client import canvas_paginated, canvas_request
from ..util import format_date, get_course_id


async def list_modules(course_identifier: Union[str, int]) -> str:
    """List modules in a course with publish status and item counts."""
    course_id = await get_course_id(course_identifier)
    modules = await canvas_paginated(f"/courses/{course_id}/modules", {"include[]": "items"})
    if not modules:
        return "No modules found."
    lines = []
    for m in modules:
        items = m.get("items", []) or []
        lines.append(
            f"**{m.get('name')}**\n"
            f"  ID: {m.get('id')} | Position: {m.get('position')}\n"
            f"  Published: {'Yes' if m.get('published') else 'No'}\n"
            f"  Items: {len(items)}"
        )
    return f"Modules in course {course_id}:\n\n" + "\n\n".join(lines)


async def get_course_structure(course_identifier: Union[str, int]) -> str:
    """Get the full module/item tree for a course in one call."""
    course_id = await get_course_id(course_identifier)
    modules = await canvas_paginated(f"/courses/{course_id}/modules", {"include[]": "items"})
    lines = [f"Course {course_id} structure ({len(modules)} modules):\n"]
    for m in modules:
        lines.append(f"[{'x' if m.get('published') else ' '}] {m.get('name')} (id={m.get('id')})")
        for item in m.get("items", []) or []:
            lines.append(
                f"    [{'x' if item.get('published') else ' '}] "
                f"{item.get('type')}: {item.get('title')} "
                f"(id={item.get('id')}"
                + (f", page_url={item.get('page_url')}" if item.get("page_url") else "")
                + ")"
            )
    return "\n".join(lines)


async def list_pages(course_identifier: Union[str, int]) -> str:
    """List wiki pages in a course with publish status and last-updated time."""
    course_id = await get_course_id(course_identifier)
    pages = await canvas_paginated(f"/courses/{course_id}/pages")
    if not pages:
        return "No pages found."
    lines = []
    for p in pages:
        lines.append(
            f"URL: {p.get('url')}\n"
            f"Title: {p.get('title')}\n"
            f"Status: {'Published' if p.get('published') else 'Unpublished'}\n"
            f"Updated: {format_date(p.get('updated_at'))}"
        )
    return f"Pages in course {course_id}:\n\n" + "\n\n".join(lines)


async def get_page_content(course_identifier: Union[str, int], page_url: str) -> str:
    """Get the full body of a specific wiki page by its URL slug."""
    course_id = await get_course_id(course_identifier)
    page = await canvas_request("GET", f"/courses/{course_id}/pages/{page_url}")
    return (
        f"Title: {page.get('title')}\n"
        f"Status: {'Published' if page.get('published') else 'Unpublished'}\n"
        f"Updated: {format_date(page.get('updated_at'))}\n\n"
        f"{page.get('body', '')}"
    )


async def create_page(
    course_identifier: Union[str, int], title: str, body: str = "", published: bool = False
) -> str:
    """Create a new wiki page. Defaults to unpublished (draft) unless published=True."""
    course_id = await get_course_id(course_identifier)
    page = await canvas_request(
        "POST",
        f"/courses/{course_id}/pages",
        json_body={"wiki_page": {"title": title, "body": body, "published": published}},
    )
    return f"Created page '{page.get('title')}' (url: {page.get('url')}, published: {page.get('published')})"


async def edit_page_content(
    course_identifier: Union[str, int],
    page_url: str,
    title: Optional[str] = None,
    body: Optional[str] = None,
) -> str:
    """Replace a page's title and/or body. Only fields you pass are changed."""
    course_id = await get_course_id(course_identifier)
    fields: dict = {}
    if title is not None:
        fields["title"] = title
    if body is not None:
        fields["body"] = body
    if not fields:
        return "Nothing to update — provide title and/or body."
    page = await canvas_request(
        "PUT", f"/courses/{course_id}/pages/{page_url}", json_body={"wiki_page": fields}
    )
    return f"Updated page '{page.get('title')}' (url: {page.get('url')})"


async def update_page_settings(
    course_identifier: Union[str, int], page_url: str, published: bool
) -> str:
    """Publish or unpublish a page."""
    course_id = await get_course_id(course_identifier)
    page = await canvas_request(
        "PUT",
        f"/courses/{course_id}/pages/{page_url}",
        json_body={"wiki_page": {"published": published}},
    )
    return f"Page '{page.get('title')}' is now {'Published' if page.get('published') else 'Unpublished'}"


async def create_module(
    course_identifier: Union[str, int], name: str, position: Optional[int] = None
) -> str:
    """Create a new module (starts unpublished)."""
    course_id = await get_course_id(course_identifier)
    fields: dict = {"name": name}
    if position is not None:
        fields["position"] = position
    module = await canvas_request(
        "POST", f"/courses/{course_id}/modules", json_body={"module": fields}
    )
    return f"Created module '{module.get('name')}' (ID: {module.get('id')})"


async def update_module(
    course_identifier: Union[str, int],
    module_id: Union[str, int],
    name: Optional[str] = None,
    published: Optional[bool] = None,
    position: Optional[int] = None,
) -> str:
    """Rename, publish/unpublish, or reposition a module."""
    course_id = await get_course_id(course_identifier)
    fields: dict = {}
    if name is not None:
        fields["name"] = name
    if published is not None:
        fields["published"] = published
    if position is not None:
        fields["position"] = position
    if not fields:
        return "Nothing to update — provide name, published, and/or position."
    module = await canvas_request(
        "PUT", f"/courses/{course_id}/modules/{module_id}", json_body={"module": fields}
    )
    return (
        f"Updated module '{module.get('name')}' "
        f"(published: {module.get('published')}, position: {module.get('position')})"
    )


async def add_module_item(
    course_identifier: Union[str, int],
    module_id: Union[str, int],
    item_type: str,
    title: Optional[str] = None,
    content_id: Optional[Union[str, int]] = None,
    page_url: Optional[str] = None,
    external_url: Optional[str] = None,
    position: Optional[int] = None,
    indent: Optional[int] = None,
) -> str:
    """Add an item to a module. item_type: File, Page, Discussion, Assignment, Quiz, SubHeader, ExternalUrl, ExternalTool.
    Page items need page_url; most others need content_id; ExternalUrl needs external_url."""
    course_id = await get_course_id(course_identifier)
    fields: dict = {"type": item_type}
    if title is not None:
        fields["title"] = title
    if content_id is not None:
        fields["content_id"] = content_id
    if page_url is not None:
        fields["page_url"] = page_url
    if external_url is not None:
        fields["external_url"] = external_url
    if position is not None:
        fields["position"] = position
    if indent is not None:
        fields["indent"] = indent
    item = await canvas_request(
        "POST",
        f"/courses/{course_id}/modules/{module_id}/items",
        json_body={"module_item": fields},
    )
    return f"Added item '{item.get('title')}' to module {module_id} (item ID: {item.get('id')})"


async def update_module_item(
    course_identifier: Union[str, int],
    module_id: Union[str, int],
    item_id: Union[str, int],
    title: Optional[str] = None,
    published: Optional[bool] = None,
    position: Optional[int] = None,
    indent: Optional[int] = None,
) -> str:
    """Rename, publish/unpublish, reposition, or re-indent a module item."""
    course_id = await get_course_id(course_identifier)
    fields: dict = {}
    if title is not None:
        fields["title"] = title
    if published is not None:
        fields["published"] = published
    if position is not None:
        fields["position"] = position
    if indent is not None:
        fields["indent"] = indent
    if not fields:
        return "Nothing to update — provide title, published, position, and/or indent."
    item = await canvas_request(
        "PUT",
        f"/courses/{course_id}/modules/{module_id}/items/{item_id}",
        json_body={"module_item": fields},
    )
    return f"Updated item '{item.get('title')}' (published: {item.get('published')})"


async def delete_page(course_identifier: Union[str, int], page_url: str) -> str:
    """Permanently delete a wiki page. This cannot be undone."""
    course_id = await get_course_id(course_identifier)
    page = await canvas_request("DELETE", f"/courses/{course_id}/pages/{page_url}")
    return f"Deleted page '{page.get('title', page_url)}'."


async def delete_module(course_identifier: Union[str, int], module_id: Union[str, int]) -> str:
    """Permanently delete a module and all its items (the items themselves, e.g. pages, are not deleted). This cannot be undone."""
    course_id = await get_course_id(course_identifier)
    module = await canvas_request("DELETE", f"/courses/{course_id}/modules/{module_id}")
    return f"Deleted module '{module.get('name', module_id)}'."
