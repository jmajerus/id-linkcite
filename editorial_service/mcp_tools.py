"""MCP tools for LinkCite editorial content, served statelessly at /mcp by the admin listener.

Each POST carries one JSON-RPC message and gets one JSON answer (no sessions, no SSE).
Agents can read, preview and save drafts. Publishing, unpublishing and restoring stay
with a person in /admin: a saved draft is a proposal until someone publishes it.
"""
import json

import psycopg

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
MAX_TEXT = 250_000

INSTRUCTIONS = """\
Supplementary Markdown for the public reader pages at https://id.linkcite.org/, one text per term IRI \
(patterns, frameworks and their terms, tags). The RDF stays authoritative: a page shows the term's \
definitions and notes from the vocabulary first, then its published Markdown below them.
Typical flow: search_resources to find a term (or list those without published text) → get_content for its \
canonical RDF text, current draft and revision number → write a draft that adds to the definition rather \
than repeating it → preview_markdown → save_draft with the revision number you read.
save_draft only saves a draft. Nothing reaches the public page until a person publishes it in the admin \
pages, so say what you saved and that it awaits review. A stale revision number means someone else saved \
first: read again and merge, never overwrite blindly.
Private notes are the editor's own and may contain unpublished manuscript text; read them only when asked, \
and never copy them into a public draft. Do not put manuscript passages in a draft unless they are meant \
to be published."""

IRI_PROP = {"type": "string", "description": "The term's exact public IRI, e.g. https://id.linkcite.org/patterns/sacralization-of-procedure"}
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

TOOLS = [
    {"name": "search_resources",
     "title": "Find terms",
     "description": "Find current public resources by words in their label or IRI, with each one's editorial status. "
                    "Status filters: any, published (has public text), unpublished (no public text), "
                    "pending (a saved draft differs from what is published).",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Words to match in the label or IRI; empty lists everything."},
         "status": {"type": "string", "enum": ["any", "published", "unpublished", "pending"], "default": "any"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 25}},
         "additionalProperties": False},
     "annotations": READ_ONLY},
    {"name": "get_content",
     "title": "Read a term's editorial content",
     "description": "A term's canonical RDF text (shown above the Markdown on its page), its draft, its published "
                    "Markdown and the revision number save_draft needs. Private notes only when asked for.",
     "inputSchema": {"type": "object", "properties": {
         "iri": IRI_PROP,
         "include_private_notes": {"type": "boolean", "default": False}},
         "required": ["iri"], "additionalProperties": False},
     "annotations": READ_ONLY},
    {"name": "list_revisions",
     "title": "List a term's revisions",
     "description": "A term's revision history, newest first: number, action, who, when and the revision note.",
     "inputSchema": {"type": "object", "properties": {"iri": IRI_PROP},
                     "required": ["iri"], "additionalProperties": False},
     "annotations": READ_ONLY},
    {"name": "get_revision",
     "title": "Read one revision",
     "description": "The draft and published Markdown as they were at one revision. Private notes only when asked for.",
     "inputSchema": {"type": "object", "properties": {
         "iri": IRI_PROP,
         "revision_number": {"type": "integer", "minimum": 1},
         "include_private_notes": {"type": "boolean", "default": False}},
         "required": ["iri", "revision_number"], "additionalProperties": False},
     "annotations": READ_ONLY},
    {"name": "preview_markdown",
     "title": "Preview Markdown",
     "description": "Render Markdown exactly as the public reader would, sanitized, to check headings, links and tables. "
                    "Saves nothing.",
     "inputSchema": {"type": "object", "properties": {"markdown": {"type": "string", "maxLength": MAX_TEXT}},
                     "required": ["markdown"], "additionalProperties": False},
     "annotations": READ_ONLY},
    {"name": "save_draft",
     "title": "Save a draft",
     "description": "Save a term's public draft (not published: a person publishes it in the admin pages). "
                    "Pass the revision_number from get_content; if someone saved since, this fails and nothing "
                    "changes. Private notes are kept as they are unless private_notes is given.",
     "inputSchema": {"type": "object", "properties": {
         "iri": IRI_PROP,
         "revision_number": {"type": "integer", "minimum": 0, "description": "The revision you read and edited."},
         "draft_markdown": {"type": "string", "maxLength": MAX_TEXT},
         "change_note": {"type": "string", "maxLength": 2000, "description": "What changed and why, for the history."},
         "private_notes": {"type": "string", "maxLength": MAX_TEXT, "description": "Replace the private notes; omit to keep them."}},
         "required": ["iri", "revision_number", "draft_markdown"], "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
]
TOOL_NAMES = {t["name"] for t in TOOLS}


class ToolError(Exception):
    """A tool failed in a way the caller can act on; reported as an isError result."""


class Context:
    """What the tools need from the server: a connection pool, the registry and the renderer."""
    def __init__(self, pool, actor, resources, canonical, render, read_state, change):
        self.pool, self.actor = pool, actor
        self.resources, self.canonical, self.render = resources, canonical, render
        self.read_state, self.change = read_state, change


def _iri(ctx, args):
    iri = args.get("iri")
    if not isinstance(iri, str) or iri not in ctx.resources():
        raise ToolError("Not a current public resource IRI. Use search_resources to find one.")
    return iri


def _text(args, name, required=True, limit=MAX_TEXT):
    value = args.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ToolError(f"{name} must be text of at most {limit} characters.")
    return value


def _int(args, name, minimum):
    value = args.get(name)
    if type(value) is not int or not minimum <= value < 2**63 - 1:
        raise ToolError(f"{name} must be an integer of at least {minimum}.")
    return value


def _bool(args, name):
    value = args.get(name, False)
    if not isinstance(value, bool):
        raise ToolError(f"{name} must be true or false.")
    return value


def _when(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _content(state, label, canonical, private):
    published = state["published_markdown"]
    value = {"iri": state["iri"], "label": label,
             "revision_number": state["revision_number"],
             "canonical_rdf_text": canonical,
             "draft_markdown": state["draft_markdown"],
             "published_markdown": published,
             "pending": published is not None and state["draft_markdown"] != published
                        or published is None and state["draft_markdown"] != "",
             "updated_at": _when(state["updated_at"]), "published_at": _when(state["published_at"])}
    if private:
        value["private_notes"] = state["private_markdown"]
    return value


def search_resources(ctx, args):
    query = _text(args, "query", required=False, limit=500) or ""
    status = args.get("status", "any")
    if status not in ("any", "published", "unpublished", "pending"):
        raise ToolError("status must be any, published, unpublished or pending.")
    limit = args.get("limit", 25)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ToolError("limit must be an integer from 1 to 200.")
    words = query.lower().split()
    registry = ctx.resources()
    matches = sorted((iri for iri, label in registry.items()
                      if all(w in (label + " " + iri).lower() for w in words)),
                     key=lambda iri: (registry[iri].lower(), iri))
    with ctx.pool.connection() as conn:
        rows = conn.execute(
            "SELECT iri, revision_number, published_markdown IS NOT NULL AS published, "
            "published_markdown IS DISTINCT FROM draft_markdown "
            "AND NOT (published_markdown IS NULL AND draft_markdown = '') AS pending "
            "FROM editorial.resource_content WHERE iri = ANY(%s)", (matches,)).fetchall()
    states = {r["iri"]: r for r in rows}
    results = []
    for iri in matches:
        row = states.get(iri) or {"revision_number": 0, "published": False, "pending": False}
        if status == "published" and not row["published"] or status == "unpublished" and row["published"] \
                or status == "pending" and not row["pending"]:
            continue
        results.append({"iri": iri, "label": registry[iri], "revision_number": row["revision_number"],
                        "published": row["published"], "pending": row["pending"]})
    return {"total": len(results), "results": results[:limit]}


def get_content(ctx, args):
    iri, private = _iri(ctx, args), _bool(args, "include_private_notes")
    with ctx.pool.connection() as conn:
        state = ctx.read_state(conn, iri)
    return _content(state, ctx.resources()[iri], ctx.canonical().get(iri, {}), private)


def list_revisions(ctx, args):
    iri = _iri(ctx, args)
    with ctx.pool.connection() as conn:
        rows = conn.execute(
            "SELECT revision_number, action, changed_by, changed_at, change_note "
            "FROM editorial.resource_revision WHERE iri=%s ORDER BY revision_number DESC", (iri,)).fetchall()
    return {"iri": iri, "revisions": [{**r, "changed_at": _when(r["changed_at"])} for r in rows]}


def get_revision(ctx, args):
    iri, number = _iri(ctx, args), _int(args, "revision_number", 1)
    private = _bool(args, "include_private_notes")
    with ctx.pool.connection() as conn:
        row = conn.execute(
            "SELECT revision_number, action, draft_markdown, private_markdown, published_markdown, "
            "published_at, changed_by, changed_at, change_note "
            "FROM editorial.resource_revision WHERE iri=%s AND revision_number=%s", (iri, number)).fetchone()
    if not row:
        raise ToolError("No such revision. Use list_revisions to see them.")
    value = {k: _when(v) for k, v in row.items() if k != "private_markdown"}
    if private:
        value["private_notes"] = row["private_markdown"]
    return {"iri": iri, **value}


def preview_markdown(ctx, args):
    return {"html": str(ctx.render(_text(args, "markdown")))}


def save_draft(ctx, args):
    iri = _iri(ctx, args)
    expected = _int(args, "revision_number", 0)
    draft = _text(args, "draft_markdown")
    note = _text(args, "change_note", required=False, limit=2000) or None
    notes = _text(args, "private_notes", required=False)
    with ctx.pool.connection() as conn:
        if notes is None:
            # Keep the notes as of the revision the caller read; change() refuses if that is stale.
            current = ctx.read_state(conn, iri)
            if current["revision_number"] != expected:
                raise ToolError(f"Stale revision: the term is at revision {current['revision_number']}, "
                                f"not {expected}. Read it again with get_content and merge your changes.")
            notes = current["private_markdown"]
        body = {"iri": iri, "action": "save", "revision_number": expected,
                "draft_markdown": draft, "private_markdown": notes, "change_note": note}
        state = ctx.change(conn, body, ctx.actor)
    value = _content(state, ctx.resources()[iri], ctx.canonical().get(iri, {}), False)
    value.pop("canonical_rdf_text")
    value["saved"] = state["revision_number"] != expected
    value["message"] = ("Draft saved; it is not public until a person publishes it."
                        if value["saved"] else "Nothing changed: the draft was already this text.")
    return value


HANDLERS = {"search_resources": search_resources, "get_content": get_content,
            "list_revisions": list_revisions, "get_revision": get_revision,
            "preview_markdown": preview_markdown, "save_draft": save_draft}


def _error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def call_tool(ctx, params, problem_type):
    name, args = params.get("name"), params.get("arguments") or {}
    if not isinstance(args, dict):
        return {"content": [{"type": "text", "text": "arguments must be an object."}], "isError": True}
    try:
        value = HANDLERS[name](ctx, args)
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except problem_type as exc:  # the server's own validation (registry, stale revision, …)
        return {"content": [{"type": "text", "text": exc.message}], "isError": True}
    except psycopg.Error:
        return {"content": [{"type": "text", "text": "The editorial database is unavailable."}], "isError": True}
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}],
            "structuredContent": value}


def handle(message, ctx_factory, server_info, problem_type):
    """One JSON-RPC message in; the response object, or None for a notification or response."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request: one JSON-RPC 2.0 message per POST.")
    if "method" not in message:
        return None  # a response to something we never ask; nothing to answer
    method, id_ = message["method"], message.get("id")
    if "id" not in message:
        return None  # notifications (initialized, cancelled, …) need no answer
    if not isinstance(id_, (str, int)) or isinstance(id_, bool):
        return _error(None, -32600, "Invalid Request: id must be a string or integer.")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(id_, -32602, "params must be an object.")
    if method == "initialize":
        requested = params.get("protocolVersion")
        return _result(id_, {
            "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": server_info,
            "instructions": INSTRUCTIONS})
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in TOOL_NAMES:
            return _error(id_, -32602, f"Unknown tool: {name!r}."[:300])
        return _result(id_, call_tool(ctx_factory(), params, problem_type))
    return _error(id_, -32601, f"Method not found: {method}.")
