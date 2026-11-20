"""Reader-facing HTML pages, written into build/pages/ from the public dataset and site/content/.

Pages are generated only from what build.py has already filtered as public, plus the authored text in
site/content/. The pages plugin (trifid/plugins/pages.js) serves them to browsers; tools asking for RDF
still get Trifid's own responses.
"""
import re
import json
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SKOS

from editorial import resource_text, reader_resources, generic_context, editorial_marker

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


@dataclass(eq=False)
class Tag:
    iri: str
    label: str

    @property
    def href(self):
        return path_of(self.iri)

    @property
    def name(self):
        """Preserve explicitly capitalized labels; humanize legacy lower-case tag labels."""
        if self.label[:1].isupper():
            return self.label
        text = self.label.replace('-', ' ')
        return text[:1].upper() + text[1:]


def natural(code):
    """A sort key that puts DF1-2 before DF1-10."""
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', code)]


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
    env = Environment(loader=FileSystemLoader(ROOT / 'site/templates'), autoescape=select_autoescape(enabled_extensions=('html', 'htm', 'xml', 'j2')),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters['md'] = md
    env.filters['mdinline'] = lambda text: md(text, inline=True)
    env.filters['editorial_marker'] = editorial_marker
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
        self.tagged = defaultdict(list)  # tag IRI -> patterns
        for p in self.patterns.values():
            for tag in (*p.contexts, *p.functions, *p.counter_tags):
                self.tagged[tag.iri].append(p)
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


    def tag_pages(self):
        """A page for each tag vocabulary that patterns use, and for each of its tags."""
        g = self.graph
        for key, text in self.content['tags'].items():
            scheme = URIRef(f'{self.root}/tags/{key}')
            tags = {node: Tag(str(node), self.label(node)) for node in g.subjects(SKOS.inScheme, scheme)}
            broader = {t: tags.get(g.value(node, SKOS.broader)) for node, t in tags.items()}
            narrower = defaultdict(list)
            for t, parent in broader.items():
                if parent:
                    narrower[parent].append(t)
            count = lambda t: len(self.tagged.get(t.iri, []))
            ordered = lambda items: sorted(items, key=lambda t: t.name.lower())
            vocabulary = {'key': key, 'iri': str(scheme), 'href': path_of(scheme), **text}
            top = ordered(t for t in tags.values() if not broader[t])
            yield f'patterns/tags/{key}.html', 'tag-vocabulary.html.j2', {
                'content': self.content, 'iri': str(scheme), 'vocabulary': vocabulary, 'count': count,
                'top': top, 'narrower': {t: ordered(n) for t, n in narrower.items()},
                'hierarchical': bool(narrower), 'used': sum(1 for t in tags.values() if count(t)), 'total': len(tags),
                'in_use': ordered(t for t in tags.values() if count(t)),
            }
            for t in tags.values():
                patterns = sorted(self.tagged.get(t.iri, []), key=sort_key)
                # Other tags of this vocabulary on the same patterns, most frequent first.
                vocab_tags = {x.iri: x for x in tags.values()}
                together = Counter(tag.iri for p in patterns for tag in (*p.contexts, *p.functions, *p.counter_tags)
                                   if tag.iri != t.iri and tag.iri in vocab_tags)
                with_it = [(vocab_tags[i], n) for i, n in sorted(together.items(), key=lambda item: (-item[1], vocab_tags[item[0]].name))][:10]
                yield f'patterns/tags/{key}/{t.href.rsplit("/", 1)[1]}.html', 'tag.html.j2', {
                    'content': self.content, 'iri': t.iri, 'tag': t, 'vocabulary': vocabulary,
                    'anti': [p for p in patterns if p.kind == 'anti'], 'restorative': [p for p in patterns if p.kind == 'restorative'],
                    'broader': broader[t], 'narrower': ordered(narrower.get(t, [])), 'with_it': with_it, 'count': len(patterns),
                }


@dataclass(eq=False)
class Term:
    iri: str
    label: str
    code: str
    alt: list
    role: str = 'other'
    parent: 'Term | None' = None
    children: list = field(default_factory=list)
    related: list = field(default_factory=list)

    @property
    def href(self):
        return path_of(self.iri)

    @property
    def framework(self):
        term = self
        while term.parent:
            term = term.parent
        return term


class Frameworks:
    """The frameworks scheme as readers see it: DF0–DF6, their terms by role, and the planes."""

    ROLES = ['mechanisms', 'stabilization', 'signals', 'responses', 'other']

    def __init__(self, graph: Graph):
        self.graph = graph
        self.content = tomllib.loads((ROOT / 'site/content/frameworks.toml').read_text(encoding='utf-8'))
        self.root = SITE + '/frameworks'
        self.scheme = URIRef(self.root + '/scheme')
        g = graph
        self.terms = {}
        for node in set(g.subjects(SKOS.inScheme, self.scheme)):
            code = str(g.value(node, SKOS.notation) or '')
            self.terms[node] = Term(str(node), str(g.value(node, SKOS.prefLabel) or node), code,
                                    sorted(str(a) for a in g.objects(node, SKOS.altLabel)))
        by_code = lambda items: sorted(items, key=lambda t: natural(t.code))
        for key in self.ROLES[:-1]:
            for member in g.objects(URIRef(f'{self.root}/collections/{key}'), SKOS.member):
                if member in self.terms:
                    self.terms[member].role = key
        for node, term in self.terms.items():
            term.parent = self.terms.get(g.value(node, SKOS.broader))
            term.related = by_code({self.terms[r] for r in [*g.objects(node, SKOS.related), *g.subjects(SKOS.related, node)] if r in self.terms})
        for term in self.terms.values():
            if term.parent:
                term.parent.children.append(term)
        for term in self.terms.values():
            term.children = by_code(term.children)
        self.frameworks = by_code(t for t in self.terms.values() if not t.parent)
        self.by_code = {t.code: t for t in self.terms.values()}

    def roles_of(self, term):
        """A term's children, grouped by role in the book's order."""
        groups = []
        for key in self.ROLES:
            members = [c for c in term.children if c.role == key]
            if members:
                groups.append({'key': key, 'title': self.content['roles'][key], 'blurb': self.content['role_blurbs'][key], 'terms': members})
        return groups

    def descendants(self, term):
        for child in term.children:
            yield child
            yield from self.descendants(child)

    def overview(self):
        c = self.content
        cards = lambda codes: [{'term': self.by_code[code], 'focus': c['frameworks'].get(code, {}).get('focus', ''),
                                'counts': [(c['roles'][k], n) for k in self.ROLES[:-1]
                                           if (n := sum(1 for d in self.descendants(self.by_code[code]) if d.role == k))]}
                               for code in codes if code in self.by_code]
        groups = [{**group, 'cards': cards(group['frameworks'])} for group in c['groups']]
        listed = {code for group in c['groups'] for code in group['frameworks']}
        missing = [t.code for t in self.frameworks if t.code not in listed]
        if missing:
            raise SystemExit('Frameworks missing from site/content/frameworks.toml groups: ' + ', '.join(missing))
        return 'frameworks/scheme.html', 'frameworks-overview.html.j2', {
            'content': c, 'iri': str(self.scheme), 'groups': groups, 'total': len(self.terms) - len(self.frameworks),
        }

    def pages(self):
        c = self.content
        group_of = {code: group for group in c['groups'] for code in group['frameworks']}
        for i, framework in enumerate(self.frameworks):
            yield f'frameworks/{framework.href.rsplit("/", 1)[1]}.html', 'framework.html.j2', {
                'content': c, 'iri': framework.iri, 't': framework, 'roles': self.roles_of(framework),
                'focus': c['frameworks'].get(framework.code, {}).get('focus', ''), 'group': group_of.get(framework.code),
                'previous': self.frameworks[i - 1] if i > 0 else None,
                'next': self.frameworks[i + 1] if i < len(self.frameworks) - 1 else None,
            }
        for term in self.terms.values():
            if not term.parent:
                continue
            siblings = [s for s in term.parent.children if s.role == term.role]
            i = siblings.index(term)
            yield f'frameworks/{term.href.rsplit("/", 1)[1]}.html', 'framework-term.html.j2', {
                'content': c, 'iri': term.iri, 't': term, 'role': c['roles'][term.role], 'role_blurb': c['role_blurbs'][term.role],
                'roles': self.roles_of(term), 'siblings': siblings,
                'previous': siblings[i - 1] if i > 0 else None, 'next': siblings[i + 1] if i < len(siblings) - 1 else None,
            }


def write_pages(dataset, base, out: Path):
    env = environment()
    catalog = Catalog(dataset.graph(URIRef(base + 'patterns')))
    frameworks = Frameworks(dataset.graph(URIRef(base + 'frameworks')))
    # Render from the filtered public dataset only, never the private source graphs.
    public = Graph()
    for s, p, o, _ in dataset.quads((None, None, None, None)):
        public.add((s, p, o))
    resources = reader_resources(public, base)
    out.parent.mkdir(parents=True, exist_ok=True)
    (out.parent / 'editorial-resources.json').write_text(json.dumps(resources, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    pages = [catalog.overview(), *catalog.pattern_pages(), *catalog.tag_pages(),
             frameworks.overview(), *frameworks.pages()]
    known = {context['iri'] for _, _, context in pages}
    # Prebuild a reader shell for every supported public document IRI.
    docs = {iri.split('#', 1)[0] for iri in resources}
    for iri in sorted(docs - known):
        path = iri[len(base):] + '.html'
        pages.append((path, 'resource.html.j2', generic_context(public, iri)))
    written = []
    for path, template, context in pages:
        iri = context['iri']
        context.update(resource_text(public, iri))
        context['editorial_iri'] = iri
        context['fragment_sections'] = [
            {'anchor': key.split('#', 1)[1],
             **generic_context(public, key), **resource_text(public, key), 'editorial_iri': key}
            for key in sorted(resources) if key.startswith(iri + '#')
        ]
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(env.get_template(template).render(**context), encoding='utf-8')
        written.append(path)
    # A removed snippet must not leave an obsolete generated page in the next release.
    generated = set(written)
    for stale in out.rglob('*.html'):
        if stale.relative_to(out).as_posix() not in generated:
            stale.unlink()
    return written
