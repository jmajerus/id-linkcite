# id.linkcite.org

Persistent identifiers for terms (provisional until formal publication at version 1.0 or later). Each IRI under `https://id.linkcite.org/` resolves to the term's public description: an HTML page in a browser, or Turtle, JSON-LD or N-Triples by content negotiation. A read-only SPARQL endpoint answers at `/query`, with an editor at `/sparql/`.

The vocabularies themselves live in their own repositories, which stay the source of truth. This repository only collects a public view of each one and serves it. Vocabularies are grouped by the work they come from: the front page lists the works, and each work's page (such as `/dehumanization`) lists its vocabularies.

| Path | Vocabulary | Source |
|---|---|---|
| `/dehumanization/patterns` | Dehumanization Patterns | `dehumanization-patterns` (the ontology and its SKOS projection) |
| `/dehumanization/frameworks` | Dehumanization Frameworks | `dehumanization-frameworks` (committed `dehumanization-frameworks.ttl`; derived from the manuscript) |
| `/index` | Concept Index (draft, not yet published) | `terminology-mcp/tmp/concept-index` |

Until November 2026 the patterns and frameworks were at `/patterns` and `/frameworks`. Those addresses redirect permanently (`trifid/plugins/redirects.js`, configured in `trifid/config.yaml`), and `sql/002-dehumanization-namespace.sql` moved their editorial content.

## How it runs

On the LXC `id-linkcite`, behind the Cloudflare tunnel for `id.linkcite.org`:

- **Trifid** ([zazuko/trifid](https://github.com/zazuko/trifid)) listens on port 7878, the tunnel's target. It turns a request for an IRI into a query, picks the format, and renders HTML. It also proxies `/query`.
- **Oxigraph** runs read-only on `127.0.0.1:7879`, reachable only from Trifid. Nothing on the internet can write to it.

Trifid uses the official Node.js 22 runtime in `/opt/id-linkcite/runtime`, which the first deployment installs (checksum-verified). Both run as the unprivileged `id-linkcite` account, as the systemd units in `deploy/`. Each deployment loads a fresh store from `build/public.nq`; the previous two releases and stores are kept for rollback.

## Reader pages

Browsers get pages written for people, where one exists; tools asking for Turtle or JSON-LD still get data from the same IRI. `scripts/pages.py` writes them into `build/pages/` from the public data and the text in `site/content/`, using the Jinja2 templates in `site/templates/` and the stylesheet in `site/assets/`. `trifid/plugins/pages.js` serves them; any IRI without a page falls back to Trifid's generic view.

They cover both published vocabularies:

- **Patterns:** the overview (`/dehumanization/patterns/scheme`), a page for each pattern and cluster, and the tags patterns use (settings, what patterns do, what works against them), each with its patterns. Text in `site/content/patterns.toml`.
- **Frameworks:** the overview (`/dehumanization/frameworks/scheme`), arranged as the book orients them (DF0, the distortion and magnification planes, then DF5 and DF6), a page for each framework with its terms by role, and a page for each term. Text in `site/content/frameworks.toml`.

Edit the text files, then build and deploy. RDF definitions, descriptions, comments and scope notes are shown where available; optional per-IRI Markdown from PostgreSQL is inserted after canonical prose at request time. See [editorial instructions](site/content/EDITORIAL.md). Reader shells are generated for all supported public resource IRIs, including hash fragments.

## What is public

`scripts/build.py` filters each vocabulary's sources before anything leaves your machine:

- only resources under the vocabulary's own path, and no blank nodes;
- links to other IRIs (types, schemes, broader, related, tags), except `dct:source` and `pc:cites`;
- only these literals: labels, alternative labels, codes (`skos:notation`), slugs, statuses, keys and version info, plus definitions (`skos:definition`), scope notes, `dct:description` and `rdfs:comment`.

Everything else stays private, including source text, page locators and editorial notes. A new property is private until it is added to `PUBLIC_TEXT` in the script on purpose.

Definitions, scope notes and descriptions are now included by default; the old `--definitions` flag is accepted for compatibility but is unnecessary. The allowlist still excludes full manuscript passages, source locators and editorial notes. Published editorial Markdown is maintained separately in PostgreSQL; drafts, notes and history stay private. The reader service can select only the published Markdown view and renders sanitized HTML using a bounded cache. The editor role writes only via the controlled database transition function. Editing does not require a Git commit, rebuild or deployment. The editor, its API and an MCP server for agents (drafts only; a person publishes) answer on port 8000 like the authoring suite's servers: `http://id-linkcite.localdomain:8000/admin` on the LAN, `https://id-editorial.linkcite.org` through Cloudflare Access.

## Updating

`npm run build` does steps 1 and 2 in order; each also runs on its own.

1. `npm run build:sources` regenerates the vocabularies' projections in their own repositories (each entry in `vocabularies.json` says how). It runs the patterns projection; the frameworks are a committed release and need no step. A new vocabulary with a generated source adds its command here.
2. `npm run build:site` (`scripts/build.py`) writes `build/public.nq`, `build/manifest.json`, the home page and the reader pages, and prints what each vocabulary contributes.
3. `python3 tools/deploy.py` copies the release to the LXC and runs `deploy/install.sh` there. It switches only once the new release answers, and otherwise restores the previous one.

`npm run publish` (`tools/publish.py`) does all three, but only from a clean checkout of `main`, and only deploys if the build differs from the live release: site code, a published text, or a vocabulary's sources. It first checks that each published text is a readable PDF and warns if one has no licence notice in its first pages. Before deploying it lists what changed; the whole site is deployed, so changed vocabulary sources go live along with a new annex. `--dry-run` builds and compares without deploying; `--force` deploys anyway.

`python3 tools/deploy.py --inspect` shows the server's state without changing it. `python3 tools/deploy.py --env` copies the editorial services' settings (DSNs, `ACCESS_TEAM_DOMAIN`, …) from `.env` to the server's `/etc/linkcite/editorial.env` and restarts them; a normal deploy never copies `.env`. The host is `root@id-linkcite.localdomain` (SSH key), or `ID_LINKCITE_HOST`.

Set up once: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, and `npm install` to run it locally.

## Running locally

```sh
oxigraph load --location /tmp/id-store --file build/public.nq
oxigraph serve-read-only --location /tmp/id-store --bind 127.0.0.1:7879 --union-default-graph &
npm start
```

Then open <http://localhost:7878/dehumanization/patterns/scheme>. Trifid rewrites `https://id.linkcite.org/` to the local address, so links stay local.

## Published texts

A work's `texts` in `vocabularies.json` lists documents published under it: each one's `source` (a file built in the manuscript repository), the `file` name it is served under at `/<work>/text/`, a `title` and a `description`; `text_license` applies to all of them. `npm run build:site` copies them into `build/text/<work>/` and records each one's fingerprint, so a rebuilt PDF makes the build stale until the site is rebuilt. The work's page lists them under "Read the annexes". Annexes 1 and 2 are published under CC BY-NC 4.0; Trifid serves `build/text/dehumanization/` (`trifid/config.yaml`), and a second work would need its own entry there.

In the manuscript repository, the VS Code tasks "Build Annex 1 + publish" and "Build Annex 2 + publish" build that annex's PDF (the `annex1` or `annex2` profile, `pdf-screen`) and then run `npm run publish` here. A failed build stops before publishing. "Publish annexes to id.linkcite.org" runs the publish step alone. Building an annex without publishing ("Build Annex N PDF", or any other build task) leaves the live site as it is.

## Adding a vocabulary

Add an entry to `vocabularies.json`: its `path` (`<work>/<vocabulary>`, which must not already be in use), a `title`, its `home` IRI, its `sources`, and how to rebuild them. Every subject in its sources must already be under `https://id.linkcite.org/<path>`. A vocabulary for a new work also needs the work in `works`: its `path` (one segment), a short `label` for page headers, a `title` and a `description`. The work's page and its card on the front page are generated from these.

## Rules for IRIs

- IRIs in pre-1.0 ontologies are provisional and may be revised, consolidated or retired. Once an IRI is included in a formally published ontology release at version 1.0 or later, preserve that identifier permanently, even if its label changes or the resource is deprecated. Never put a version in an entity IRI.
- Terms take slash paths (`/dehumanization/patterns/sacralization-of-procedure`); classes and properties may use a `#` vocabulary document (`/dehumanization/patterns/vocab#AntiPattern`).
- `/dehumanization/text/` is reserved for the work's own text (chapters, annexes, appendices); no vocabulary may use it. Annexes 1 and 2 are published there as PDFs (see Published texts). It is an interim home: when the book has its own domain, each page there redirects permanently rather than being mirrored. Section anchors are keyed by code or slug, never page numbers, so term pages can link into the text.
- The published vocabulary includes its available definitions. Beyond that, only the texts listed under a work's `texts` are published; the rest of the manuscript stays private. Reader pages retain `noindex` until a separate decision is made about search-engine indexing.

## Licensing

- **Software** (the scripts, configuration and deployment files here): [PolyForm Noncommercial License 1.0.0](LICENSE.md).
- **Data** (the vocabulary descriptions published at `https://id.linkcite.org/`): [Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)](LICENSE-DATA.md).
