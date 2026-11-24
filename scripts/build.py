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
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
import subprocess
import sys
from pathlib import Path

from rdflib import Dataset, Graph, Literal, Namespace, URIRef
from rdflib.util import guess_format
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, SKOS

from pages import write_pages
from source_state import source_state

ROOT = Path(__file__).resolve().parent.parent
PC = Namespace('https://id.linkcite.org/dehumanization/patterns/vocab#')
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


def copy_texts(config, out):
    """Copy each work's published texts (vocabularies.json "texts") into build/text/<work>/, fingerprinted.

    They are served at /<work>/text/<file>. The folder is rebuilt from scratch, so a text dropped from
    the configuration also leaves the release.
    """
    shutil.rmtree(out, ignore_errors=True)
    texts = []
    for work in config.get('works', []):
        for text in work.get('texts', []):
            source = (ROOT / text['source']).resolve()
            if not source.is_file():
                sys.exit(f"{work['path']}: missing {source}. Build it in the manuscript repository first.")
            data = source.read_bytes()
            target = out / work['path'] / text['file']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            built = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).date().isoformat()
            print(f"{work['path']}/text/{text['file']}: {len(data):,} bytes, built {built}")
            texts.append({'work': work['path'], 'file': text['source'], 'href': f"/{work['path']}/text/{text['file']}",
                          'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'built': built})
    return texts


def works(manifest, config):
    """Each work (vocabularies.json "works") with the published vocabularies under its path, for its page."""
    base = manifest['base']
    result = []
    for work in config.get('works', []):
        # 'short' names a vocabulary within its work ("Patterns"), from the last segment of its path.
        vocabularies = [{'href': '/' + v['home'][len(base):], 'title': v['title'], 'iri': base + v['path'],
                         'short': v['path'].rsplit('/', 1)[-1].replace('-', ' ').capitalize(),
                         'resources': v['resources']}
                        for v in manifest['vocabularies'] if v['path'].startswith(work['path'] + '/')]
        built = {t['href']: t for t in manifest.get('texts', [])}
        texts = [{**t, **built[f"/{work['path']}/text/{t['file']}"]} for t in work.get('texts', [])]
        if vocabularies:
            result.append({**work, 'href': '/' + work['path'], 'vocabularies': vocabularies, 'texts': texts})
    return result


def welcome(works):
    """The home page: general, with a card per work and shortcuts to its vocabularies; each work's page
    lists them in full. Links are relative, so they work locally too."""
    e = html.escape
    cards = ''.join(
        f'''
    <li class="trifid-card">
      <h3><a href="{e(w['href'])}">{e(w['title'])}</a></h3>
      <p>{e(w['description'])}</p>
      <p class="shortcuts">{' · '.join(f'<a href="{e(v["href"])}">{e(v["short"])}</a>' for v in w['vocabularies'])}</p>
    </li>''' for w in works)
    # Trifid's grid lets cards shrink to 250px, too narrow for the cards' text.
    return f'''<style>
  .trifid-card-grid {{ grid-template-columns: repeat(auto-fill, minmax(min(100%, 24rem), 1fr)); }}
  /* The title link covers the whole card (Trifid's h3 a::after); lift the shortcuts above it. */
  .trifid-card .shortcuts a {{ position: relative; z-index: 1; }}
</style>
<div class="trifid-header-title">
  <div class="trifid-content">
    <div class="container">
      <h1>id.linkcite.org</h1>
      <p>Persistent identifiers for the terms of published vocabularies, grouped by the work they come from.
      Identifiers are provisional during pre-1.0 development and become permanent when included in a formally
      published ontology release at version 1.0 or later.</p>
      <p>Each IRI resolves here to its available definitions, codes and links: as a page in a browser, or as Turtle,
      JSON-LD or N-Triples by content negotiation. Reader pages may also include editorial commentary.</p>
    </div>
  </div>
</div>

<div class="trifid-content">
  <h2>Works</h2>
  <ul class="trifid-card-grid">{cards}
  </ul>
  <p>Additional vocabularies will be added here.</p>

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
    # Captured before reading anything, including vocabularies.json, so edits made
    # during the build count as stale.
    site = source_state(ROOT)
    config = json.loads((ROOT / 'vocabularies.json').read_text())
    base = config['base'].rstrip('/')

    dataset = Dataset()
    manifest = {'base': base + '/', 'definitions': True, 'site': site, 'vocabularies': []}
    (args.out / 'manifest.json').unlink(missing_ok=True)  # no manifest until the build completes
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
            # Hash exactly the bytes parsed: the files may live in other, uncommitted checkouts.
            data = path.read_bytes()
            source.parse(data=data, format=guess_format(str(path)))
            sources.append({'file': name, 'commit': source_commit(path), 'sha256': hashlib.sha256(data).hexdigest()})
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
    manifest['texts'] = copy_texts(config, args.out / 'text')
    grouped = works(manifest, config)
    (args.out / 'welcome.hbs').write_text(welcome(grouped), encoding='utf-8')
    pages = write_pages(dataset, base + '/', args.out / 'pages', grouped)
    print(f'Wrote {len(pages)} reader pages')
    # Last: tools/deploy.py treats the manifest's site state as proof of a complete build.
    # Atomically, so an interrupted write never leaves a partial manifest.
    temporary = args.out / 'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, args.out / 'manifest.json')
    print(f"Wrote {args.out / 'public.nq'}: {len(lines)} quads, including available definitions")


if __name__ == '__main__':
    main()
