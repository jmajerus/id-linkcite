import sys
import unittest
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, RDFS, SKOS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from editorial import reader_resources, editorial_marker, resource_text, render_markdown
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
            **ctx, editorial_iri=str(NODE))
        self.assertIn("&lt;b&gt;literal&lt;/b&gt;", html)
        self.assertIn(str(editorial_marker(str(NODE))), html)
        self.assertNotIn("PRIVATE", html)

    def test_empty_content(self):
        ctx = resource_text(Graph(), str(NODE))
        html = environment().get_template("_resource-content.html.j2").render(**ctx, editorial_iri=str(NODE))
        marker, link = html.strip().split("\n")
        self.assertEqual(marker, str(editorial_marker(str(NODE))))
        # Hidden for visitors; the editor's hint cookie reveals it. Fragment IRIs keep their #.
        self.assertEqual(link, '<p class="edit-link" hidden><a href="https://id-editorial.linkcite.org/admin'
                               '?iri=https%3A//id.linkcite.org/patterns/example">Edit</a></p>')
        fragment = environment().get_template("_resource-content.html.j2").render(
            **ctx, editorial_iri=BASE + "patterns/vocab#AntiPattern")
        self.assertIn("?iri=https%3A//id.linkcite.org/patterns/vocab%23AntiPattern", fragment)

    def test_editorial_sanitization(self):
        html = str(render_markdown('## Heading\n\n[bad](javascript:alert(1))\n\n'
                                   '<img src="/assets/example.png" onerror="alert(1)">'
                                   '<script>alert(1)</script>'))
        self.assertIn("<h2>Heading</h2>", html)
        self.assertIn('src="/assets/example.png"', html)
        self.assertNotIn("javascript:", html)
        self.assertNotIn("onerror=", html)
        self.assertNotIn("<script", html)

    def test_slash_and_hash_registry(self):
        doc = URIRef(BASE + "patterns/vocab")
        term = URIRef(str(doc) + "#AntiPattern")
        self.graph.add((doc, RDFS.label, Literal("Vocabulary")))
        self.graph.add((term, RDFS.label, Literal("Anti-pattern")))
        self.graph.add((URIRef("https://elsewhere.example/test"), RDFS.label, Literal("external")))
        self.assertEqual(reader_resources(self.graph, BASE),
                         {str(NODE): "Example", str(doc): "Vocabulary", str(term): "Anti-pattern"})
        self.assertNotEqual(editorial_marker(str(NODE)), editorial_marker(str(term)))

if __name__ == "__main__":
    unittest.main()
