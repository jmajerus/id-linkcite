"""Resolve published editorial Markdown by IRI and select public RDF prose.

The source directory is an explicit publication allowlist: all .md files in it
are intended for the public site. Drafts belong elsewhere. No private graph is
read here, and editorial Markdown is not added to the RDF dataset.
"""
import re
from pathlib import Path

import bleach
import markdown
from markupsafe import Markup
from rdflib import Literal, URIRef
from rdflib.namespace import DCTERMS, RDF, RDFS, SKOS

SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]*")
TAGS = {"p", "h2", "h3", "h4", "h5", "h6", "strong", "em", "b", "i", "a",
        "ul", "ol", "li", "blockquote", "pre", "code", "hr", "br",
        "table", "thead", "tbody", "tr", "th", "td", "img", "sup", "sub",
        "dl", "dt", "dd", "del"}
ATTRS = {"a": ["href", "title"], "img": ["src", "alt", "title"],
         "ol": ["start"], "code": ["class"], "th": ["align"], "td": ["align"]}


def render_markdown(text):
    # The page shell owns h1. Drop executable HTML, unsafe URLs and event attributes.
    html = markdown.markdown(text, extensions=["extra", "sane_lists"])
    return Markup(bleach.clean(html, tags=TAGS, attributes=ATTRS,
                              protocols={"http", "https", "mailto"}, strip=True))


def load_snippets(root: Path, graph, base: str):
    """Map explicit published files to existing public resource IRIs.

    E.g. patterns/roach-motel.md and patterns/vocab#AntiPattern.md.
    Reject orphan files instead of silently publishing stale content.
    """
    found = {}
    if not root.exists():
        return found
    for file in sorted(root.rglob("*.md")):
        if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Unsafe editorial file: {file}")
        relative = file.relative_to(root).as_posix()[:-3]
        document, sep, fragment = relative.partition("#")
        if not all(SEGMENT.fullmatch(p) for p in document.split("/")) or (
                sep and not SEGMENT.fullmatch(fragment)):
            raise ValueError(f"Invalid editorial IRI path: {relative}")
        iri = base + relative
        if (URIRef(iri), None, None) not in graph:
            raise ValueError(f"Editorial file {file} does not match a public IRI: {iri}")
        if sep and (URIRef(base + document), None, None) not in graph:
            raise ValueError(f"Missing public document for {iri}")
        text = file.read_text(encoding="utf-8").strip()
        if text:
            found[iri] = text
    return found


def english_literals(graph, node, predicate):
    return sorted({str(v) for v in graph.objects(node, predicate)
                   if isinstance(v, Literal) and str(v).strip()
                   and (not v.language or v.language.lower().split("-")[0] == "en")})


def resource_text(graph, iri):
    """Select exact public English RDF literals, never invent or paraphrase prose."""
    node = URIRef(iri)
    seen = set()
    result = {}
    for key, predicate in (("definitions", SKOS.definition),
                           ("descriptions", DCTERMS.description),
                           ("comments", RDFS.comment),
                           ("scope_notes", SKOS.scopeNote)):
        values = [v for v in english_literals(graph, node, predicate) if v not in seen]
        result[key] = values
        seen.update(values)
    return result


def generic_context(graph, iri):
    node = URIRef(iri)
    def label(n):
        for predicate in (SKOS.prefLabel, RDFS.label, DCTERMS.title):
            values = english_literals(graph, n, predicate)
            if values:
                return values[0]
        return str(n).rsplit("#", 1)[-1].rsplit("/", 1)[-1]

    links = []
    for name, predicate in (("Type", RDF.type), ("Scheme", SKOS.inScheme),
                            ("Broader", SKOS.broader), ("Narrower", SKOS.narrower),
                            ("Related", SKOS.related), ("Member", SKOS.member),
                            ("Defined by", RDFS.isDefinedBy)):
        for other in sorted(set(graph.objects(node, predicate))):
            if isinstance(other, URIRef) and str(other).startswith(("https://", "http://")):
                links.append({"relation": name, "iri": str(other), "label": label(other)})
    return {"iri": iri, "label": label(node), "resource_links": links}
