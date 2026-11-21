"""Optional integration tests against a disposable database with migration 001 applied.

Set LINKCITE_TEST_EDITOR_DSN and LINKCITE_TEST_READER_DSN. Never point at production.
Tests roll back all editorial writes; public view checks use separate connections.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "editorial_service"))
from server import change, Problem, PublishedHTMLCache

IRI = "https://id.linkcite.org/patterns/test-editorial-integration"

@unittest.skipUnless(os.environ.get("LINKCITE_TEST_EDITOR_DSN") and
                     os.environ.get("LINKCITE_TEST_READER_DSN"), "disposable PostgreSQL DSNs not set")
class DatabaseTests(unittest.TestCase):
    def test_edits_history_and_restore(self):
        with psycopg.connect(os.environ["LINKCITE_TEST_EDITOR_DSN"], row_factory=dict_row) as conn:
            # Start an outer transaction so change() uses a savepoint, not a commit.
            conn.execute("BEGIN")
            try:
                with patch("server.resources", return_value={IRI: "Test"}):
                    body = {"iri": IRI, "revision_number": 0, "action": "save",
                            "draft_markdown": "**Public**", "private_markdown": "Secret notes"}
                    first = change(conn, body, "test")
                    self.assertEqual(first["revision_number"], 1)
                    self.assertIsNone(first["published_markdown"])
                    self.assertEqual(change(conn, {**body, "revision_number": 1}, "test")["revision_number"], 1)
                    second = change(conn, {**body, "revision_number": 1, "action": "publish"}, "test")
                    self.assertEqual(second["published_markdown"], "**Public**")
                    third = change(conn, {**body, "revision_number": 2, "private_markdown": "More private"}, "test")
                    self.assertEqual(third["published_at"], second["published_at"])
                    fourth = change(conn, {**body, "revision_number": 3, "action": "restore", "restore_revision": 1}, "test")
                    self.assertEqual(fourth["revision_number"], 4)
                    self.assertEqual(fourth["private_markdown"], "Secret notes")
                    self.assertEqual(fourth["published_markdown"], "**Public**")
                    with self.assertRaises(Problem) as ctx:
                        change(conn, body, "test")
                    self.assertEqual(ctx.exception.status, 409)
                    self.assertEqual(conn.execute("SELECT count(*) AS n FROM editorial.resource_revision WHERE iri=%s",(IRI,)).fetchone()["n"],4)
            finally:
                conn.rollback()

    def test_editor_cannot_bypass_transition_function(self):
        with psycopg.connect(os.environ["LINKCITE_TEST_EDITOR_DSN"], row_factory=dict_row) as conn:
            statements = [
                ("UPDATE editorial.resource_content SET published_markdown=%s WHERE iri=%s", ("<script>alert(1)</script>", IRI)),
                ("UPDATE editorial.resource_content SET published_at=now() WHERE iri=%s", (IRI,)),
                ("UPDATE editorial.resource_content SET iri=%s WHERE iri=%s", (IRI + "-other", IRI)),
                ("UPDATE editorial.resource_content SET draft_markdown='' WHERE iri=%s", (IRI,)),
                ("INSERT INTO editorial.resource_content(iri,published_markdown,published_at) VALUES (%s,'injected',now())", (IRI,)),
                ("DELETE FROM editorial.resource_content WHERE iri=%s", (IRI,)),
                ("UPDATE editorial.resource_revision SET private_markdown='' WHERE iri=%s", (IRI,)),
                ("DELETE FROM editorial.resource_revision WHERE iri=%s", (IRI,)),
                ("INSERT INTO editorial.resource_revision(iri,revision_number,action,draft_markdown,private_markdown,changed_by) VALUES (%s,1,'save','','','test')", (IRI,)),
            ]
            for query, params in statements:
                with self.subTest(query=query), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with conn.transaction():
                        conn.execute(query, params)
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema='editorial' "
                "AND table_name='resource_content' AND column_name='published_html'").fetchone())

    def test_mcp_save_draft_keeps_notes_and_never_publishes(self):
        import mcp_tools
        from contextlib import contextmanager
        from server import read_state, render_markdown
        with psycopg.connect(os.environ["LINKCITE_TEST_EDITOR_DSN"], row_factory=dict_row) as conn:
            conn.execute("BEGIN")
            try:
                class Pool:
                    @contextmanager
                    def connection(self, timeout=None):
                        yield conn
                registry = {IRI: "Test"}
                with patch("server.resources", return_value=registry):
                    change(conn, {"iri": IRI, "revision_number": 0, "action": "publish",
                                  "draft_markdown": "Live", "private_markdown": "Notes"}, "test")
                    ctx = mcp_tools.Context(Pool(), "mcp (test)", lambda: registry, dict,
                                            render_markdown, read_state, change)
                    saved = mcp_tools.save_draft(ctx, {"iri": IRI, "revision_number": 1, "draft_markdown": "Proposed"})
                    self.assertEqual((saved["revision_number"], saved["published_markdown"]), (2, "Live"))
                    self.assertTrue(saved["pending"])
                    row = read_state(conn, IRI)
                    self.assertEqual((row["draft_markdown"], row["private_markdown"]), ("Proposed", "Notes"))
                    last = mcp_tools.list_revisions(ctx, {"iri": IRI})["revisions"][0]
                    self.assertEqual((last["action"], last["changed_by"]), ("save", "mcp (test)"))
                    with self.assertRaises(mcp_tools.ToolError):
                        mcp_tools.save_draft(ctx, {"iri": IRI, "revision_number": 1, "draft_markdown": "Stale"})
            finally:
                conn.rollback()

    def test_reader_cannot_read_private_tables(self):
        with psycopg.connect(os.environ["LINKCITE_TEST_READER_DSN"], row_factory=dict_row) as conn:
            conn.execute("SELECT iri,document_iri,published_markdown FROM editorial.public_content LIMIT 1")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT private_markdown FROM editorial.resource_content LIMIT 1")
            conn.rollback()
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT private_markdown FROM editorial.resource_revision LIMIT 1")
            conn.rollback()
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT editorial.change_resource(%s,0,'save','','',NULL,'test',NULL)", (IRI,))
            conn.rollback()


class CacheTests(unittest.TestCase):
    def test_untrusted_published_markdown_is_sanitized_and_cached(self):
        cache = PublishedHTMLCache(max_bytes=4096, max_entries=2)
        source = '<img src="/a.png" onerror="alert(1)">\n\n[bad](javascript:alert(1))'
        html = cache.render(source)
        self.assertNotIn("onerror=", html)
        self.assertNotIn("javascript:", html)
        with patch("server.render_markdown", side_effect=AssertionError("cache miss")):
            self.assertEqual(cache.render(source), html)
        self.assertIn("new text", cache.render("new text"))
        cache.render("third entry")
        self.assertNotIn(source, cache.entries)
        self.assertLessEqual(cache.size, cache.max_bytes)

if __name__ == "__main__":
    unittest.main()
