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
from server import change, Problem

IRI = "https://id.linkcite.org/patterns/test-editorial-integration"

@unittest.skipUnless(os.environ.get("LINKCITE_TEST_EDITOR_DSN") and
                     os.environ.get("LINKCITE_TEST_READER_DSN"), "disposable PostgreSQL DSNs not set")
class DatabaseTests(unittest.TestCase):
    def test_edits_history_and_restore(self):
        with psycopg.connect(os.environ["LINKCITE_TEST_EDITOR_DSN"], row_factory=dict_row) as conn:
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

    def test_reader_cannot_read_private_tables(self):
        with psycopg.connect(os.environ["LINKCITE_TEST_READER_DSN"], row_factory=dict_row) as conn:
            conn.execute("SELECT iri,document_iri,published_html FROM editorial.public_content LIMIT 1")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT private_markdown FROM editorial.resource_content LIMIT 1")
            conn.rollback()
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT private_markdown FROM editorial.resource_revision LIMIT 1")
            conn.rollback()

if __name__ == "__main__":
    unittest.main()
