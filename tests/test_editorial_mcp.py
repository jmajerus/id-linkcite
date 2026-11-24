"""The admin listener's Access rules, its MCP protocol handling and tools, without a database."""
import http.client
import json
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "editorial_service"))
import mcp_tools
import server
from access import Access, AccessError, private_peer

ISSUER = "https://team.cloudflareaccess.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
IRI = "https://id.linkcite.org/dehumanization/patterns/example"
REGISTRY = {IRI: "Example pattern", "https://id.linkcite.org/dehumanization/patterns/other": "Other"}


def token(key=KEY, **claims):
    now = int(time.time())
    body = {"iss": ISSUER, "sub": "owner-id", "email": "owner@example.org", "iat": now, "exp": now + 300,
            "aud": ["some-application"], **claims}
    return jwt.encode({k: v for k, v in body.items() if v is not None}, key, algorithm="RS256")


def headers(**values):
    message = Message()
    for name, value in values.items():
        for v in value if isinstance(value, list) else [value]:
            message[name.replace("_", "-")] = v
    return message


ACCESS = Access(ISSUER + "/", ["id-linkcite.localdomain"], keys=lambda _: KEY.public_key())


class AccessTests(unittest.TestCase):
    def test_lan_request_naming_this_host_is_trusted(self):
        for host in ("id-linkcite.localdomain:8000", "localhost:8000", "192.168.1.20:8000", "ID-LINKCITE.localdomain"):
            self.assertEqual(ACCESS.authorize(headers(Host=host), "192.168.1.5"), {"via": "lan"})

    def test_untrusted_routes_need_an_assertion(self):
        cases = [
            (headers(Host="id-linkcite.localdomain:8000", CF_Ray="abc"), "192.168.1.5"),   # came through the tunnel
            (headers(Host="evil.example:8000"), "192.168.1.5"),                            # DNS rebinding
            (headers(Host="id-linkcite.localdomain:8000"), "203.0.113.9"),                 # a public peer
            (headers(Host="id-linkcite.majerus.us", CF_Connecting_IP="203.0.113.9"), "192.168.1.5"),
        ]
        for message, peer in cases:
            with self.assertRaises(AccessError) as ctx:
                ACCESS.authorize(message, peer)
            self.assertEqual(ctx.exception.status, 401)

    def test_valid_assertion_identifies_the_owner(self):
        identity = ACCESS.authorize(headers(Host="id-linkcite.majerus.us", CF_Ray="abc",
                                            Cf_Access_Jwt_Assertion=token()), "192.168.1.5")
        self.assertEqual(identity, {"via": "access", "subject": "owner-id", "email": "owner@example.org"})

    def test_invalid_assertions_fail_closed(self):
        now = int(time.time())
        bad = [token(key=OTHER_KEY), token(iss="https://other.cloudflareaccess.com"),
               token(iat=now - 900, exp=now - 600), token(sub=None), token(sub=" "), "", "not-a-jwt",
               jwt.encode({"iss": ISSUER, "sub": "x", "iat": now, "exp": now + 60}, "s" * 32, algorithm="HS256")]
        for value in bad:
            with self.assertRaises(AccessError) as ctx:
                ACCESS.authorize(headers(Host="id-linkcite.majerus.us", Cf_Access_Jwt_Assertion=value), "10.0.0.2")
            self.assertEqual(ctx.exception.status, 403, value)
        with self.assertRaises(AccessError) as ctx:
            ACCESS.authorize(headers(Host="id-linkcite.majerus.us",
                                     Cf_Access_Jwt_Assertion=[token(), token()]), "10.0.0.2")
        self.assertEqual(ctx.exception.status, 403)

    def test_unconfigured_or_malformed(self):
        for domain in (None, "", "http://team.cloudflareaccess.com", "https://team.cloudflareaccess.com/path",
                       "https://user@team.cloudflareaccess.com", "https://[team.cloudflareaccess.com",
                       "https://team.cloudflareaccess.com:notaport"):
            access = Access(domain, [], keys=lambda _: KEY.public_key())
            with self.assertRaises(AccessError) as ctx:
                access.authorize(headers(Host="x.majerus.us", Cf_Access_Jwt_Assertion=token()), "10.0.0.2")
            self.assertEqual(ctx.exception.status, 503)
        for host in (None, "", "a b", "host/path", "user@host", "host:notaport"):
            with self.assertRaises(AccessError) as ctx:
                ACCESS.authorize(headers(**({"Host": host} if host is not None else {})), "10.0.0.2")
            self.assertEqual(ctx.exception.status, 400, host)

    def test_private_peers(self):
        for address in ("10.1.2.3", "127.0.0.1", "172.16.0.1", "192.168.0.1", "169.254.1.1", "::1",
                        "::ffff:192.168.1.1", "fd00::1", "fe80::1"):
            self.assertTrue(private_peer(address), address)
        for address in ("8.8.8.8", "172.32.0.1", "100.64.0.1", "2001:db8::1", "", "localhost"):
            self.assertFalse(private_peer(address), address)


class FakePool:
    def __init__(self, rows=()):
        self.rows = list(rows)

    @contextmanager
    def connection(self, timeout=None):
        yield self

    def execute(self, sql, params=()):
        self.last = (sql, params)
        return self

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def state(revision=3, draft="Draft", private="Secret", published=None):
    return {"iri": IRI, "draft_markdown": draft, "private_markdown": private, "published_markdown": published,
            "revision_number": revision, "created_at": None, "updated_at": None, "published_at": None}


def context(pool=None, current=None, changes=None):
    def change(conn, body, actor):
        changes.append((body, actor))
        return state(revision=body["revision_number"] + 1, draft=body["draft_markdown"],
                     private=body["private_markdown"])
    return mcp_tools.Context(pool or FakePool(), "mcp (owner@example.org)", lambda: REGISTRY,
                             lambda: {IRI: {"definitions": ["A definition."]}}, server.render_markdown,
                             lambda conn, iri: current or state(), change)


def rpc(method, params=None, id_=1, ctx=None):
    message = {"jsonrpc": "2.0", "method": method, **({"id": id_} if id_ is not None else {}),
               **({"params": params} if params is not None else {})}
    return mcp_tools.handle(message, lambda: ctx or context(), {"name": "id-linkcite", "version": "0"}, server.Problem)


def call(name, ctx=None, **args):
    return rpc("tools/call", {"name": name, "arguments": args}, ctx=ctx)["result"]


class ProtocolTests(unittest.TestCase):
    def test_initialize_negotiates_the_version(self):
        result = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("tools", result["capabilities"])
        self.assertIn("save_draft", result["instructions"])
        self.assertEqual(rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"],
                         mcp_tools.PROTOCOL_VERSIONS[0])

    def test_tools_offer_no_publishing(self):
        names = {t["name"] for t in rpc("tools/list")["result"]["tools"]}
        self.assertEqual(names, {"search_resources", "get_content", "list_revisions", "get_revision",
                                 "preview_markdown", "save_draft"})
        self.assertEqual(rpc("tools/call", {"name": "publish", "arguments": {}})["error"]["code"], -32602)
        for name in (["save_draft"], {"x": 1}, None, 5):
            self.assertEqual(rpc("tools/call", {"name": name, "arguments": {}})["error"]["code"], -32602, name)

    def test_notifications_and_errors(self):
        self.assertIsNone(rpc("notifications/initialized", id_=None))
        self.assertEqual(rpc("ping")["result"], {})
        self.assertEqual(rpc("resources/list")["error"]["code"], -32601)
        bad = mcp_tools.handle([{"jsonrpc": "2.0", "id": 1, "method": "ping"}], context, {}, server.Problem)
        self.assertEqual(bad["error"]["code"], -32600)
        self.assertEqual(rpc("ping", id_=True)["error"]["code"], -32600)


class ToolTests(unittest.TestCase):
    def test_save_draft_keeps_private_notes_and_records_mcp(self):
        changes = []
        result = call("save_draft", ctx=context(changes=changes), iri=IRI, revision_number=3,
                      draft_markdown="New text", change_note="Expanded")
        self.assertNotIn("isError", result)
        body, actor = changes[0]
        self.assertEqual((body["action"], body["private_markdown"], body["revision_number"]), ("save", "Secret", 3))
        self.assertEqual(actor, "mcp (owner@example.org)")
        self.assertTrue(result["structuredContent"]["saved"])
        self.assertNotIn("private_notes", result["structuredContent"])

    def test_save_draft_refuses_a_stale_revision(self):
        changes = []
        result = call("save_draft", ctx=context(changes=changes), iri=IRI, revision_number=2, draft_markdown="x")
        self.assertTrue(result["isError"])
        self.assertIn("revision 3", result["content"][0]["text"])
        self.assertEqual(changes, [])

    def test_stale_race_inside_change_is_reported(self):
        def change(conn, body, actor):
            raise server.Problem(409, "Someone changed this resource. Reload before saving.")
        ctx = context()
        ctx.change = change
        result = call("save_draft", ctx=ctx, iri=IRI, revision_number=3, draft_markdown="x")
        self.assertTrue(result["isError"])
        self.assertIn("Someone changed", result["content"][0]["text"])

    def test_unknown_iri_and_bad_arguments(self):
        for args in ({"iri": "https://id.linkcite.org/dehumanization/patterns/missing"}, {"iri": 5}, {}):
            self.assertTrue(call("get_content", **args)["isError"])
        self.assertTrue(call("get_content", iri=IRI, include_private_notes="yes")["isError"])
        self.assertTrue(call("save_draft", iri=IRI, revision_number="3", draft_markdown="x")["isError"])
        self.assertTrue(call("search_resources", status="draft")["isError"])
        self.assertTrue(rpc("tools/call", {"name": "get_content", "arguments": []})["result"]["isError"])

    def test_get_content_hides_private_notes_unless_asked(self):
        content = call("get_content", iri=IRI)["structuredContent"]
        self.assertNotIn("private_notes", content)
        self.assertEqual(content["canonical_rdf_text"], {"definitions": ["A definition."]})
        self.assertTrue(content["pending"])
        self.assertEqual(call("get_content", iri=IRI, include_private_notes=True)["structuredContent"]["private_notes"],
                         "Secret")

    def test_search_filters_by_words_and_status(self):
        pool = FakePool([{"iri": IRI, "revision_number": 2, "published": True, "pending": False}])
        found = call("search_resources", ctx=context(pool), query="example PATTERN")["structuredContent"]
        self.assertEqual([r["iri"] for r in found["results"]], [IRI])
        unpublished = call("search_resources", ctx=context(pool), status="unpublished")["structuredContent"]
        self.assertEqual([r["label"] for r in unpublished["results"]], ["Other"])

    def test_preview_is_sanitized(self):
        html = call("preview_markdown", markdown="## Hi\n\n<script>x</script>")["structuredContent"]["html"]
        self.assertIn("<h2>Hi</h2>", html)
        self.assertNotIn("<script", html)


class HTTPTests(unittest.TestCase):
    """The admin handler end to end on loopback, as a LAN client and through 'Cloudflare'."""
    @classmethod
    def setUpClass(cls):
        cls.server = server.Server(("127.0.0.1", 0), server.Handler)
        cls.server.mode, cls.server.pool, cls.server.actor = "admin", FakePool([state()]), "editor"
        cls.server.access = Access(ISSUER, [], keys=lambda _: KEY.public_key())
        cls.server.admin_url = "http://id-linkcite.localdomain:8000/admin"
        cls.port = cls.server.server_address[1]
        cls.patch = patch.object(server, "resources", return_value=REGISTRY)
        cls.patch.start()
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.patch.stop()

    def request(self, method, path, body=None, **extra):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = {"Host": f"localhost:{self.port}", **{k.replace("_", "-"): v for k, v in extra.items()}}
        if body is not None:
            body = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body, hdrs)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

    def test_health_and_admin_on_the_lan(self):
        response, data = self.request("GET", "/health")
        self.assertEqual(response.status, 200)
        value = json.loads(data)
        self.assertTrue(value["ok"])
        self.assertEqual(value["dependencies"], [{"name": "postgresql", "ok": True}])
        response, data = self.request("GET", "/admin")
        self.assertEqual(response.status, 200)
        self.assertIn(b"/admin/admin.js", data)
        self.assertEqual(self.request("GET", "/")[0].getheader("Location"), "/admin")

    def test_mcp_over_http(self):
        response, data = self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": 7, "method": "tools/list"},
                                      Accept="application/json, text/event-stream")
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(data)["id"], 7)
        response, _ = self.request("POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(response.status, 202)
        self.assertEqual(self.request("GET", "/mcp")[0].status, 405)
        # A cross-site page cannot post a simple (non-JSON) request, or one from another origin.
        self.assertEqual(self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                      Content_Type="text/plain")[0].status, 403)
        self.assertEqual(self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                      Origin="https://evil.example")[0].status, 403)
        self.assertEqual(self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                      MCP_Protocol_Version="1999-01-01")[0].status, 400)

    def test_through_cloudflare_needs_access(self):
        self.assertEqual(self.request("GET", "/health", CF_Ray="abc")[0].status, 401)
        self.assertEqual(self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                      Host="id-linkcite.majerus.us", CF_Ray="abc")[0].status, 401)
        response, data = self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                      Host="id-linkcite.majerus.us", CF_Ray="abc",
                                      Cf_Access_Jwt_Assertion=token())
        self.assertEqual((response.status, json.loads(data)["result"]), (200, {}))
        self.assertEqual(self.request("GET", "/api/resources", Host="id-linkcite.majerus.us",
                                      Cf_Access_Jwt_Assertion=token(key=OTHER_KEY))[0].status, 403)

    def test_editor_sets_the_reader_hint_only_on_linkcite_org(self):
        response, _ = self.request("GET", "/admin", Host="id-editorial.linkcite.org", CF_Ray="abc",
                                   Cf_Access_Jwt_Assertion=token())
        self.assertEqual(response.status, 200)
        cookie = response.getheader("Set-Cookie")
        self.assertTrue(cookie.startswith("linkcite_editor=1; Domain=linkcite.org;"), cookie)
        self.assertIn("Secure", cookie)
        self.assertNotIn("HttpOnly", cookie)  # reader pages read it from script
        self.assertIsNone(self.request("GET", "/admin")[0].getheader("Set-Cookie"))
        self.assertIsNone(self.request("GET", "/admin/admin.js", Host="id-editorial.linkcite.org",
                                       Cf_Access_Jwt_Assertion=token())[0].getheader("Set-Cookie"))

    def test_editor_writes_need_same_origin(self):
        body = {"markdown": "*x*"}
        self.assertEqual(self.request("POST", "/api/preview", body, X_LinkCite_Editor="1")[0].status, 403)
        response, data = self.request("POST", "/api/preview", body, X_LinkCite_Editor="1",
                                      Origin=f"http://localhost:{self.port}")
        self.assertEqual((response.status, json.loads(data)["html"]), (200, "<p><em>x</em></p>"))


if __name__ == "__main__":
    unittest.main()
