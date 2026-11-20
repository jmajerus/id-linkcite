#!/usr/bin/env python3
"""Collect the public view of each vocabulary into build/public.nq, one named graph per vocabulary.

    python scripts/build.py                 # labels, links, definitions, scope notes and descriptions

Each vocabulary's sources (vocabularies.json) are read from sibling repositories and filtered:

- Only subjects under the vocabulary's own path (https://id.linkcite.org/<path>) are kept, and blank nodes are dropped.
- A link to another IRI is kept, except the predicates in PRIVATE_LINKS.
- A literal is kept only for the predicates in PUBLIC_TEXT or DEFINITIONS.
  Anything else, such as source text, page locators and editorial notes, never leaves its repository.

A new property therefore stays private until it is added here on purpose.
"""
import argparse
import html
import json
import subprocess
import sys
from pathlib import Path

from rdflib import Dataset, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, SKOS

from pages import write_pages

ROOT = Path(__file__).resolve().parent.parent
PC = Namespace('https://id.linkcite.org/patterns/vocab#')
TERM = 'urn:terminology-mcp:vocab:'

PUBLIC_TEXT = {RDFS.label, SKOS.prefLabel, SKOS.altLabel, SKOS.notation, DCTERMS.title, OWL.versionInfo,
               PC.slug, PC.status, PC.recoveryStatus, PC.termKey, PC.vocabularyName, PC.domainKey, PC.domainStatus}
DEFINITIONS = {SKOS.definition, SKOS.scopeNote, DCTERMS.description, RDFS.comment}
PRIVATE_LINKS = {DCTERMS.source, PC.cites}


def public(graph, prefix):
    text = PUBLIC_TEXT | DEFINITIONS
    for s, p, o in graph:
        if not (isinstance(s, URIRef) and (str(s) == prefix or str(s).startswith((prefix + '/', prefix + '#')))):
            continue
        if str(p).startswith(TERM):
            continue
        if isinstance(o, URIRef):
            if p not in PRIVATE_LINKS:
                yield s, p, o
        elif isinstance(o, Literal) and p in text:
            yield s, p, o


def source_commit(path):
    try:
        return subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=path.parent, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def welcome(manifest):
    """The home page: one card per published vocabulary. Links are relative, so they work locally too."""
    base = manifest['base']
    cards = ''.join(
        f'''
    <li class="trifid-card">
      <h3><a href="/{html.escape(v['home'][len(base):])}">{html.escape(v['title'])}</a></h3>
      <p><code>{html.escape(base + v['path'])}/</code> · {v['resources']} resources</p>
    </li>''' for v in manifest['vocabularies'])
    return f'''<div class="trifid-header-title">
  <div class="trifid-content">
    <div class="container">
      <h1>id.linkcite.org</h1>
      <p>Persistent identifiers for the terms of <cite>The Struggle Against Dehumanization</cite> and related
      vocabularies. Identifiers are provisional during pre-1.0 development and become permanent when included
      in a formally published ontology release at version 1.0 or later.</p>
      <p>Each IRI resolves here to its available definitions, codes and links: as a page in a browser, or as Turtle,
      JSON-LD or N-Triples by content negotiation. Reader pages may also include editorial commentary.</p>
    </div>
  </div>
</div>

<div class="trifid-content">
  <h2>Vocabularies</h2>
  <ul class="trifid-card-grid">{cards}
  </ul>

  <h2>Query</h2>
  <ul class="trifid-link-list">
    <li><a href="/sparql/">SPARQL editor</a> (read-only; the endpoint is <code>/query</code>)</li>
  </ul>
</div>
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--definitions', action='store_true', help=argparse.SUPPRESS)  # legacy flag; always included
    parser.add_argument('--out', type=Path, default=ROOT / 'build')
    args = parser.parse_args()
    config = json.loads((ROOT / 'vocabularies.json').read_text())
    base = config['base'].rstrip('/')

    dataset = Dataset()
    manifest = {'base': base + '/', 'definitions': True, 'vocabularies': []}
    for vocab in config['vocabularies']:
        if not vocab.get('publish', True):
            print(f"{vocab['path']}: not published (\"publish\": false)")
            continue
        prefix = f"{base}/{vocab['path']}"
        source = Graph()
        sources = []
        for name in vocab['sources']:
            path = (ROOT / name).resolve()
            if not path.exists():
                sys.exit(f"{vocab['path']}: missing {path}. {vocab.get('build', '')}".strip())
            source.parse(path)
            sources.append({'file': name, 'commit': source_commit(path)})
        graph = dataset.graph(URIRef(prefix))
        for triple in public(source, prefix):
            graph.add(triple)
        # Hash IRIs (classes and properties) are described by their document, the part before the #.
        for s in set(graph.subjects()):
            if '#' in str(s):
                graph.add((s, RDFS.isDefinedBy, URIRef(str(s).split('#')[0])))
        documents = {o for o in graph.objects(None, RDFS.isDefinedBy)}
        for document in documents:
            if (document, None, None) not in graph:
                graph.add((document, RDFS.label, Literal(f"{vocab['title']} vocabulary", lang='en')))
        subjects = len(set(graph.subjects()))
        print(f"{vocab['path']}: {len(graph)} of {len(source)} triples, {subjects} resources")
        manifest['vocabularies'].append({'path': vocab['path'], 'title': vocab['title'], 'home': vocab['home'],
                                         'triples': len(graph), 'resources': subjects, 'sources': sources})

    args.out.mkdir(parents=True, exist_ok=True)
    # Sorted, so an unchanged build gives an unchanged file.
    lines = sorted(set(dataset.serialize(format='nquads').splitlines()) - {''})
    (args.out / 'public.nq').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    (args.out / 'welcome.hbs').write_text(welcome(manifest), encoding='utf-8')
    pages = write_pages(dataset, base + '/', args.out / 'pages')
    print(f'Wrote {len(pages)} reader pages')
    print(f"Wrote {args.out / 'public.nq'}: {len(lines)} quads, including available definitions")


if __name__ == '__main__':
    main()
