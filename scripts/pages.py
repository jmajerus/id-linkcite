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
from rdflib.namespace import OWL, RDF, RDFS, SKOS

ROOT = Path(__file__).resolve().parent.parent
SITE = 'https://id.linkcite.org'
PC = Namespace(SITE + '/patterns/vocab#')


def md(text, inline=False):
    html = markdown.markdown(text.strip())
    if inline and html.startswith('<p>') and html.endswith('</p>') and html.count('<p>') == 1:
        html = html[3:-4]
    return Markup(html)


def path_of(iri):
    return str(iri)[len(SITE):]


@dataclass
class Tag:
    iri: str
    label: str

    @property
    def href(self):
        return path_of(self.iri)


@dataclass(eq=False)
class Pattern:
    iri: str
    label: str
    kind: str  # 'anti', 'restorative' or 'cluster'
    provisional: bool
    group: dict | None = None                         # where it is listed first on the overview
    groups: list = field(default_factory=list)       # every group that lists it
    cluster: 'Pattern | None' = None
    members: list = field(default_factory=list)      # a cluster's patterns
    answered_by: list = field(default_factory=list)  # restorative patterns that counter or interrupt this one
    counters: list = field(default_factory=list)     # anti-patterns this one counters
    interrupts: list = field(default_factory=list)   # anti-patterns this one interrupts
    related: list = field(default_factory=list)
    contexts: list = field(default_factory=list)
    functions: list = field(default_factory=list)
    counter_tags: list = field(default_factory=list)

    @property
    def href(self):
        return path_of(self.iri)

    @property
    def slug(self):
        return self.href.rsplit('/', 1)[1]

    @property
    def answers(self):
        return sorted({*self.counters, *self.interrupts}, key=sort_key)


sort_key = lambda item: item.label.lower()


def environment():
    env = Environment(loader=FileSystemLoader(ROOT / 'site/templates'), autoescape=select_autoescape(),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters['md'] = md
    env.filters['mdinline'] = lambda text: md(text, inline=True)
    return env


class Catalog:
    """The patterns vocabulary as readers see it: patterns, their groups on the overview, and their links."""

    def __init__(self, graph: Graph):
        self.graph = graph
        self.content = tomllib.loads((ROOT / 'site/content/patterns.toml').read_text(encoding='utf-8'))
        self.root = SITE + '/patterns'
        self.scheme = URIRef(self.root + '/scheme')
        g, label = graph, self.label

        self.patterns = {}
        for node in set(g.subjects(SKOS.inScheme, self.scheme)):
            types = set(g.objects(node, RDF.type))
            kind = 'cluster' if PC.PatternCluster in types else 'restorative' if PC.RestorativePattern in types else 'anti'
            status = str(g.value(node, PC.recoveryStatus) or '')
            self.patterns[node] = Pattern(str(node), label(node), kind, provisional=bool(status) and status != 'manuscript-attested')
        known = lambda nodes: sorted((self.patterns[n] for n in set(nodes) if n in self.patterns), key=sort_key)
        tags = lambda node, prop: sorted((Tag(str(t), label(t)) for t in set(g.objects(node, prop))), key=sort_key)
        for node, p in self.patterns.items():
            p.counters = known(g.objects(node, PC.counters))
            p.interrupts = known(g.objects(node, PC.interrupts))
            for target in {*p.counters, *p.interrupts}:
                target.answered_by.append(p)
            broader = known(g.objects(node, SKOS.broader))
            p.cluster = next((b for b in broader if b.kind == 'cluster'), None)
            p.members = known(g.subjects(SKOS.broader, node))
            p.contexts = tags(node, PC.hasContext)
            p.functions = tags(node, PC.hasFunction)
            p.counter_tags = tags(node, PC.hasCounter)
        for node, p in self.patterns.items():
            p.answered_by.sort(key=sort_key)
            answering = {*p.answers, *p.answered_by}
            p.related = [r for r in known([*g.objects(node, PC.relatedPattern), *g.objects(node, SKOS.related)])
                         if r not in answering and r is not p]
        self._group()

    def label(self, node):
        value = self.graph.value(node, SKOS.prefLabel) or self.graph.value(node, RDFS.label)
        return str(value) if value else str(node).rsplit('/', 1)[-1].replace('-', ' ')

    def _group(self):
        c, root = self.content, self.root
        members = lambda key: sorted((self.patterns[m] for m in self.graph.objects(URIRef(f'{root}/collections/{key}'), SKOS.member)
                                      if m in self.patterns), key=sort_key)

        def place(group):
            for p in group['patterns']:
                p.groups.append(group)
                p.group = p.group or group
            return group

        self.sections = [place({'id': s['collection'].removeprefix('section-'), 'numeral': s['numeral'], 'blurb': s['blurb'],
                                'title': self.label(URIRef(f"{root}/collections/{s['collection']}")), 'patterns': members(s['collection'])})
                         for s in c['sections']]
        clusters = sorted((p for p in self.patterns.values() if p.kind == 'cluster'), key=sort_key)
        cluster_group = lambda cl: place({'id': cl.slug, 'title': cl.label, 'href': cl.href, 'cluster': cl,
                                          'blurb': c['clusters'].get(cl.slug, {}).get('blurb', ''), 'patterns': cl.members})
        self.anti_clusters = [cluster_group(cl) for cl in clusters if cl.members and cl.members[0].kind == 'anti']
        self.restorative_clusters = [cluster_group(cl) for cl in clusters if cl.members and cl.members[0].kind == 'restorative']
        unplaced = lambda kind: [p for p in self.patterns.values() if p.kind == kind and not p.groups]
        self.proposed = place({'id': 'proposed', 'title': c['proposed_title'], 'blurb': c['proposed'],
                              'patterns': sorted((p for k in ('anti', 'restorative') for p in unplaced(k) if p.provisional), key=sort_key)})
        self.restorative = place({'id': 'restorative-patterns', 'title': 'Restorative patterns', 'blurb': c['restorative_intro'],
                                  'patterns': sorted((p for p in unplaced('restorative')), key=sort_key)})
        leftover = unplaced('anti')
        if leftover:
            raise SystemExit('Anti-patterns in no section or cluster: ' + ', '.join(p.label for p in leftover))
        for cluster in clusters:
            cluster.group = next((g for g in self.anti_clusters + self.restorative_clusters if g['cluster'] is cluster), None)

    def overview(self):
        counts = {kind: sum(p.kind == kind for p in self.patterns.values()) for kind in ('anti', 'restorative')}
        version = self.graph.value(URIRef(self.root), OWL.versionInfo)
        return 'patterns/scheme.html', 'patterns-overview.html.j2', {
            'content': self.content, 'iri': str(self.scheme), 'counts': counts, 'sections': self.sections,
            'anti_clusters': self.anti_clusters, 'restorative_clusters': self.restorative_clusters,
            'restorative': self.restorative['patterns'], 'proposed': self.proposed['patterns'], 'version': str(version or ''),
        }

    def pattern_pages(self):
        for p in sorted(self.patterns.values(), key=sort_key):
            siblings = p.group['patterns'] if p.group else []
            i = siblings.index(p) if p in siblings else -1
            yield f'patterns/{p.slug}.html', 'pattern.html.j2', {
                'content': self.content, 'page': self.content['pattern_page'], 'iri': p.iri, 'p': p,
                'previous': siblings[i - 1] if i > 0 else None,
                'next': siblings[i + 1] if 0 <= i < len(siblings) - 1 else None,
            }


def write_pages(dataset, base, out: Path):
    env = environment()
    catalog = Catalog(dataset.graph(URIRef(base + 'patterns')))
    written = []
    for path, template, context in [catalog.overview(), *catalog.pattern_pages()]:
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(env.get_template(template).render(**context), encoding='utf-8')
        written.append(path)
    return written
