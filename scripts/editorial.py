import re
from base64 import urlsafe_b64encode

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


def editorial_marker(iri):
    """Opaque marker to be replaced once by the public reader plugin."""
    encoded = urlsafe_b64encode(iri.encode("utf-8")).decode("ascii")
    return Markup(f"<!--LINKCITE-EDITORIAL:{encoded}-->")


def reader_resources(graph, base):
    resources = {}
    for node in set(graph.subjects()):
        iri = str(node)
        if not isinstance(node, URIRef) or not iri.startswith(base):
            continue
        document, _, fragment = iri[len(base):].partition("#")
        if not all(SEGMENT.fullmatch(p) for p in document.split("/")) or (
                fragment and not SEGMENT.fullmatch(fragment)):
            continue
        resources[iri] = generic_context(graph, iri)["label"]
    return resources


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
