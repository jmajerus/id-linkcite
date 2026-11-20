#!/usr/bin/env python3
"""Services for LinkCite editorial content.

Run public and admin modes in separate processes with distinct database roles.
The public reader listens on loopback only, for Trifid. The admin listener follows
the authoring suite: port 8000 on every interface, serving the editor at /admin,
its JSON API at /api, MCP at /mcp and /health. Direct LAN requests are trusted;
requests through the Cloudflare tunnel need a verified Access assertion (access.py).
"""
import argparse
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections import OrderedDict
from threading import Lock

import psycopg
from urllib.parse import parse_qs, urlsplit

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from editorial import render_markdown
from access import Access, AccessError
import mcp_tools

LOG = logging.getLogger("linkcite-editorial")
BASE = "https://id.linkcite.org/"
MAX_BODY = 2 * 1024 * 1024
MAX_TEXT = 250_000
STATE_COLUMNS = ("iri, draft_markdown, private_markdown, published_markdown, "
                 "revision_number, created_at, updated_at, published_at")
HISTORY_COLUMNS = ("revision_number, action, changed_by, changed_at, change_note")
DSN_VARIABLES = {"public": "LINKCITE_EDITORIAL_READER_DSN",
                 "admin": "LINKCITE_EDITORIAL_EDITOR_DSN"}
# The password is no longer used (the admin listener follows the suite's Access rules);
# an old environment file may still carry it, so it is dropped before serving.
SECRET_VARIABLES = (*DSN_VARIABLES.values(), "LINKCITE_EDITORIAL_PASSWORD")
ADMIN_PORT = 8000
SERVICE = "id-linkcite"


class Server(ThreadingHTTPServer):
    # The socketserver default backlog of 5 drops SYNs under bursts, adding
    # 1 s retransmits that exceed the reader's 100 ms deadline.
    request_queue_size = 128


class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def resources():
    # Read the current release's registry (never a private source graph).
    return json.loads((ROOT / "build/editorial-resources.json").read_text(encoding="utf-8"))


def canonical():
    # The public RDF text each page shows above its Markdown; absent from older builds.
    path = ROOT / "build/editorial-canonical.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def version():
    return json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]


def health(pool, admin_url):
    """The authoring suite's health contract (authoring-ops-mcp docs/health-contract.md)."""
    try:
        with pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1 FROM editorial.resource_content LIMIT 1")
        database = True
    except Exception:
        database = False
    value = {"ok": True, "service": SERVICE, "version": version(),
             "dependencies": [{"name": "postgresql", "ok": database}], "admin": admin_url}
    deployed = ROOT / "deployed.json"
    if deployed.exists():
        value["deployed"] = json.loads(deployed.read_text(encoding="utf-8"))
    return value


def actor_for(identity, lan_actor, channel=None):
    who = identity.get("email") or identity.get("subject") or lan_actor
    return (f"{channel} ({who})" if channel else who)[:200]


def validate_iri(iri):
    if not isinstance(iri, str) or len(iri) > 2048 or iri not in resources():
        raise Problem(404, "IRI is not in the current public resource registry.")


def check_text(value, field):
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise Problem(400, f"{field} must be text of at most {MAX_TEXT} characters.")
    return value


def empty_state(iri):
    return {"iri": iri, "draft_markdown": "", "private_markdown": "",
            "published_markdown": None, "revision_number": 0, "created_at": None,
            "updated_at": None, "published_at": None}


def read_state(conn, iri):
    row = conn.execute(
        f"SELECT {STATE_COLUMNS} FROM editorial.resource_content WHERE iri=%s",
        (iri,)).fetchone()
    return row or empty_state(iri)


def change(conn, body, actor):
    iri = body.get("iri")
    validate_iri(iri)
    action = body.get("action")
    if action not in {"save", "publish", "unpublish", "restore"}:
        raise Problem(400, "Unknown action.")
    expected = body.get("revision_number")
    if type(expected) is not int or not 0 <= expected < 2**63-1:
        raise Problem(400, "Expected revision number is required.")
    note = body.get("change_note") or None
    if note is not None:
        note = check_text(note, "Change note")
    draft, private, restore = None, None, None
    if action == "restore":
        restore = body.get("restore_revision")
        if type(restore) is not int or not 0 < restore < 2**63:
            raise Problem(400, "Revision number is required.")
    else:
        draft = check_text(body.get("draft_markdown"), "Draft")
        private = check_text(body.get("private_markdown"), "Private notes")
    try:
        # The function alone has write privileges; the editor has SELECT/EXECUTE.
        # A savepoint also permits callers to recover from a stale revision safely.
        with conn.transaction():
            return conn.execute(
                f"SELECT {STATE_COLUMNS} FROM editorial.change_resource("
                "%s::text,%s::bigint,%s::text,%s::text,%s::text,%s::bigint,%s::text,%s::text)",
                (iri, expected, action, draft, private, restore, actor, note)).fetchone()
    except psycopg.errors.SerializationFailure as exc:
        raise Problem(409, "Someone changed this resource. Reload before saving.") from exc
    except psycopg.errors.NoDataFound as exc:
        raise Problem(404, "Revision not found.") from exc
    except psycopg.errors.InvalidParameterValue as exc:
        raise Problem(400, "Invalid editorial change.") from exc


class PublishedHTMLCache:
    """Bounded LRU of sanitized HTML keyed by exact published Markdown.

    Only the reader view supplies inputs. A changed or unpublished document
    cannot retrieve its former cache entry by IRI or revision number.
    """
    def __init__(self, max_bytes=32 * 1024 * 1024, max_entries=1024):
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self.entries = OrderedDict()
        self.size = 0
        self.lock = Lock()

    def render(self, source):
        with self.lock:
            if source in self.entries:
                self.entries.move_to_end(source)
                return self.entries[source][0]
        # Always sanitize a cache miss, even if the editor bypassed the UI.
        html = str(render_markdown(source))
        size = sys.getsizeof(source) + sys.getsizeof(html)
        if size > self.max_bytes or not self.max_entries:
            return html
        with self.lock:
            if source in self.entries:
                self.entries.move_to_end(source)
                return self.entries[source][0]
            while self.entries and (self.size + size > self.max_bytes or
                                    len(self.entries) >= self.max_entries):
                _, (_, old_size) = self.entries.popitem(last=False)
                self.size -= old_size
            self.entries[source] = (html, size)
            self.size += size
        return html


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Headers and body are separate writes on a keep-alive connection; with Nagle
    # enabled, delayed ACKs add ~40 ms to every response on Linux.
    disable_nagle_algorithm = True

    def log_message(self, fmt, *args):
        # No request paths, query strings, credentials or document bodies in logs.
        pass

    def send_bytes(self, status, body, content_type, extra=None):
        if status >= 400:
            self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", 
                         "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, status, value, extra=None):
        self.send_bytes(status, json.dumps(value, default=str).encode("utf-8"),
                        "application/json; charset=utf-8", extra)

    def read_json(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                raise Problem(413, "Request too large or empty.")
            return json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError):
            raise Problem(400, "Invalid JSON.")

    def same_origin(self):
        # Browsers send Origin on cross-site POSTs; no CORS headers are ever sent, and a
        # JSON content type forces a preflight that fails, so cross-site writes cannot land.
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        return origin is None or origin in ("http://" + host, "https://" + host)

    def mcp(self, identity):
        if self.command != "POST":
            self.json(405, {"error": "MCP here is stateless: POST one JSON-RPC message."}, {"Allow": "POST"})
            return
        if self.headers.get_content_type() != "application/json" or not self.same_origin():
            raise Problem(403, "A JSON POST from an MCP client is required.")
        version_header = self.headers.get("MCP-Protocol-Version")
        if version_header and version_header not in mcp_tools.PROTOCOL_VERSIONS:
            raise Problem(400, "Unsupported MCP-Protocol-Version.")
        message = self.read_json()
        server = self.server
        context = lambda: mcp_tools.Context(
            server.pool, actor_for(identity, server.actor, "mcp"), resources, canonical,
            render_markdown, read_state, change)
        response = mcp_tools.handle(message, context, {"name": SERVICE, "title": "LinkCite editorial",
                                                       "version": version()}, Problem)
        if response is None:
            self.send_bytes(202, b"", "application/json; charset=utf-8")
        else:
            self.json(200, response)

    def dispatch(self):
        url = urlsplit(self.path)
        params = parse_qs(url.query)
        iri = params.get("iri", [""])[0]
        if self.server.mode == "public":
            # Only accept the literal loopback hosts. Prevent DNS-rebinding access.
            if self.headers.get("Host") not in self.server.allowed_hosts:
                raise Problem(403, "Invalid host.")
            if self.command not in ("GET", "HEAD") or url.path != "/content":
                raise Problem(404, "Not found.")
            doc = params.get("document", [""])[0]
            if not doc.startswith(BASE) or "#" in doc or len(doc) > 2048:
                raise Problem(400, "Invalid document IRI.")
            with self.server.pool.connection(timeout=0.045) as conn:
                rows = conn.execute(
                    "SELECT iri, published_markdown FROM editorial.public_content WHERE document_iri=%s",
                    (doc,)).fetchall()
            self.json(200, {r["iri"]: self.server.html_cache.render(r["published_markdown"]) for r in rows})
            return
        try:
            identity = self.server.access.authorize(self.headers, self.client_address[0])
        except AccessError as exc:
            raise Problem(exc.status, exc.message)
        if url.path == "/mcp":
            self.mcp(identity)
            return
        if self.command in ("GET", "HEAD"):
            if url.path == "/health":
                self.json(200, health(self.server.pool, self.server.admin_url))
                return
            if url.path == "/":
                self.send_bytes(302, b"", "text/plain; charset=utf-8", {"Location": "/admin"})
                return
            assets = {"/admin": ("admin.html", "text/html; charset=utf-8"),
                      "/admin/": ("admin.html", "text/html; charset=utf-8"),
                      "/admin/admin.js": ("admin.js", "text/javascript; charset=utf-8"),
                      "/admin/admin.css": ("admin.css", "text/css; charset=utf-8")}
            if url.path in assets:
                name, mime = assets[url.path]
                self.send_bytes(200, (Path(__file__).parent / name).read_bytes(), mime)
                return
            if url.path == "/api/resources":
                self.json(200, resources())
                return
            if url.path in ("/api/state", "/api/history", "/api/revision"):
                validate_iri(iri)
                with self.server.pool.connection() as conn:
                    if url.path == "/api/state":
                        value = read_state(conn, iri)
                    elif url.path == "/api/history":
                        value = conn.execute(
                            f"SELECT {HISTORY_COLUMNS} FROM editorial.resource_revision "
                            "WHERE iri=%s ORDER BY revision_number DESC", (iri,)).fetchall()
                    else:
                        try:
                            number = int(params.get("revision", [""])[0])
                        except ValueError:
                            raise Problem(400, "Invalid revision.")
                        value = conn.execute(
                            "SELECT * FROM editorial.resource_revision WHERE iri=%s AND revision_number=%s",
                            (iri, number)).fetchone()
                        if not value:
                            raise Problem(404, "Revision not found.")
                self.json(200, value)
                return
            raise Problem(404, "Not found.")
        if self.command != "POST" or url.path not in ("/api/preview", "/api/change"):
            raise Problem(404, "Not found.")
        # Same-origin AJAX only; no CORS. The custom header and JSON type force a preflight.
        if (self.headers.get("Origin") is None or not self.same_origin() or
                self.headers.get("X-LinkCite-Editor") != "1" or
                self.headers.get_content_type() != "application/json"):
            raise Problem(403, "Same-origin editor request required.")
        body = self.read_json()
        if not isinstance(body, dict):
            raise Problem(400, "JSON object required.")
        if url.path == "/api/preview":
            self.json(200, {"html": str(render_markdown(check_text(body.get("markdown"), "Markdown")))})
        else:
            with self.server.pool.connection() as conn:
                value = change(conn, body, actor_for(identity, self.server.actor))
            self.json(200, value)

    def handle_request(self):
        try:
            self.dispatch()
        except Problem as exc:
            self.json(exc.status, {"error": exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            LOG.warning("Editorial request failed: %s", type(exc).__name__)
            try:
                self.json(503, {"error": "Editorial service temporarily unavailable."})
            except (BrokenPipeError, ConnectionResetError):
                pass

    do_GET = do_POST = do_HEAD = handle_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("public", "admin"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # One environment file serves both units; each mode reads only its own role's
    # DSN, and the systemd units unset the other mode's secrets before start.
    dsn = os.environ[DSN_VARIABLES[args.mode]]
    port = 7880 if args.mode == "public" else int(os.environ.get("LINKCITE_EDITORIAL_PORT", ADMIN_PORT))
    # Warm persistent connections. The reader has a tightly bounded transaction time.
    timeout = 50 if args.mode == "public" else 5000
    with ConnectionPool(dsn, min_size=1, max_size=8 if args.mode == "public" else 3,
                        timeout=0.05 if args.mode == "public" else 2,
                        kwargs={"row_factory": dict_row, "connect_timeout": 3,
                                "options": f"-c statement_timeout={timeout}"}) as pool:
        server = Server(("127.0.0.1" if args.mode == "public" else "0.0.0.0", port), Handler)
        server.mode, server.pool = args.mode, pool
        if args.mode == "public":
            server.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            server.html_cache = PublishedHTMLCache()
        if args.mode == "admin":
            # Who LAN edits are recorded as; Access sign-ins are recorded by their email.
            server.actor = os.environ.get("LINKCITE_EDITORIAL_USER", "editor")
            lan_hosts = os.environ.get("LINKCITE_LAN_HOSTS", "id-linkcite.localdomain,id-linkcite").split(",")
            server.access = Access(os.environ.get("ACCESS_TEAM_DOMAIN"), lan_hosts)
            server.admin_url = f"http://{lan_hosts[0].strip()}:{port}/admin"
            if not server.access.issuer:
                LOG.warning("ACCESS_TEAM_DOMAIN is unset or invalid: requests through Cloudflare will be refused.")
        for name in SECRET_VARIABLES:
            os.environ.pop(name, None)
        LOG.info("%s listening on %s:%s", args.mode, server.server_address[0], port)
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
