# id.linkcite.org

Permanent identifiers for terms. Each IRI under `https://id.linkcite.org/` resolves to the term's public description: an HTML page in a browser, or Turtle, JSON-LD or N-Triples by content negotiation. A read-only SPARQL endpoint answers at `/query`, with an editor at `/sparql/`.

The vocabularies themselves live in their own repositories, which stay the source of truth. This repository only collects a public view of each one and serves it.

| Path | Vocabulary | Source |
|---|---|---|
| `/patterns` | Dehumanization Patterns | `dehumanization-patterns` (the ontology and its SKOS projection) |
| `/frameworks` | Dehumanization Frameworks | `struggle-against-dehumanization` (`build/dehumanization-frameworks.ttl`) |
| `/index` | Concept Index (draft, not yet published) | `terminology-mcp/tmp/concept-index` |

## How it runs

On the LXC `id-linkcite`, behind the Cloudflare tunnel for `id.linkcite.org`:

- **Trifid** ([zazuko/trifid](https://github.com/zazuko/trifid)) listens on port 7878, the tunnel's target. It turns a request for an IRI into a query, picks the format, and renders HTML. It also proxies `/query`.
- **Oxigraph** runs read-only on `127.0.0.1:7879`, reachable only from Trifid. Nothing on the internet can write to it.

Trifid uses the official Node.js 22 runtime in `/opt/id-linkcite/runtime`, which the first deployment installs (checksum-verified). Both run as the unprivileged `id-linkcite` account, as the systemd units in `deploy/`. Each deployment loads a fresh store from `build/public.nq`; the previous two releases and stores are kept for rollback.

## What is public

`scripts/build.py` filters each vocabulary's sources before anything leaves your machine:

- only resources under the vocabulary's own path, and no blank nodes;
- links to other IRIs (types, schemes, broader, related, tags), except `dct:source` and `pc:cites`;
- only these literals: labels, alternative labels, codes (`skos:notation`), slugs, statuses, keys and version info.

Everything else stays private, including source text, page locators and editorial notes. A new property is private until it is added to `PUBLIC_TEXT` in the script on purpose.

Definitions, scope notes and descriptions are left out until the book is published. `python3 scripts/build.py --definitions` includes them.

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

- Never change an IRI once it's minted, and never put a version in one. Rename labels freely.
- Terms take slash paths (`/patterns/sacralization-of-procedure`); classes and properties may use a `#` vocabulary document (`/patterns/vocab#AntiPattern`).
- Before the book is published, serve labels and links only.
