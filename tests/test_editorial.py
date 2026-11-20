import sys
import tempfile
import unittest
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, RDFS, SKOS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from editorial import load_snippets, resource_text, render_markdown
from build import public
from pages import environment

BASE = "https://id.linkcite.org/"
NODE = URIRef(BASE + "patterns/example")
PC = Namespace(BASE + "patterns/vocab#")


class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph()
        self.graph.add((NODE, SKOS.prefLabel, Literal("Example", lang="en")))
        self.graph.add((NODE, SKOS.definition, Literal("Exact definition <b>literal</b>", lang="en")))
        self.graph.add((NODE, DCTERMS.description, Literal("Exact definition <b>literal</b>", lang="en")))
        self.graph.add((NODE, SKOS.scopeNote, Literal("A scope note")))
        self.graph.add((NODE, SKOS.definition, Literal("French text", lang="fr")))
        self.graph.add((NODE, PC.bodyMarkdown, Literal("PRIVATE BODY")))
        self.graph.add((NODE, PC.editorialNote, Literal("PRIVATE NOTE")))

    def test_filter_and_reader_text(self):
        filtered = Graph()
        for triple in public(self.graph, BASE + "patterns"):
            filtered.add(triple)
        self.assertIn((NODE, SKOS.scopeNote, Literal("A scope note")), filtered)
        self.assertFalse(list(filtered.objects(NODE, PC.bodyMarkdown)))
        self.assertFalse(list(filtered.objects(NODE, PC.editorialNote)))
        ctx = resource_text(filtered, str(NODE))
        self.assertEqual(ctx["definitions"], ["Exact definition <b>literal</b>"])
        self.assertEqual(ctx["descriptions"], [])
        self.assertEqual(ctx["scope_notes"], ["A scope note"])
        html = environment().get_template("_resource-content.html.j2").render(
            **ctx, editorial="## Extra\n\n**An example.**")
        self.assertIn("&lt;b&gt;literal&lt;/b&gt;", html)
        self.assertIn("<strong>An example.</strong>", html)
        self.assertNotIn("PRIVATE", html)

    def test_empty_content(self):
        ctx = resource_text(Graph(), str(NODE))
        html = environment().get_template("_resource-content.html.j2").render(**ctx, editorial="")
        self.assertFalse(html.strip())

    def test_editorial_sanitization(self):
        html = str(render_markdown('## Heading\n\n[bad](javascript:alert(1))\n\n'
                                   '<img src="/assets/example.png" onerror="alert(1)">'
                                   '<script>alert(1)</script>'))
        self.assertIn("<h2>Heading</h2>", html)
        self.assertIn('src="/assets/example.png"', html)
        self.assertNotIn("javascript:", html)
        self.assertNotIn("onerror=", html)
        self.assertNotIn("<script", html)

    def test_slash_and_hash_mapping(self):
        doc = URIRef(BASE + "patterns/vocab")
        term = URIRef(str(doc) + "#AntiPattern")
        self.graph.add((doc, RDFS.label, Literal("Vocabulary")))
        self.graph.add((term, RDFS.label, Literal("Anti-pattern")))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            sub = root / "patterns"
            sub.mkdir()
            (sub / "example.md").write_text("Example prose", encoding="utf-8")
            (sub / "vocab#AntiPattern.md").write_text("Fragment prose", encoding="utf-8")
            self.assertEqual(load_snippets(root, self.graph, BASE),
                             {str(NODE): "Example prose", str(term): "Fragment prose"})
            (sub / "unknown.md").write_text("Orphan", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match a public IRI"):
                load_snippets(root, self.graph, BASE)

if __name__ == "__main__":
    unittest.main()
