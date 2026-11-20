# id.linkcite.org

Persistent identifiers for terms (provisional until formal publication at version 1.0 or later). Each IRI under `https://id.linkcite.org/` resolves to the term's public description: an HTML page in a browser, or Turtle, JSON-LD or N-Triples by content negotiation. A read-only SPARQL endpoint answers at `/query`, with an editor at `/sparql/`.

The vocabularies themselves live in their own repositories, which stay the source of truth. This repository only collects a public view of each one and serves it.

| Path | Vocabulary | Source |
|---|---|---|
| `/patterns` | Dehumanization Patterns | `dehumanization-patterns` (the ontology and its SKOS projection) |
| `/frameworks` | Dehumanization Frameworks | `dehumanization-frameworks` (committed `dehumanization-frameworks.ttl`; derived from the manuscript) |
| `/index` | Concept Index (draft, not yet published) | `terminology-mcp/tmp/concept-index` |

## How it runs

On the LXC `id-linkcite`, behind the Cloudflare tunnel for `id.linkcite.org`:

- **Trifid** ([zazuko/trifid](https://github.com/zazuko/trifid)) listens on port 7878, the tunnel's target. It turns a request for an IRI into a query, picks the format, and renders HTML. It also proxies `/query`.
- **Oxigraph** runs read-only on `127.0.0.1:7879`, reachable only from Trifid. Nothing on the internet can write to it.

Trifid uses the official Node.js 22 runtime in `/opt/id-linkcite/runtime`, which the first deployment installs (checksum-verified). Both run as the unprivileged `id-linkcite` account, as the systemd units in `deploy/`. Each deployment loads a fresh store from `build/public.nq`; the previous two releases and stores are kept for rollback.

## Reader pages

Browsers get pages written for people, where one exists; tools asking for Turtle or JSON-LD still get data from the same IRI. `scripts/pages.py` writes them into `build/pages/` from the public data and the text in `site/content/`, using the Jinja2 templates in `site/templates/` and the stylesheet in `site/assets/`. `trifid/plugins/pages.js` serves them; any IRI without a page falls back to Trifid's generic view.

They cover both published vocabularies:

- **Patterns:** the overview (`/patterns/scheme`), a page for each pattern and cluster, and the tags patterns use (settings, what patterns do, what works against them), each with its patterns. Text in `site/content/patterns.toml`.
- **Frameworks:** the overview (`/frameworks/scheme`), arranged as the book orients them (DF0, the distortion and magnification planes, then DF5 and DF6), a page for each framework with its terms by role, and a page for each term. Text in `site/content/frameworks.toml`.

Edit the text files, then build and deploy. RDF definitions, descriptions, comments and scope notes are shown where available; optional per-IRI Markdown from PostgreSQL is inserted after canonical prose at request time. See [editorial instructions](site/content/EDITORIAL.md). Reader shells are generated for all supported public resource IRIs, including hash fragments.

## What is public

`scripts/build.py` filters each vocabulary's sources before anything leaves your machine:

- only resources under the vocabulary's own path, and no blank nodes;
- links to other IRIs (types, schemes, broader, related, tags), except `dct:source` and `pc:cites`;
- only these literals: labels, alternative labels, codes (`skos:notation`), slugs, statuses, keys and version info, plus definitions (`skos:definition`), scope notes, `dct:description` and `rdfs:comment`.

Everything else stays private, including source text, page locators and editorial notes. A new property is private until it is added to `PUBLIC_TEXT` in the script on purpose.

Definitions, scope notes and descriptions are now included by default; the old `--definitions` flag is accepted for compatibility but is unnecessary. The allowlist still excludes full manuscript passages, source locators and editorial notes. Published editorial Markdown is maintained separately in PostgreSQL; drafts, notes and history stay private. The reader service can select only the published HTML view. Editing does not require a Git commit, rebuild or deployment.

## Updating

1. Regenerate a vocabulary's projection in its own repository, if it changed (each entry in `vocabularies.json` says how).
2. `python3 scripts/build.py` writes `build/public.nq`, `build/manifest.json` and the home page, and prints what each vocabulary contributes.
3. `python3 tools/deploy.py` copies the release to the LXC and runs `deploy/install.sh` there. It switches only once the new release answers, and otherwise restores the previous one.

`python3 tools/deploy.py --inspect` shows the server's state without changing it. The host is `root@id-linkcite.localdomain` (SSH key), or `ID_LINKCITE_HOST`.

Set up once: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, and `npm install` to run it locally.

## Running locally

```sh
oxigraph load --location /tmp/id-store --file build/public.nq
oxigraph serve-read-only --location /tmp/id-store --bind 127.0.0.1:7879 --union-default-graph &
npm start
```

Then open <http://localhost:7878/patterns/scheme>. Trifid rewrites `https://id.linkcite.org/` to the local address, so links stay local.

## Adding a vocabulary

Add an entry to `vocabularies.json`: its `path` (the first segment under the root, which must not already be in use), a `title`, its `home` IRI, its `sources`, and how to rebuild them. Every subject in its sources must already be under `https://id.linkcite.org/<path>`.

## Rules for IRIs

- IRIs in pre-1.0 ontologies are provisional and may be revised, consolidated or retired. Once an IRI is included in a formally published ontology release at version 1.0 or later, preserve that identifier permanently, even if its label changes or the resource is deprecated. Never put a version in an entity IRI.
- Terms take slash paths (`/patterns/sacralization-of-procedure`); classes and properties may use a `#` vocabulary document (`/patterns/vocab#AntiPattern`).
- The published vocabulary includes its available definitions; full manuscript text stays private. Reader pages retain `noindex` until a separate decision is made about search-engine indexing.

## Licensing

- **Software** (the scripts, configuration and deployment files here): [PolyForm Noncommercial License 1.0.0](LICENSE.md).
- **Data** (the vocabulary descriptions published at `https://id.linkcite.org/`): [Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)](LICENSE-DATA.md).
