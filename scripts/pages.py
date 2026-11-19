"""Reader-facing HTML pages, written into build/pages/ from the public dataset and site/content/.

Pages are generated only from what build.py has already filtered as public, plus the authored text in
site/content/. The pages plugin (trifid/plugins/pages.js) serves them to browsers; tools asking for RDF
still get Trifid's own responses.
"""
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, SKOS

ROOT = Path(__file__).resolve().parent.parent
PC = Namespace('https://id.linkcite.org/patterns/vocab#')


def md(text, inline=False):
    html = markdown.markdown(text.strip())
    if inline and html.startswith('<p>') and html.endswith('</p>') and html.count('<p>') == 1:
        html = html[3:-4]
    return Markup(html)


@dataclass
class Pattern:
    iri: str
    label: str
    kind: str  # 'anti', 'restorative' or 'cluster'
    provisional: bool
    answered_by: list = field(default_factory=list)  # restorative patterns that counter or interrupt this one
    answers: list = field(default_factory=list)      # anti-patterns this one counters or interrupts

    @property
    def href(self):
        return self.iri[len('https://id.linkcite.org'):]


def environment():
    env = Environment(loader=FileSystemLoader(ROOT / 'site/templates'), autoescape=select_autoescape(),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters['md'] = md
    env.filters['mdinline'] = lambda text: md(text, inline=True)
    return env


def patterns_overview(graph: Graph, base: str):
    content = tomllib.loads((ROOT / 'site/content/patterns.toml').read_text(encoding='utf-8'))
    root = base + 'patterns'
    scheme = URIRef(root + '/scheme')
    label = lambda node: str(graph.value(node, SKOS.prefLabel) or graph.value(node, RDFS.label) or node)

    patterns = {}
    for node in set(graph.subjects(SKOS.inScheme, scheme)):
        types = set(graph.objects(node, RDF.type))
        kind = 'cluster' if PC.PatternCluster in types else 'restorative' if PC.RestorativePattern in types else 'anti'
        status = str(graph.value(node, PC.recoveryStatus) or '')
        patterns[node] = Pattern(str(node), label(node), kind, provisional=bool(status) and status != 'manuscript-attested')
    by_label = lambda items: sorted(items, key=lambda p: p.label.lower())
    for restorative in patterns.values():
        node = URIRef(restorative.iri)
        targets = {t for p in (PC.counters, PC.interrupts) for t in graph.objects(node, p) if t in patterns}
        for target in targets:
            patterns[target].answered_by.append(restorative)
            restorative.answers.append(patterns[target])
    for p in patterns.values():
        p.answered_by, p.answers = by_label(p.answered_by), by_label(p.answers)

    members = lambda key: {m for m in graph.objects(URIRef(f'{root}/collections/{key}'), SKOS.member) if m in patterns}
    placed = set()
    sections = []
    for section in content['sections']:
        nodes = members(section['collection'])
        placed |= nodes
        sections.append({'id': section['collection'].removeprefix('section-'), 'numeral': section['numeral'],
                         'title': label(URIRef(f"{root}/collections/{section['collection']}")),
                         'blurb': section['blurb'], 'patterns': by_label(patterns[n] for n in nodes)})

    def clusters(kind):
        groups = []
        for node, cluster in sorted(patterns.items(), key=lambda item: item[1].label):
            if cluster.kind != 'cluster':
                continue
            narrower = [patterns[n] for n in graph.subjects(SKOS.broader, node) if n in patterns and patterns[n].kind == kind]
            if narrower:
                placed.update(URIRef(p.iri) for p in narrower)
                placed.add(node)
                slug = cluster.href.rsplit('/', 1)[1]
                groups.append({'id': slug, 'title': cluster.label, 'href': cluster.href,
                               'blurb': content['clusters'].get(slug, {}).get('blurb', ''), 'patterns': by_label(narrower)})
        return groups

    anti_clusters, restorative_clusters = clusters('anti'), clusters('restorative')
    rest = lambda kind: [p for n, p in patterns.items() if p.kind == kind and n not in placed]
    proposed = by_label(p for kind in ('anti', 'restorative') for p in rest(kind) if p.provisional)
    restorative = by_label(p for p in rest('restorative') if not p.provisional)
    leftover = [p for p in rest('anti') if not p.provisional]
    if leftover:
        raise SystemExit('Anti-patterns in no section or cluster: ' + ', '.join(p.label for p in leftover))

    counts = {kind: sum(p.kind == kind for p in patterns.values()) for kind in ('anti', 'restorative')}
    return 'patterns/scheme.html', 'patterns-overview.html.j2', {
        'content': content, 'iri': str(scheme), 'counts': counts, 'sections': sections,
        'anti_clusters': anti_clusters, 'restorative_clusters': restorative_clusters,
        'restorative': restorative, 'proposed': proposed,
        'version': str(graph.value(URIRef(root), URIRef('http://www.w3.org/2002/07/owl#versionInfo')) or ''),
    }


def write_pages(dataset, base, out: Path):
    env = environment()
    graph = dataset.graph(URIRef(base + 'patterns'))
    written = []
    for path, template, context in [patterns_overview(graph, base)]:
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(env.get_template(template).render(**context), encoding='utf-8')
        written.append(path)
    return written
